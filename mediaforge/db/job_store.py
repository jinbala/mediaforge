import json
import uuid
from datetime import datetime, timezone

from loguru import logger
from redis.asyncio import Redis
from sqlalchemy import case, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from mediaforge.db.tables import AssetStatus, AssetTable, JobItemTable, JobTable
from mediaforge.models.job import BatchSubmitPayload, SkuInput


def _as_uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(value)


def current_asset_results(assets: list[dict]) -> list[dict]:
    """Pick the newest result per output, keeping a successful attempt if there is one."""
    results = {}
    for asset in sorted(assets, key=lambda row: (row["created_at"], str(row["asset_id"]))):
        if asset["status"] not in (AssetStatus.success, AssetStatus.failed):
            continue
        key = (asset["sku_id"], asset["output_type"], asset["platform"])
        previous = results.get(key)
        if previous is None or previous["status"] != AssetStatus.success or asset["status"] == AssetStatus.success:
            results[key] = asset
    return list(results.values())


class JobStore:
    def __init__(self, engine: AsyncEngine, redis: Redis):
        self.engine = engine
        self.redis = redis

    async def create_job(self, tenant_id: str, payload: BatchSubmitPayload) -> str:
        job_id = uuid.uuid4()
        async with self.engine.begin() as conn:
            await conn.execute(
                JobTable.__table__.insert().values(
                    job_id=job_id,
                    tenant_id=_as_uuid(tenant_id),
                    status="pending",
                    total_skus=payload.total_skus,
                    input_data=payload.model_dump(mode="json"),
                    created_at=datetime.now(timezone.utc),
                )
            )
            await conn.execute(
                JobItemTable.__table__.insert(),
                [
                    {
                        "job_id": job_id,
                        "sku_id": sku.sku_id,
                        "status": "pending",
                        "updated_at": datetime.now(timezone.utc),
                    }
                    for sku in payload.skus
                ],
            )
        return str(job_id)

    async def create_job_idempotent(
        self,
        tenant_id: str,
        payload: BatchSubmitPayload,
        *,
        idempotency_key: str,
        created_by: str | None = None,
        source: str = "agent",
    ) -> tuple[str, bool]:
        """Create one job once; return (job_id, was_created)."""
        tenant_uuid = _as_uuid(tenant_id)
        job_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        async with self.engine.begin() as conn:
            inserted = (
                await conn.execute(
                    pg_insert(JobTable.__table__)
                    .values(
                        job_id=job_id,
                        tenant_id=tenant_uuid,
                        created_by=_as_uuid(created_by) if created_by else None,
                        source=source,
                        idempotency_key=idempotency_key,
                        status="pending",
                        total_skus=payload.total_skus,
                        input_data=payload.model_dump(mode="json"),
                        created_at=now,
                    )
                    .on_conflict_do_nothing(
                        index_elements=["tenant_id", "idempotency_key"],
                        index_where=JobTable.__table__.c.idempotency_key.isnot(None),
                    )
                    .returning(JobTable.__table__.c.job_id)
                )
            ).scalar_one_or_none()
            if inserted is None:
                existing = (
                    await conn.execute(
                        select(JobTable.__table__.c.job_id).where(
                            JobTable.__table__.c.tenant_id == tenant_uuid,
                            JobTable.__table__.c.idempotency_key == idempotency_key,
                        )
                    )
                ).scalar_one()
                return str(existing), False
            await conn.execute(
                JobItemTable.__table__.insert(),
                [
                    {
                        "job_id": job_id,
                        "sku_id": sku.sku_id,
                        "status": "pending",
                        "updated_at": now,
                    }
                    for sku in payload.skus
                ],
            )
        return str(job_id), True

    async def get_job_by_idempotency(self, tenant_id: str, idempotency_key: str) -> dict | None:
        async with self.engine.connect() as conn:
            row = (
                await conn.execute(
                    select(JobTable.__table__).where(
                        JobTable.__table__.c.tenant_id == _as_uuid(tenant_id),
                        JobTable.__table__.c.idempotency_key == idempotency_key,
                    )
                )
            ).mappings().first()
            return dict(row) if row else None

    async def get_job(self, job_id: str) -> dict:
        async with self.engine.connect() as conn:
            result = await conn.execute(
                select(JobTable.__table__).where(JobTable.__table__.c.job_id == _as_uuid(job_id))
            )
            row = result.mappings().first()
            if row is None:
                raise ValueError(f"Job {job_id} not found")
            return dict(row)

    async def list_jobs_for_tenant(
        self, tenant_id: str, limit: int = 50, *, include_input_data: bool = False
    ) -> list[dict]:
        table = JobTable.__table__
        columns = [
            table.c.job_id, table.c.status, table.c.total_skus, table.c.done_skus, table.c.created_at,
        ]
        if include_input_data:
            columns.append(table.c.input_data)
        async with self.engine.connect() as conn:
            result = await conn.execute(
                select(*columns)
                .where(table.c.tenant_id == _as_uuid(tenant_id))
                .order_by(table.c.created_at.desc())
                .limit(limit)
            )
            return [dict(row) for row in result.mappings().all()]

    async def start_job(self, job_id: str) -> None:
        async with self.engine.connect() as conn:
            await conn.execute(
                update(JobTable.__table__)
                .where(JobTable.__table__.c.job_id == _as_uuid(job_id))
                .values(status="running", started_at=datetime.now(timezone.utc))
            )
            await conn.commit()

    async def start_job_item(self, job_id: str, sku_id: str, *, repair_id: str | None = None) -> bool:
        """Mark one SKU running. Terminal items are idempotently skipped."""
        table = JobItemTable.__table__
        statuses = ("pending", "running", "retrying")
        if repair_id:
            statuses = ("repair_pending", "repair_running", "repair_retrying")
        statement = update(table).where(
            table.c.job_id == _as_uuid(job_id),
            table.c.sku_id == sku_id,
            table.c.status.in_(statuses),
        )
        if repair_id:
            statement = statement.where(table.c.generation_context["manual_repair_id"].as_string() == repair_id)
        async with self.engine.begin() as conn:
            result = await conn.execute(
                statement.values(
                    status="repair_running" if repair_id else "running",
                    error_msg=None,
                    updated_at=datetime.now(timezone.utc),
                )
            )
        return bool(result.rowcount)

    async def reserve_image_repair(
        self, job_id: str, tenant_id: str, asset_ids: set[uuid.UUID], skus: dict[str, SkuInput], repair_id: str
    ) -> dict[str, list[dict]]:
        job = JobTable.__table__
        item = JobItemTable.__table__
        asset = AssetTable.__table__
        job_uuid = _as_uuid(job_id)
        now = datetime.now(timezone.utc)
        async with self.engine.begin() as conn:
            original = (
                await conn.execute(select(job).where(
                    job.c.job_id == job_uuid, job.c.tenant_id == _as_uuid(tenant_id),
                ).with_for_update())
            ).mappings().one()
            if original["status"] not in ("partial_fail", "failed"):
                raise ValueError("任务状态已更新，请刷新页面后查看")
            legacy_running = (await conn.execute(select(job.c.job_id).where(
                job.c.tenant_id == original["tenant_id"],
                job.c.source == "retry",
                job.c.idempotency_key == f"retry:{job_id}",
                job.c.status.in_(("pending", "running")),
            ))).scalar_one_or_none()
            if legacy_running:
                raise ValueError("已有重试正在运行，请先查看重试进度")
            latest = current_asset_results(list((await conn.execute(
                select(asset).where(asset.c.job_id == job_uuid)
            )).mappings()))
            selected = [
                row for row in latest
                if row["status"] == AssetStatus.failed
                and row["asset_id"] in asset_ids
                and row["sku_id"] in skus
                and row["platform"]
                and row["output_type"] in skus[row["sku_id"]].output_types
                and row["platform"].split(":", 1)[0] in skus[row["sku_id"]].target_platforms
            ]
            if len(selected) != len(asset_ids):
                raise ValueError("所选图片状态已更新，请刷新后重新选择")
            by_sku: dict[str, list[dict]] = {}
            for row in selected:
                by_sku.setdefault(row["sku_id"], []).append(row)
            items = (await conn.execute(select(item).where(
                item.c.job_id == job_uuid,
                item.c.sku_id.in_(by_sku),
                item.c.status.in_(("partial_fail", "failed")),
            ))).mappings().all()
            if len(items) != len(by_sku):
                raise ValueError("所选图片所属商品状态已更新，请刷新后重试")
            for row in items:
                await conn.execute(update(item).where(
                    item.c.job_id == job_uuid, item.c.sku_id == row["sku_id"],
                ).values(
                    status="repair_pending",
                    generation_context={**(row["generation_context"] or {}), "manual_repair_id": repair_id},
                    updated_at=now,
                ))
            await conn.execute(update(job).where(job.c.job_id == job_uuid).values(
                status="running", done_skus=original["done_skus"] - len(items), finished_at=None,
            ))
        return by_sku

    async def fail_unqueued_image_repair(self, job_id: str, sku_id: str, repair_id: str) -> None:
        item = JobItemTable.__table__
        async with self.engine.begin() as conn:
            result = await conn.execute(update(item).where(
                item.c.job_id == _as_uuid(job_id),
                item.c.sku_id == sku_id,
                item.c.status == "repair_pending",
                item.c.generation_context["manual_repair_id"].as_string() == repair_id,
            ).values(
                status=case((item.c.completed_assets > 0, "partial_fail"), else_="failed"),
                error_msg="补生成队列暂时不可用，请重试",
                updated_at=datetime.now(timezone.utc),
            ))
        if result.rowcount:
            await self.refresh_job_progress(job_id)

    async def complete_image_repair(self, job_id: str, sku_id: str, *, error_msg: str | None = None) -> dict:
        results = current_asset_results(await self.get_assets_for_job(job_id, sku_id=sku_id))
        failed = [row for row in results if row["status"] == AssetStatus.failed]
        return await self.complete_job_item(
            job_id,
            sku_id,
            completed_assets=len(results) - len(failed),
            failed_assets=len(failed),
            error_msg=(error_msg or failed[0]["error_msg"]) if failed else None,
        )

    async def fail_unqueued_job_item(self, job_id: str, sku_id: str) -> None:
        """Record a publish failure if the worker has not already started the SKU."""
        table = JobItemTable.__table__
        async with self.engine.begin() as conn:
            result = await conn.execute(
                update(table)
                .where(
                    table.c.job_id == _as_uuid(job_id),
                    table.c.sku_id == sku_id,
                    table.c.status == "pending",
                )
                .values(
                    status="failed",
                    failed_assets=1,
                    error_msg="任务队列暂时不可用，请重试",
                    updated_at=datetime.now(timezone.utc),
                )
            )
        if result.rowcount:
            await self.refresh_job_progress(job_id)

    async def complete_job_item(
        self,
        job_id: str,
        sku_id: str,
        *,
        completed_assets: int,
        failed_assets: int,
        error_msg: str | None = None,
    ) -> dict:
        if failed_assets == 0:
            status = "done"
        elif completed_assets == 0:
            status = "failed"
        else:
            status = "partial_fail"
        table = JobItemTable.__table__
        async with self.engine.begin() as conn:
            await conn.execute(
                update(table)
                .where(table.c.job_id == _as_uuid(job_id), table.c.sku_id == sku_id)
                .values(
                    status=status,
                    completed_assets=completed_assets,
                    failed_assets=failed_assets,
                    error_msg=error_msg,
                    updated_at=datetime.now(timezone.utc),
                )
            )
        return await self.refresh_job_progress(job_id)

    async def mark_job_item_retrying(
        self, job_id: str, sku_id: str, error_msg: str, *, repair_id: str | None = None
    ) -> None:
        table = JobItemTable.__table__
        async with self.engine.begin() as conn:
            await conn.execute(
                update(table)
                .where(table.c.job_id == _as_uuid(job_id), table.c.sku_id == sku_id)
                .values(
                    status="repair_retrying" if repair_id else "retrying",
                    error_msg=error_msg,
                    updated_at=datetime.now(timezone.utc),
                )
            )

    async def save_job_item_context(
        self, job_id: str, sku_id: str, generation_context: dict
    ) -> None:
        table = JobItemTable.__table__
        async with self.engine.begin() as conn:
            await conn.execute(
                update(table)
                .where(table.c.job_id == _as_uuid(job_id), table.c.sku_id == sku_id)
                .values(
                    generation_context=generation_context,
                    updated_at=datetime.now(timezone.utc),
                )
            )

    async def get_job_items(self, job_id: str) -> list[dict]:
        table = JobItemTable.__table__
        async with self.engine.connect() as conn:
            rows = await conn.execute(
                select(table).where(table.c.job_id == _as_uuid(job_id))
            )
            return [dict(row) for row in rows.mappings().all()]

    async def refresh_job_progress(self, job_id: str) -> dict:
        item = JobItemTable.__table__
        job = JobTable.__table__
        job_uuid = _as_uuid(job_id)
        terminal = ["done", "partial_fail", "failed"]
        async with self.engine.begin() as conn:
            await conn.execute(
                select(job.c.job_id).where(job.c.job_id == job_uuid).with_for_update()
            )
            counts = (
                await conn.execute(
                    select(
                        func.count().label("total_skus"),
                        func.count().filter(item.c.status.in_(terminal)).label("done_skus"),
                        func.count().filter(item.c.status == "failed").label("failed_skus"),
                        func.count().filter(item.c.status == "partial_fail").label("partial_skus"),
                        func.coalesce(func.sum(item.c.completed_assets), 0).label("completed_assets"),
                        func.coalesce(func.sum(item.c.failed_assets), 0).label("failed_assets"),
                    ).where(item.c.job_id == job_uuid)
                )
            ).mappings().one()
            total = int(counts["total_skus"])
            done = int(counts["done_skus"])
            if done < total:
                status = "running"
                finished_at = None
            elif int(counts["failed_skus"]) == total:
                status = "failed"
                finished_at = datetime.now(timezone.utc)
            elif int(counts["failed_skus"]) or int(counts["partial_skus"]):
                status = "partial_fail"
                finished_at = datetime.now(timezone.utc)
            else:
                status = "done"
                finished_at = datetime.now(timezone.utc)
            await conn.execute(
                update(job)
                .where(job.c.job_id == job_uuid)
                .values(status=status, done_skus=done, finished_at=finished_at)
            )

        event = {
            "event": "done" if done == total else "progress",
            "job_id": job_id,
            "status": status,
            "done_skus": done,
            "total_skus": total,
            "completed_assets": int(counts["completed_assets"]),
            "failed_assets": int(counts["failed_assets"]),
        }
        try:
            await self.redis.publish(f"job:{job_id}", json.dumps(event))
        except Exception:
            logger.warning("Failed to publish job progress for {}", job_id, exc_info=True)
        return event

    async def add_asset(
        self,
        *,
        job_id: str,
        tenant_id: str,
        sku_id: str,
        output_type: str,
        model_used: str,
        status: AssetStatus,
        file_path: str | None = None,
        platform: str | None = None,
        error_msg: str | None = None,
    ) -> str:
        table = AssetTable.__table__
        platform_match = table.c.platform.is_(None) if platform is None else table.c.platform == platform
        async with self.engine.begin() as conn:
            existing = (
                await conn.execute(
                    select(table.c.asset_id).where(
                        table.c.job_id == _as_uuid(job_id),
                        table.c.sku_id == sku_id,
                        table.c.output_type == output_type,
                        platform_match,
                        table.c.status == AssetStatus.success,
                    )
                )
            ).scalar_one_or_none()
            values = dict(
                    job_id=_as_uuid(job_id),
                    tenant_id=_as_uuid(tenant_id),
                    sku_id=sku_id,
                    output_type=output_type,
                    platform=platform,
                    model_used=model_used,
                    file_path=file_path,
                    status=status,
                    error_msg=error_msg,
                )
            if status == AssetStatus.success and existing:
                await conn.execute(update(table).where(table.c.asset_id == existing).values(**values))
                return str(existing)
            asset_id = uuid.uuid4()
            values["asset_id"] = asset_id
            await conn.execute(table.insert().values(**values))
        return str(asset_id)

    async def get_assets_for_job(self, job_id: str, *, sku_id: str | None = None) -> list[dict]:
        table = AssetTable.__table__
        statement = select(table).where(table.c.job_id == _as_uuid(job_id)).order_by(
            table.c.created_at, table.c.asset_id
        )
        if sku_id is not None:
            statement = statement.where(table.c.sku_id == sku_id)
        async with self.engine.connect() as conn:
            result = await conn.execute(statement)
            return [dict(row) for row in result.mappings().all()]

    async def get_asset(
        self, job_id: str, sku_id: str, output_type: str, platform: str | None
    ) -> dict | None:
        table = AssetTable.__table__
        platform_match = table.c.platform.is_(None) if platform is None else table.c.platform == platform
        async with self.engine.connect() as conn:
            row = (
                await conn.execute(
                    select(table).where(
                        table.c.job_id == _as_uuid(job_id),
                        table.c.sku_id == sku_id,
                        table.c.output_type == output_type,
                        platform_match,
                        table.c.status == AssetStatus.success,
                    )
                )
            ).mappings().first()
        return dict(row) if row else None

    async def finalize_job(self, job_id: str, success: int, failed: int) -> str:
        status = "done" if failed == 0 else ("failed" if success == 0 else "partial_fail")
        async with self.engine.connect() as conn:
            await conn.execute(
                update(JobTable.__table__)
                .where(JobTable.__table__.c.job_id == _as_uuid(job_id))
                .values(
                    status=status,
                    done_skus=success,
                    finished_at=datetime.now(timezone.utc),
                )
            )
            await conn.commit()
        event = {
            "event": "done",
            "job_id": job_id,
            "status": status,
            "success": success,
            "failed": failed,
        }
        try:
            await self.redis.publish(f"job:{job_id}", json.dumps(event))
        except Exception:
            logger.warning("Failed to publish job completion event for {}", job_id, exc_info=True)
        return status

    async def delete_job(self, job_id: str) -> int:
        """Delete a job and all its assets. Returns number of jobs deleted (0 or 1)."""
        job_uuid = _as_uuid(job_id)
        async with self.engine.begin() as conn:
            await conn.execute(
                delete(AssetTable.__table__).where(AssetTable.__table__.c.job_id == job_uuid)
            )
            result = await conn.execute(
                delete(JobTable.__table__).where(JobTable.__table__.c.job_id == job_uuid)
            )
        return result.rowcount
