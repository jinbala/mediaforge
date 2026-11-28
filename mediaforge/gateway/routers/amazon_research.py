from datetime import datetime
from time import monotonic
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from kombu.exceptions import OperationalError
from loguru import logger
from redis.exceptions import RedisError
from starlette.concurrency import run_in_threadpool

from mediaforge.db import amazon_research_store as store
from mediaforge.gateway.dependencies import get_current_principal, get_tenant_only
from mediaforge.gateway.middleware.rate_limit import limiter
from mediaforge.models.amazon_category import CategoryInputs
from mediaforge.models.amazon_research import AmazonResearchReport, AmazonResearchRequest
from mediaforge.models.amazon_research_comparison import ResearchRunComparison
from mediaforge.models.amazon_research_export import ResearchEvidenceBundle, ResearchReportIndex
from mediaforge.models.amazon_research_run import (
    AmazonResearchRun,
    AmazonResearchRunRequest,
    AmazonResearchRunSummary,
    RetryTarget,
)
from mediaforge.models.amazon_script_tools import (
    FbaInput,
    HsInput,
    KeywordInput,
    LandedInput,
    ListingInput,
    PpcInput,
    ScriptInput,
    ScriptOutput,
)
from mediaforge.models.amazon_skills import SkillDefinition, SkillId, SkillReport, SkillRequest
from mediaforge.models.tenant import Tenant
from mediaforge.models.user import User
from mediaforge.orchestrator.tasks import process_amazon_research
from mediaforge.rag.amazon_category_collection import board_identity, normalize_board
from mediaforge.rag.amazon_research import (
    AmazonFetchError,
    AmazonSourceError,
    amazon_public_client,
    normalize_source,
    run_amazon_research,
)
from mediaforge.rag.amazon_script_samples import lookup_hs, sample_keywords, sample_products
from mediaforge.rag.amazon_store_collection import collect_market_listing
from mediaforge.workers.amazon_fba_fees import calculate_fba
from mediaforge.workers.amazon_landed_cost import calculate_landed
from mediaforge.workers.amazon_research_comparison import compare_research_runs
from mediaforge.workers.amazon_research_export import (
    build_evidence_bundle,
    build_report_index,
    build_report_markdown,
)
from mediaforge.workers.amazon_skill_definitions import SKILLS
from mediaforge.workers.amazon_skills import generate_skill_report

router = APIRouter(prefix="/api/v1/research", tags=["research"])


@router.post("/amazon", response_model=AmazonResearchReport)
@limiter.limit("8/minute")
async def research_amazon(
    request: Request,
    body: AmazonResearchRequest,
    _tenant: Tenant = Depends(get_tenant_only),
) -> AmazonResearchReport:
    # 原有单次查询入口保留，深调研与跨设备快照走新任务资源。
    try:
        return await run_amazon_research(body)
    except AmazonSourceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except AmazonFetchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/amazon/tools", response_model=ScriptOutput)
@limiter.limit("8/minute")
async def research_amazon_tool(
    request: Request,
    body: ScriptInput,
    _tenant: Tenant = Depends(get_tenant_only),
) -> ScriptOutput:
    start = monotonic()
    try:
        if isinstance(body, KeywordInput):
            result = await sample_keywords(body)
        elif isinstance(body, (ListingInput, PpcInput)):
            result = await sample_products(body)
        elif isinstance(body, FbaInput):
            result = calculate_fba(body)
        elif isinstance(body, LandedInput):
            result = calculate_landed(body)
        elif isinstance(body, HsInput):
            result = await lookup_hs(body)
    except AmazonSourceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except AmazonFetchError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    # 脚本报告只使用本次入参和公开来源，不记录商品词、报关资料和响应正文。
    logger.info("Amazon tool report={} tool={} status={} duration_ms={}",
                result.report_id, result.tool, result.status, round((monotonic() - start) * 1000))
    return result


@router.get("/amazon/skills", response_model=list[SkillDefinition])
async def list_amazon_skills(_tenant: Tenant = Depends(get_tenant_only)) -> list[SkillDefinition]:
    return list(SKILLS.values())


@router.post("/amazon/skills/{req}", response_model=SkillReport)
@limiter.limit("12/minute")
async def generate_amazon_skill(
    request: Request, req: SkillId, body: SkillRequest,
    _tenant: Tenant = Depends(get_tenant_only),
) -> SkillReport:
    try:
        return await generate_skill_report(req, body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except AmazonFetchError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _owner(principal: tuple[User, Tenant]) -> tuple[UUID, UUID]:
    user, tenant = principal
    # 没有创建账号的旧式 API Key 沿用原查询权限，归属到个人历史需要真实账号。
    if user.user_id.int == 0:
        raise HTTPException(status_code=403, detail="调研历史需通过账号或账号创建的 API Key 访问。")
    return tenant.tenant_id, user.user_id


async def _owned_run(owner: tuple[UUID, UUID], run_id: UUID) -> AmazonResearchRun:
    run = await store.get_run(owner, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="未找到调研任务。")
    return run


def _has_sample(run: AmazonResearchRun, asin: str | None) -> bool:
    return bool(asin and (
        asin in run.snapshot.asin_sources
        or (run.report and any(item.asin == asin for item in run.report.listings))
    ))


async def _enqueue(run: AmazonResearchRun, owner: tuple[UUID, UUID]) -> AmazonResearchRun:
    # 先提交数据库，再通知 worker，派发失败也保留可恢复的任务标识。
    try:
        await run_in_threadpool(
            process_amazon_research.apply_async,
            args=(str(run.id), run.attempt),
            task_id=f"amazon-research-{run.id}-{run.attempt}",
            queue="low",
            retry=False,
            ignore_result=True,
        )
    except (OperationalError, RedisError, OSError) as exc:
        logger.warning("Amazon research run={} phase=queue error={}", run.id, type(exc).__name__)
        await store.record_queue_failure(run.id, run.attempt)
    return await _owned_run(owner, run.id)


@router.post("/amazon/runs", status_code=202, response_model=AmazonResearchRun)
@limiter.limit("8/minute")
async def submit_amazon_run(
    request: Request,
    body: AmazonResearchRunRequest,
    principal: tuple[User, Tenant] = Depends(get_current_principal),
) -> AmazonResearchRun:
    owner = _owner(principal)
    try:
        if body.mode != "product" and body.product_costs is not None:
            raise AmazonSourceError("单品成本仅用于单品调研任务。")
        if body.mode == "bestsellers":
            body.source = normalize_board(body.source)
            body.category = body.category or CategoryInputs()
            children = {}
            for source in body.category.subcategories:
                url = normalize_board(source)
                identity = board_identity(url)
                if identity != board_identity(body.source):
                    children.setdefault(identity, url)
            body.category.subcategories = list(children.values())
        else:
            body.source = normalize_source(body.mode, body.source)
            if body.category is not None:
                raise AmazonSourceError("子类目和成本假设仅用于类目榜单任务。")
        body.keyword = body.keyword.strip()
        body.review_samples = body.review_samples.strip() if body.mode == "product" else ""
        body.supplemental_notes = body.supplemental_notes.strip()
    except AmazonSourceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if body.context is not None:
        parent = await _owned_run(owner, body.context.run_id)
        if parent.mode == body.mode or (body.mode == "product" and (
            body.context.asin != body.source.rsplit("/", 1)[-1]
            or not _has_sample(parent, body.context.asin)
        )) or (parent.mode == "bestsellers" and not _has_sample(parent, body.context.asin)):
            raise HTTPException(status_code=422, detail="来源任务未包含该调研方向的商品样本。")
    try:
        run, created = await store.submit_run(owner, body)
    except store.ResearchRunConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await _enqueue(run, owner) if created else run


@router.get("/amazon/runs", response_model=list[AmazonResearchRunSummary])
async def history_amazon_runs(
    before: datetime | None = None,
    principal: tuple[User, Tenant] = Depends(get_current_principal),
) -> list[AmazonResearchRunSummary]:
    return await store.list_runs(_owner(principal), before)


@router.get("/amazon/runs/{run_id}", response_model=AmazonResearchRun)
async def get_amazon_run(
    run_id: UUID,
    principal: tuple[User, Tenant] = Depends(get_current_principal),
) -> AmazonResearchRun:
    return await _owned_run(_owner(principal), run_id)


@router.get("/amazon/runs/{run_id}/reports", response_model=ResearchReportIndex)
async def index_amazon_reports(
    run_id: UUID, principal: tuple[User, Tenant] = Depends(get_current_principal),
) -> ResearchReportIndex:
    return build_report_index(await _owned_run(_owner(principal), run_id))


@router.get("/amazon/runs/{run_id}/reports/{section}")
async def read_amazon_report(
    run_id: UUID, section: str,
    principal: tuple[User, Tenant] = Depends(get_current_principal),
) -> dict[str, str]:
    run = await _owned_run(_owner(principal), run_id)
    try:
        markdown = await run_in_threadpool(
            build_report_markdown, run, None if section == "full" else section,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="未找到该专题。") from exc
    return {"markdown": markdown}


@router.get("/amazon/runs/{run_id}/evidence", response_model=ResearchEvidenceBundle)
async def read_amazon_evidence(
    run_id: UUID, principal: tuple[User, Tenant] = Depends(get_current_principal),
) -> ResearchEvidenceBundle:
    run = await _owned_run(_owner(principal), run_id)
    return await run_in_threadpool(build_evidence_bundle, run)


@router.get("/amazon/runs/{run_id}/comparison", response_model=ResearchRunComparison)
async def compare_amazon_history(
    run_id: UUID, principal: tuple[User, Tenant] = Depends(get_current_principal),
) -> ResearchRunComparison:
    owner = _owner(principal)
    run = await _owned_run(owner, run_id)
    return compare_research_runs(run, await store.previous_run(owner, run))


@router.get("/amazon/runs/{run_id}/samples/{asin}/store")
@limiter.limit("8/minute")
async def resolve_amazon_sample_store(
    request: Request, run_id: UUID, asin: str = Path(pattern=r"^[A-Z0-9]{10}$"),
    principal: tuple[User, Tenant] = Depends(get_current_principal),
) -> dict[str, str]:
    run = await _owned_run(_owner(principal), run_id)
    if run.mode != "bestsellers" or not _has_sample(run, asin):
        raise HTTPException(status_code=404, detail="未找到该榜单商品样本。")
    # 榜单卡片通常不含店铺地址；只在用户点击时读取本站商品详情核对跳转来源。
    async with amazon_public_client() as client:
        listing = await collect_market_listing(client, asin)
    if listing.problem:
        raise HTTPException(status_code=502, detail=listing.problem)
    if not listing.store_url:
        raise HTTPException(status_code=404, detail="商品页未公开可用的品牌店铺链接。")
    return {"url": listing.store_url}


@router.post("/amazon/runs/{run_id}/retry", status_code=202, response_model=AmazonResearchRun)
@limiter.limit("8/minute")
async def retry_amazon_run(
    request: Request,
    run_id: UUID,
    target: RetryTarget,
    item_key: str | None = None,
    principal: tuple[User, Tenant] = Depends(get_current_principal),
) -> AmazonResearchRun:
    owner = _owner(principal)
    try:
        run, queued = await store.retry_run(owner, run_id, target, item_key)
    except store.ResearchRunConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if run is None:
        raise HTTPException(status_code=404, detail="未找到调研任务。")
    return await _enqueue(run, owner) if queued else run
