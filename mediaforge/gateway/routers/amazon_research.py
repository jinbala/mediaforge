from fastapi import APIRouter, Depends, HTTPException, Request

from mediaforge.gateway.dependencies import get_tenant_only
from mediaforge.gateway.middleware.rate_limit import limiter
from mediaforge.models.amazon_research import AmazonResearchReport, AmazonResearchRequest
from mediaforge.models.tenant import Tenant
from mediaforge.rag.amazon_research import AmazonFetchError, AmazonSourceError, run_amazon_research

router = APIRouter(prefix="/api/v1/research", tags=["research"])


@router.post("/amazon", response_model=AmazonResearchReport)
@limiter.limit("8/minute")
async def research_amazon(
    request: Request,
    body: AmazonResearchRequest,
    _tenant: Tenant = Depends(get_tenant_only),
) -> AmazonResearchReport:
    # 由 Gateway 校验租户并限流，调研交给 rag 层，按输入错误和上游失败返回对应状态码。
    try:
        return await run_amazon_research(body)
    except AmazonSourceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except AmazonFetchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
