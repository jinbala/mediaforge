from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import and_, func, or_, select

from mediaforge.db.engine import get_engine
from mediaforge.db.redis_client import get_redis
from mediaforge.db.tables import ProductTable
from mediaforge.gateway.dependencies import get_tenant_only
from mediaforge.models.tenant import Tenant
from mediaforge.rag.cached_retriever import invalidate_tenant_rag_cache
from mediaforge.rag.factory import get_vector_store

router = APIRouter(prefix="/api/v1/products")


class ProductResponse(BaseModel):
    document_id: str
    sku_code: str
    visibility: str
    source_type: str
    category: str
    style: str
    color: str
    material: str
    season: str
    description: str
    sales_count: int
    price: Decimal | None
    image_url: str
    index_status: str
    index_error: str | None
    indexed_at: datetime | None
    updated_at: datetime


class ProductListResponse(BaseModel):
    products: list[ProductResponse]
    total: int
    page: int
    page_size: int


class ProductCategoriesResponse(BaseModel):
    categories: list[str]


class ProductFiltersResponse(BaseModel):
    categories: list[str]
    styles: list[str]
    colors: list[str]


class ProductSummaryResponse(BaseModel):
    global_count: int
    private_count: int
    indexing_count: int
    failed_count: int


def _visible_products(tenant_id):
    table = ProductTable.__table__
    return or_(table.c.visibility == "global", table.c.tenant_id == tenant_id)


@router.get("", response_model=ProductListResponse)
async def list_products(
    scope: str = Query(default="all", pattern="^(all|global|private)$"),
    limit: int = Query(default=200, ge=1, le=500),
    q: str | None = Query(default=None, max_length=100),
    category: str | None = Query(default=None, max_length=100),
    style: str | None = Query(default=None, max_length=100),
    color: str | None = Query(default=None, max_length=100),
    index_status: str | None = Query(default=None, pattern="^(indexed|indexing|pending|failed)$"),
    sort: str = Query(default="updated_desc", pattern="^(created_desc|updated_desc|sales_desc)$"),
    page: int = Query(default=1, ge=1),
    page_size: int | None = Query(default=None, ge=1, le=100),
    tenant: Tenant = Depends(get_tenant_only),
) -> ProductListResponse:
    table = ProductTable.__table__
    condition = _visible_products(tenant.tenant_id)
    if scope == "global":
        condition = table.c.visibility == "global"
    elif scope == "private":
        condition = table.c.tenant_id == tenant.tenant_id

    if q and q.strip():
        pattern = f"%{q.strip()}%"
        condition = and_(
            condition,
            or_(
                table.c.sku_code.ilike(pattern),
                table.c.description.ilike(pattern),
                table.c.category.ilike(pattern),
                table.c.style.ilike(pattern),
                table.c.color.ilike(pattern),
            ),
        )
    if category and category.strip():
        condition = and_(condition, table.c.category == category.strip())
    if style and style.strip():
        condition = and_(condition, table.c.style == style.strip())
    if color and color.strip():
        condition = and_(condition, table.c.color == color.strip())
    if index_status:
        condition = and_(condition, table.c.index_status == index_status)

    effective_page_size = page_size or limit
    if sort == "created_desc":
        order_by = (table.c.created_at.desc().nulls_last(), table.c.document_id)
    elif sort == "sales_desc":
        order_by = (table.c.sales_count.desc(),)
    else:
        order_by = (table.c.updated_at.desc(),)

    async with get_engine().connect() as connection:
        total = int(
            (
                await connection.execute(
                    select(func.count()).select_from(table).where(condition)
                )
            ).scalar_one()
        )
        result = await connection.execute(
            select(table)
            .where(condition)
            .order_by(*order_by)
            .limit(effective_page_size)
            .offset((page - 1) * effective_page_size)
        )
    return ProductListResponse(
        products=[ProductResponse(**dict(row)) for row in result.mappings()],
        total=total,
        page=page,
        page_size=effective_page_size,
    )


@router.get("/categories", response_model=ProductCategoriesResponse)
async def product_categories(
    scope: str = Query(default="all", pattern="^(all|global|private)$"),
    tenant: Tenant = Depends(get_tenant_only),
) -> ProductCategoriesResponse:
    table = ProductTable.__table__
    condition = _visible_products(tenant.tenant_id)
    if scope == "global":
        condition = table.c.visibility == "global"
    elif scope == "private":
        condition = table.c.tenant_id == tenant.tenant_id
    async with get_engine().connect() as connection:
        result = await connection.execute(
            select(table.c.category)
            .where(condition, table.c.category != "")
            .distinct()
            .order_by(table.c.category)
        )
    return ProductCategoriesResponse(categories=list(result.scalars().all()))


@router.get("/filters", response_model=ProductFiltersResponse)
async def product_filters(
    scope: str = Query(default="all", pattern="^(all|global|private)$"),
    tenant: Tenant = Depends(get_tenant_only),
) -> ProductFiltersResponse:
    table = ProductTable.__table__
    condition = _visible_products(tenant.tenant_id)
    if scope == "global":
        condition = table.c.visibility == "global"
    elif scope == "private":
        condition = table.c.tenant_id == tenant.tenant_id
    async with get_engine().connect() as connection:
        values = {}
        for name in ("category", "style", "color"):
            column = getattr(table.c, name)
            result = await connection.execute(
                select(column).where(condition, column != "").distinct().order_by(column)
            )
            values[f"{name}s" if name != "category" else "categories"] = list(result.scalars().all())
    return ProductFiltersResponse(**values)


@router.get("/summary", response_model=ProductSummaryResponse)
async def product_summary(
    tenant: Tenant = Depends(get_tenant_only),
) -> ProductSummaryResponse:
    table = ProductTable.__table__
    visible = _visible_products(tenant.tenant_id)
    async with get_engine().connect() as connection:
        row = (
            await connection.execute(
                select(
                    func.count().filter(table.c.visibility == "global").label("global_count"),
                    func.count().filter(table.c.tenant_id == tenant.tenant_id).label("private_count"),
                    func.count()
                    .filter(table.c.index_status.in_(["pending", "indexing"]))
                    .label("indexing_count"),
                    func.count().filter(table.c.index_status == "failed").label("failed_count"),
                ).where(visible)
            )
        ).mappings().one()
    return ProductSummaryResponse(**dict(row))


@router.delete("/{document_id:path}", status_code=204)
async def delete_product(
    document_id: str,
    tenant: Tenant = Depends(get_tenant_only),
) -> None:
    table = ProductTable.__table__
    owned = and_(
        table.c.document_id == document_id,
        table.c.tenant_id == tenant.tenant_id,
        table.c.visibility == "private",
    )
    async with get_engine().begin() as connection:
        index_status = (
            await connection.execute(
                select(table.c.index_status).where(owned).with_for_update()
            )
        ).scalar_one_or_none()
        if index_status is None:
            raise HTTPException(404, "找不到可删除的企业商品。")
        if index_status in {"pending", "indexing"}:
            raise HTTPException(409, "该商品正在建立索引，请稍后再删除。")

        try:
            await get_vector_store().delete(document_id)
            await invalidate_tenant_rag_cache(await get_redis(), str(tenant.tenant_id))
        except Exception as exc:
            logger.exception("Failed to remove product {} from search", document_id)
            raise HTTPException(503, "商品搜索索引暂时无法清理，请稍后重试。") from exc
        await connection.execute(table.delete().where(owned))
