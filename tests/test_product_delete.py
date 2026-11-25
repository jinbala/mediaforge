from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import quote
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine

from mediaforge.db.product_store import build_product_record
from mediaforge.db.tables import Base, ProductTable, TenantTable
from mediaforge.gateway.routers import products


@pytest.fixture
async def catalog(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")
    owners = [uuid4(), uuid4()]
    private, other, public, pending = [
        build_product_record(
            {"product_id": sku},
            document_id=f"{owner or 'global'}:{sku}",
            tenant_id=str(owner) if owner else None,
            visibility="private" if owner else "global",
            source_type="test",
            search_text="sample",
            index_status=status,
        )
        for owner, sku, status in [
            (owners[0], "QA/01", "indexed"),
            (owners[1], "QA/01", "indexed"),
            (None, "QA/01", "indexed"),
            (owners[0], "QA/02", "pending"),
        ]
    ]
    table = ProductTable.__table__
    tenants = TenantTable.__table__
    store = SimpleNamespace(delete=AsyncMock())
    redis = SimpleNamespace(incr=AsyncMock())
    monkeypatch.setattr(products, "get_engine", lambda: engine)
    monkeypatch.setattr(products, "get_vector_store", lambda: store)
    monkeypatch.setattr(products, "get_redis", AsyncMock(return_value=redis))
    app = FastAPI()
    app.include_router(products.router)
    app.dependency_overrides[products.get_tenant_only] = lambda: SimpleNamespace(tenant_id=owners[0])

    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all, tables=[tenants, table])
            await connection.execute(
                tenants.insert(),
                [{"tenant_id": owner, "name": "Fixture", "api_key_hash": ""} for owner in owners],
            )
            await connection.execute(table.insert(), [private, other, public, pending])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            ids = [row["document_id"] for row in (private, other, public, pending)]
            yield client, store, redis, ids
    finally:
        await engine.dispose()


async def test_delete_removes_only_own_private_product(catalog):
    client, store, redis, (private, other, public, pending) = catalog
    for target, status in [(other, 404), (public, 404), (pending, 409), (private, 204), (private, 404)]:
        response = await client.delete(f"/api/v1/products/{quote(target, safe='')}")
        assert response.status_code == status
    store.delete.assert_awaited_once_with(private)
    redis.incr.assert_awaited_once()
    response = await client.get("/api/v1/products/summary")
    assert response.json() == {
        "global_count": 1, "private_count": 1, "indexing_count": 1, "failed_count": 0,
    }


async def test_delete_keeps_product_when_index_removal_fails(catalog):
    client, store, redis, (private, *_) = catalog
    store.delete.side_effect = TimeoutError
    response = await client.delete(f"/api/v1/products/{quote(private, safe='')}")
    assert response.status_code == 503
    redis.incr.assert_not_awaited()
    response = await client.get("/api/v1/products?scope=private")
    assert response.json()["total"] == 2
