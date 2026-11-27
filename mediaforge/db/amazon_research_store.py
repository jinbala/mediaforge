import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError

from mediaforge.db.engine import get_session
from mediaforge.db.tables import AmazonResearchRunTable as Run
from mediaforge.models.amazon_research import AmazonResearchReport
from mediaforge.models.amazon_research_run import (
    AmazonResearchRun,
    AmazonResearchRunRequest,
    AmazonResearchRunSummary,
    AmazonResearchSnapshot,
    RetryTarget,
)
from mediaforge.rag.amazon_store_collection import page_id

LEASE = timedelta(seconds=120)
QUEUE_WAIT = timedelta(minutes=5)
ACTIVE = ("queued", "running")
TERMINAL = ("done", "partial_fail", "failed", "queue_failed")


class ResearchRunConflict(ValueError):
    pass


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def source_key(mode: str, url: str) -> str:
    return f"amazon:US:store:{page_id(url)}" if mode == "store" else f"amazon:US:{mode}:{url}"


def _fingerprint(inputs: AmazonResearchRunRequest) -> str:
    payload = inputs.model_dump(mode="json", exclude={"source", "submission_id"})
    payload["source"] = source_key(inputs.mode, inputs.source)
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _owner(owner: tuple[UUID, UUID]):
    return and_(Run.tenant_id == owner[0], Run.user_id == owner[1])


def _summary(row: Run) -> AmazonResearchRunSummary:
    status = row.status
    if status in ACTIVE and _utc(row.lease_until) <= datetime.now(UTC):
        status = "interrupted"
    return AmazonResearchRunSummary(
        id=row.id,
        source=row.source,
        mode=row.mode,
        status=status,
        stage=row.stage,
        title=row.title,
        canonical_source=row.canonical_source,
        attempt=row.attempt,
        created_at=_utc(row.created_at),
        updated_at=_utc(row.updated_at),
    )


def _view(row: Run) -> AmazonResearchRun:
    data = _summary(row)
    return AmazonResearchRun(
        **data.model_dump(),
        inputs=AmazonResearchRunRequest.model_validate(row.inputs),
        error="队列或工作进程停止更新，可从已保存进度恢复。"
        if data.status == "interrupted"
        else row.error,
        report=AmazonResearchReport.model_validate(row.report) if row.report else None,
        snapshot=AmazonResearchSnapshot.model_validate(row.snapshot),
    )


async def submit_run(
    owner: tuple[UUID, UUID], inputs: AmazonResearchRunRequest
) -> tuple[AmazonResearchRun, bool]:
    active_key = _fingerprint(inputs)
    now = datetime.now(UTC)
    async with get_session() as session:
        candidate = Run(
            tenant_id=owner[0],
            user_id=owner[1],
            mode=inputs.mode,
            source=inputs.source,
            canonical_source=source_key(inputs.mode, inputs.source),
            active_key=active_key,
            submission_id=inputs.submission_id,
            status="queued",
            stage="queue",
            lease_until=now + QUEUE_WAIT,
            inputs=inputs.model_dump(mode="json"),
            snapshot=AmazonResearchSnapshot().model_dump(mode="json"),
        )
        if inputs.submission_id:
            previous = await session.scalar(
                select(Run).where(_owner(owner), Run.submission_id == inputs.submission_id)
            )
            if previous:
                if (
                    _fingerprint(AmazonResearchRunRequest.model_validate(previous.inputs))
                    != active_key
                ):
                    raise ResearchRunConflict("提交标识已用于其他调研参数。")
                return _view(previous), False
        # 唯一索引负责跨 Gateway 进程并发去重，完成后释放 active_key，后续可另建历史快照。
        try:
            async with session.begin_nested():
                session.add(candidate)
                await session.flush()
            return _view(candidate), True
        except IntegrityError:
            criteria = Run.active_key == active_key
            if inputs.submission_id:
                criteria = or_(criteria, Run.submission_id == inputs.submission_id)
            previous = await session.scalar(
                select(Run).where(_owner(owner), criteria).order_by(Run.created_at.desc()).limit(1)
            )
            if previous is None:
                raise
            if _fingerprint(AmazonResearchRunRequest.model_validate(previous.inputs)) != active_key:
                raise ResearchRunConflict("提交标识已用于其他调研参数。") from None
            return _view(previous), False


async def list_runs(
    owner: tuple[UUID, UUID], before: datetime | None = None
) -> list[AmazonResearchRunSummary]:
    query = select(Run).where(_owner(owner))
    if before:
        query = query.where(Run.created_at < before)
    async with get_session() as session:
        rows = await session.scalars(query.order_by(Run.created_at.desc(), Run.id).limit(30))
        return [_summary(row) for row in rows]


async def get_run(owner: tuple[UUID, UUID], run_id: UUID) -> AmazonResearchRun | None:
    async with get_session() as session:
        row = await session.scalar(select(Run).where(_owner(owner), Run.id == run_id))
        return _view(row) if row else None


async def retry_run(
    owner: tuple[UUID, UUID],
    run_id: UUID,
    target: RetryTarget,
    item_key: str | None = None,
) -> tuple[AmazonResearchRun | None, bool]:
    now = datetime.now(UTC)
    try:
        async with get_session() as session:
            row = await session.scalar(
                select(Run).where(_owner(owner), Run.id == run_id).with_for_update()
            )
            if row is None:
                return None, False
            run = _view(row)
            if run.status in ACTIVE:
                return run, False
            if run.attempt >= 6:
                raise ResearchRunConflict("此任务已达到重试上限，可以从表单创建新的调研。")
            retryable = [
                item
                for item in run.snapshot.evidence
                if item.status in {"pending", "failed", "incomplete"}
                and item.code not in {"sample_limit", "duplicate", "store_mismatch"}
                and (item_key is None or item.key == item_key)
            ]
            if target == "resume" and run.status not in {"queue_failed", "interrupted"}:
                raise ResearchRunConflict("当前任务没有中断或派发失败。")
            if target == "failed":
                if not retryable:
                    raise ResearchRunConflict("当前没有可重试的采集栏目。")
                for item in retryable:
                    item.status = "pending"
            inputs = run.inputs
            # 重试沿用原调研 ID 及成功样本，增加尝试版本，延迟抵达的旧消息不能覆盖新状态。
            row.active_key = _fingerprint(inputs)
            row.attempt += 1
            row.status, row.stage = "queued", f"retry-{target}"
            row.error, row.lease_token = None, None
            row.lease_until, row.updated_at = now + QUEUE_WAIT, now
            row.snapshot = run.snapshot.model_dump(mode="json")
            await session.flush()
            return _view(row), True
    except IntegrityError as exc:
        raise ResearchRunConflict("同参数调研正在运行，请从历史记录查看。") from exc


async def record_queue_failure(run_id: UUID, attempt: int) -> None:
    async with get_session() as session:
        await session.execute(
            update(Run)
            .where(Run.id == run_id, Run.attempt == attempt, Run.status == "queued")
            .values(
                status="queue_failed",
                active_key=None,
                updated_at=datetime.now(UTC),
                error="调研任务未能进入队列，可恢复本任务。",
            )
        )


async def claim_run(
    run_id: UUID, attempt: int
) -> tuple[AmazonResearchRun | None, UUID | None, bool]:
    now, token = datetime.now(UTC), uuid4()
    async with get_session() as session:
        # 有效租约内只有一个 worker 可以更新。崩溃后的重投等到租约到期再继续。
        row = (
            await session.execute(
                update(Run)
                .where(
                    Run.id == run_id,
                    Run.attempt == attempt,
                    or_(
                        Run.status == "queued",
                        and_(Run.status == "running", Run.lease_until <= now),
                    ),
                )
                .values(
                    status="running", lease_token=token, lease_until=now + LEASE, updated_at=now
                )
                .returning(Run)
            )
        ).scalar_one_or_none()
        if row:
            return _view(row), token, False
        running = await session.scalar(
            select(Run.id).where(
                Run.id == run_id,
                Run.attempt == attempt,
                Run.status == "running",
            )
        )
        return None, None, running is not None


async def checkpoint(
    run: AmazonResearchRun,
    token: UUID,
    stage: str,
    *,
    status: str = "running",
    error: str | None = None,
) -> None:
    now = datetime.now(UTC)
    data = dict(
        stage=stage,
        status=status,
        updated_at=now,
        lease_until=now + LEASE,
        error=error,
        snapshot=run.snapshot.model_dump(mode="json"),
        canonical_source=source_key(run.mode, run.snapshot.store_root or run.source),
    )
    if run.report:
        data["report"] = run.report.model_dump(mode="json")
        data["title"] = run.report.title_zh or run.report.title
    if status in TERMINAL:
        data.update(active_key=None, lease_token=None, completed_at=now)
    async with get_session() as session:
        result = await session.execute(
            update(Run)
            .where(
                Run.id == run.id,
                Run.attempt == run.attempt,
                Run.lease_token == token,
                Run.status == "running",
            )
            .values(**data)
        )
        if not result.rowcount:
            raise ResearchRunConflict("任务已由其他工作进程接管。")
