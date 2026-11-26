import csv
import io
import json
from pathlib import Path
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from loguru import logger
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from mediaforge.config import get_settings
from mediaforge.gateway.dependencies import get_tenant_only
from mediaforge.gateway.middleware.rate_limit import limiter
from mediaforge.models.tenant import Tenant
from mediaforge.rag.embeddings import DashScopeEmbeddingClient
from mediaforge.rag.factory import get_vector_store
from mediaforge.rag.models import RagResult
from mediaforge.rag.product_share import MAX_GALLERY_IMAGES, ProductShareError, preview_product_share

router = APIRouter(prefix="/api/v1/rag")

REQUIRED_COLS = {"product_id", "category", "style", "color"}
ALLOWED_CONTENT_TYPES = {
    "text/csv",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "application/octet-stream",
}


class IngestResponse(BaseModel):
    accepted: int
    message: str


class IngestPreviewRow(BaseModel):
    product_id: str
    image_ref: str
    has_image_url: bool
    image_name: str


class IngestPreviewResponse(BaseModel):
    rows: list[IngestPreviewRow]
    skipped: int


class ProductLinkRequest(BaseModel):
    text: str = Field(min_length=1, max_length=16_000)


class ProductLinkPreview(BaseModel):
    product_id: str
    title: str
    image_data: str
    gallery_image_data: list[str] = Field(default_factory=list)


class IngestStatusResponse(BaseModel):
    status: str
    count: int
    backend: str


class SearchRequest(BaseModel):
    query: str
    category: str = ""
    image_url: str = ""
    top_k: int = 5


class SearchResult(BaseModel):
    product_id: str
    score: float
    image_url: str
    metadata: dict


class SearchResponse(BaseModel):
    results: list[SearchResult]


def _parse_csv(content: bytes) -> list[dict]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("gb18030")
    reader = csv.DictReader(io.StringIO(text))
    reader.fieldnames = [header.strip() for header in (reader.fieldnames or [])]
    _check_headers(reader.fieldnames)
    return list(reader)


def _parse_excel(content: bytes) -> list[dict]:
    wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    try:
        rows = wb.active.iter_rows(values_only=True)
        headers = [str(cell or "").strip() for cell in next(rows, ())]
        _check_headers(headers)
        return [dict(zip(headers, row)) for row in rows if any(cell is not None for cell in row)]
    finally:
        wb.close()


def _check_headers(headers: list[str]) -> None:
    if not headers:
        raise ValueError("表格是空的，请使用导入模板填写商品。")
    missing = REQUIRED_COLS - set(headers)
    if missing:
        raise ValueError(f"表格缺少必填列：{', '.join(sorted(missing))}")


def _rows_to_items(rows: list[dict]) -> list[dict]:
    seen: set[str] = set()
    clean: list[dict] = []
    for row in rows:
        value = row.get("product_id")
        pid = str(value).strip() if value is not None else ""
        if pid and pid not in seen:
            seen.add(pid)
            clean.append({**row, "product_id": pid})
    return clean


async def _read_catalog(file: UploadFile) -> tuple[list[dict], int]:
    filename = (file.filename or "").lower()
    ct = (file.content_type or "").split(";")[0].strip().lower()
    if filename.endswith(".xls"):
        raise HTTPException(415, "旧版 .xls 请先另存为 .xlsx 或 CSV 后再导入。")
    if not filename.endswith((".xlsx", ".csv")) and ct not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(415, "请选择 .xlsx 或 .csv 表格。")
    is_excel = filename.endswith(".xlsx") or (
        not filename.endswith(".csv") and ct == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    limit = get_settings().max_upload_size
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(413, "表格超过上传大小限制。")
    try:
        parsed = await run_in_threadpool(_parse_excel if is_excel else _parse_csv, content)
    except (UnicodeDecodeError, csv.Error, BadZipFile, InvalidFileException, ParseError, KeyError, OSError) as exc:
        raise HTTPException(422, "无法读取表格，请检查文件格式，或用 Excel 另存为 .xlsx 后重试。") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    rows = _rows_to_items(parsed)
    if not rows:
        raise HTTPException(422, "表格里没有填写 SKU（product_id）的商品。")
    return rows, len(parsed) - len(rows)


def _image_filename(value: str) -> str:
    return value.replace("\\", "/").rsplit("/", 1)[-1]


def _image_reference(row: dict) -> IngestPreviewRow:
    ref = str(row.get("image_url") or row.get("image_path") or "").strip()
    has_image_url = ref.startswith(("http://", "https://", "data:"))
    return IngestPreviewRow(
        product_id=row["product_id"],
        image_ref="表格内嵌图片" if ref.startswith("data:") else ref,
        has_image_url=has_image_url,
        image_name="" if has_image_url else _image_filename(ref).lower(),
    )


@router.post("/ingest/preview", response_model=IngestPreviewResponse)
@limiter.limit("30/minute")
async def preview_products(
    request: Request,
    file: UploadFile = File(...),
    tenant: Tenant = Depends(get_tenant_only),
) -> IngestPreviewResponse:
    rows, skipped = await _read_catalog(file)
    return IngestPreviewResponse(rows=[_image_reference(row) for row in rows], skipped=skipped)


@router.post("/ingest/link/preview", response_model=ProductLinkPreview)
@limiter.limit("15/minute")
async def preview_product_link(
    request: Request,
    body: ProductLinkRequest,
    tenant: Tenant = Depends(get_tenant_only),
) -> ProductLinkPreview:
    try:
        return ProductLinkPreview(**await preview_product_share(body.text))
    except ProductShareError as exc:
        raise HTTPException(422, str(exc)) from exc


async def _embed_and_upsert(
    rows: list[dict],
    tenant_id: str,
    image_files: dict[str, tuple[bytes, str]],
) -> None:
    from mediaforge.rag.ingest import embed_and_upsert

    await embed_and_upsert(
        rows,
        image_files=image_files,
        tenant_id=tenant_id,
        visibility="private",
        source_type="user_upload",
        require_image=True,
    )


@router.post("/ingest", response_model=IngestResponse)
@limiter.limit("5/minute")
async def ingest_products(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    images: list[UploadFile] = File(default=[]),
    image_assignments: str = Form(default="{}"),
    tenant: Tenant = Depends(get_tenant_only),
) -> IngestResponse:
    rows, _ = await _read_catalog(file)
    try:
        assignments = json.loads(image_assignments)
    except json.JSONDecodeError as exc:
        raise HTTPException(422, "图片分配数据格式不正确。") from exc
    if not isinstance(assignments, dict):
        raise HTTPException(422, "图片分配数据格式不正确。")
    for sku, assignment in assignments.items():
        names = assignment if isinstance(assignment, list) else [assignment]
        if not 1 <= len(names) <= MAX_GALLERY_IMAGES or any(
            not isinstance(name, str) or not name for name in names
        ):
            raise HTTPException(422, "商品图片分配格式错误，单个商品最多导入 10 张图。")
        names = [name.lower() for name in names]
        if len(set(names)) != len(names):
            raise HTTPException(422, "同一商品的图片文件名不能重复。")
        assignments[sku] = names

    image_files: dict[str, tuple[bytes, str]] = {}
    limit = get_settings().max_upload_size
    for image in images:
        try:
            filename = _image_filename(image.filename or "")
            if not filename:
                continue
            image_content = await image.read(limit + 1)
            if len(image_content) > limit:
                raise HTTPException(413, "图片超过上传大小限制。")
            ext = Path(filename).suffix or ".jpg"
            image_files[filename.lower()] = (image_content, ext)
        finally:
            await image.close()

    missing_images: list[str] = []
    for row in rows:
        reference = _image_reference(row)
        assigned = assignments.get(reference.product_id)
        if assigned is None and reference.has_image_url:
            continue
        names = assigned if assigned is not None else [reference.image_name]
        missing = [name for name in names if name not in image_files]
        if not missing:
            row.update(image_url="", image_path=names[0])
            if len(names) > 1:
                row["gallery_image_paths"] = names[1:]
        else:
            label = "、".join(missing) or reference.image_ref or "未填写图片文件名"
            missing_images.append(f"{reference.product_id}（{label}）")
    if missing_images:
        preview = "；".join(missing_images[:10])
        suffix = f"；另有 {len(missing_images) - 10} 条" if len(missing_images) > 10 else ""
        raise HTTPException(
            status_code=422,
            detail=f"以下商品没有匹配到上传图片：{preview}{suffix}",
        )

    background_tasks.add_task(
        _embed_and_upsert,
        rows,
        str(tenant.tenant_id),
        image_files,
    )

    return IngestResponse(
        accepted=len(rows),
        message=f"Accepted {len(rows)} products. Embedding and indexing in background.",
    )


@router.get("/status", response_model=IngestStatusResponse)
async def ingest_status(
    tenant: Tenant = Depends(get_tenant_only),
) -> IngestStatusResponse:
    vector_store = get_vector_store()
    health = await vector_store.health()
    return IngestStatusResponse(
        status=health.get("status", "unknown"),
        count=health.get("count", 0),
        backend=health.get("backend", "unknown"),
    )


@router.post("/search", response_model=SearchResponse)
async def search_products(
    body: SearchRequest,
    tenant: Tenant = Depends(get_tenant_only),
) -> SearchResponse:
    from mediaforge.rag.cached_retriever import CachedRetriever
    from mediaforge.rag.retriever import ReferenceRetriever

    embed_client = DashScopeEmbeddingClient()

    query_dense = None
    query_image = None

    try:
        query_dense = await embed_client.embed_text(body.query)
    except Exception as exc:
        logger.warning("Text dense embedding failed; BM25 search remains available: {}", exc)

    if body.image_url:
        try:
            query_image = await embed_client.embed_image(body.image_url)
        except Exception as exc:
            logger.warning("Embedding image failed: {}", exc)

    vector_store = get_vector_store()
    from mediaforge.db.redis_client import get_redis

    redis = await get_redis()
    cached = CachedRetriever(vector_store, redis=redis)
    retriever = ReferenceRetriever(cached)

    refs = await retriever.retrieve(
        image_path=body.image_url or None,
        text=body.query,
        category=body.category,
        tenant_id=str(tenant.tenant_id),
        top_k=body.top_k,
        query_dense=query_dense,
        query_image=query_image,
    )

    # Optional rerank
    if refs and len(refs) > 1:
        candidates = [
            {**r.metadata, "product_id": r.product_id, "image_url": r.image_url, "score": r.score}
            for r in refs
        ]
        try:
            reranked = await embed_client.rerank(
                query_text=body.query,
                query_image_path=body.image_url or None,
                candidates=candidates,
                top_n=body.top_k,
            )
            refs = [
                RagResult(
                    product_id=c["product_id"],
                    score=c.get("rerank_score", c.get("score", 0.0)),
                    image_url=c.get("image_url", ""),
                    metadata={
                        k: v
                        for k, v in c.items()
                        if k not in {"product_id", "image_url", "score", "rerank_score"}
                    },
                )
                for c in reranked
            ]
        except Exception as exc:
            logger.warning("Rerank failed: {}", exc)

    return SearchResponse(
        results=[
            SearchResult(
                product_id=r.product_id,
                score=r.score,
                image_url=r.image_url,
                metadata=r.metadata,
            )
            for r in refs
        ]
    )
