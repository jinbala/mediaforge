from unittest.mock import AsyncMock

import pytest

from mediaforge.config import get_settings
from mediaforge.rag import ingest
from mediaforge.rag.embeddings import DashScopeEmbeddingClient


def test_model_names_configured():
    client = DashScopeEmbeddingClient(api_key="test")
    settings = get_settings()
    assert client.image_model == settings.dashscope_image_model
    assert client.text_model == settings.dashscope_text_model
    assert client.rerank_model == settings.dashscope_rerank_model


@pytest.mark.asyncio
async def test_dashscope_calls_are_skipped_without_a_dashscope_key(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "dashscope_api_key", " ")
    monkeypatch.setattr(settings, "openrouter_api_key", "openrouter-test-key")
    client = DashScopeEmbeddingClient()

    assert client.api_key == ""
    with pytest.raises(RuntimeError, match="DASHSCOPE_API_KEY"):
        await client.embed_text("连衣裙")
    with pytest.raises(RuntimeError, match="DASHSCOPE_API_KEY"):
        await client.embed_image("unreadable-image.jpg")

    candidates = [{"product_id": "SKU001", "image_url": "unreadable-image.jpg"}]
    assert await client.rerank("连衣裙", None, candidates) == candidates


@pytest.mark.asyncio
async def test_ingest_without_dashscope_indexes_metadata_and_requires_the_image(monkeypatch):
    monkeypatch.setattr(get_settings(), "dashscope_api_key", "")
    store = AsyncMock()
    upsert_products = AsyncMock()
    image_url = "/api/v1/files/rag/global/SKU001.jpg"
    monkeypatch.setattr(ingest, "get_vector_store", lambda: store)
    monkeypatch.setattr(ingest, "upsert_products", upsert_products)
    monkeypatch.setattr(ingest, "_prepare_image", AsyncMock(side_effect=[
        ("source.jpg", image_url), ("", ""),
    ]))

    await ingest.embed_and_upsert(
        [{"product_id": "SKU001", "description": "white shirt"}, {"product_id": "SKU002"}],
        require_image=True,
    )

    items = store.upsert.await_args.args[0]
    assert [(item.product_id, item.search_text, item.image_url) for item in items] == [
        ("SKU001", "white shirt", image_url),
    ]
    records = upsert_products.await_args.args[0]
    assert [(row["sku_code"], row["index_status"]) for row in records] == [
        ("SKU001", "indexed"), ("SKU002", "failed"),
    ]
