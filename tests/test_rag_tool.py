import json
import os
from unittest.mock import AsyncMock

os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("OPENROUTER_API_KEY", "test-key")
import mediaforge.config; mediaforge.config.clear_settings_cache()

import pytest
from mediaforge.orchestrator.rag_tool import RAGSearchTool
from mediaforge.rag.models import RagResult


@pytest.mark.asyncio
async def test_rag_tool_schema():
    tool = RAGSearchTool(vector_store=None)
    assert tool.name == "rag_search"
    assert "query_text" in tool.args_schema.model_fields


@pytest.mark.asyncio
async def test_keyword_only_search_accepts_single_route_scores():
    tool = RAGSearchTool(vector_store=None)
    refs = [
        RagResult(
            product_id=f"sku-{rank}", score=1 / (60 + rank),
            image_url=f"/{rank}.jpg", metadata={},
        )
        for rank in range(1, 4)
    ]
    tool.retriever = AsyncMock()
    tool.retriever.retrieve.return_value = refs
    tool.embed_client = AsyncMock()
    tool.embed_client.embed_text.side_effect = ValueError("DashScope key is missing")
    tool.embed_client.rerank.side_effect = lambda **kwargs: kwargs["candidates"]

    response = json.loads(await tool._arun(query_text="white linen", top_k=2))

    assert response["matched"] is True
    assert [result["product_id"] for result in response["results"]] == ["sku-1", "sku-2"]
