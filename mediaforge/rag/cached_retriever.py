import hashlib
import json

from loguru import logger

from mediaforge.rag.models import RagResult
from mediaforge.rag.vector_store import VectorStore


def _cache_version_key(tenant_id: str) -> str:
    return f"mediaforge:rag_cache_version:{tenant_id}"


async def invalidate_tenant_rag_cache(redis, tenant_id: str) -> None:
    await redis.incr(_cache_version_key(tenant_id))


def _cache_key(
    version: int,
    text: str,
    category: str,
    tenant_id: str,
    top_k: int,
    query_image: list[float] | None = None,
    min_sales_count: int = 0,
    quality_status: str = "",
) -> str:
    img_sig = ""
    if query_image:
        img_sig = hashlib.sha256(json.dumps(query_image[:10]).encode()).hexdigest()[:12]
    raw = (
        f"rag:v2:{tenant_id}:{version}:{text}:{category}:{top_k}:{img_sig}:"
        f"{min_sales_count}:{quality_status}"
    )
    return f"mediaforge:rag_cache:{hashlib.sha256(raw.encode()).hexdigest()[:32]}"


class CachedRetriever:
    """Caching layer over VectorStore.

    Delegates to vector_store.hybrid_search() and caches results in Redis.
    Exposes the same retrieve() interface as ReferenceRetriever.
    """

    TTL_SECONDS = 600

    def __init__(self, vector_store: VectorStore, redis=None):
        self.vector_store = vector_store
        self.redis = redis

    async def retrieve(
        self,
        image_path: str | None,
        text: str,
        category: str,
        tenant_id: str = "",
        top_k: int = 5,
        query_dense: list[float] | None = None,
        query_image: list[float] | None = None,
        min_sales_count: int = 0,
        quality_status: str = "",
    ) -> list[RagResult]:
        key = None
        if self.redis is not None:
            try:
                version = int(await self.redis.get(_cache_version_key(tenant_id)) or 0)
                key = _cache_key(
                    version, text, category, tenant_id, top_k, query_image,
                    min_sales_count, quality_status,
                )
                cached = await self.redis.get(key)
                if cached:
                    items = json.loads(cached)
                    return [RagResult(**item) for item in items]
            except Exception as exc:
                logger.warning("RAG cache read failed: {}", exc)

        results = await self.vector_store.hybrid_search(
            image_path=image_path,
            text=text,
            category=category,
            tenant_id=tenant_id,
            top_k=top_k,
            query_dense=query_dense,
            query_image=query_image,
            min_sales_count=min_sales_count,
            quality_status=quality_status,
        )

        if key is not None and results:
            try:
                payload = json.dumps([r.model_dump() for r in results])
                await self.redis.set(key, payload, ex=self.TTL_SECONDS)
            except Exception as exc:
                logger.warning("RAG cache write failed: {}", exc)

        return results
