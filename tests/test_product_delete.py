from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import quote
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, Request
from sqlalchemy.ext.asyncio import create_async_engine

from mediaforge.db.product_store import build_product_record
from mediaforge.db.tables import Base, HiddenPublicProductTable, ProductTable, TenantTable
from mediaforge.gateway.routers import products


@pytest.fixture
async def catalog(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")
    owners = [uuid4(), uuid4()]
    private, other, public, pending = [
        build_product_record(
            {"product_id": sku, "category": "shared" if owner is None else "tenant"},
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

    def test_tenant(request: Request):
        index = 1 if request.headers.get("X-Test-Tenant") == "other" else 0
        return SimpleNamespace(tenant_id=owners[index])

    app.dependency_overrides[products.get_tenant_only] = test_tenant

    try:
        async with engine.begin() as connection:
            await connection.run_sync(
                Base.metadata.create_all, tables=[tenants, table, HiddenPublicProductTable.__table__]
            )
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


async def test_hide_and_restore_public_product_for_one_tenant(catalog):
    client, store, _, (private, _, public, _) = catalog
    base = "/api/v1/products"
    public_url = f"{base}/visibility/{quote(public, safe='')}"
    private_url = f"{base}/visibility/{quote(private, safe='')}"
    other_tenant = {"X-Test-Tenant": "other"}

    assert (await client.put(private_url, json={"hidden": True})).status_code == 404
    for _ in range(2):
        assert (await client.put(public_url, json={"hidden": True})).status_code == 204

    assert (await client.get(base)).json()["total"] == 2
    assert (await client.get(f"{base}?scope=global")).json()["total"] == 0
    assert (await client.get(f"{base}/summary")).json()["global_count"] == 0
    assert (await client.get(f"{base}/filters")).json()["categories"] == ["tenant"]
    assert (await client.get(f"{base}/categories?scope=global")).json()["categories"] == []
    assert (await client.get(f"{base}/filters?scope=hidden")).json()["categories"] == ["shared"]
    hidden = (await client.get(f"{base}?scope=hidden")).json()["products"]
    assert [(row["document_id"], row["hidden"]) for row in hidden] == [(public, True)]
    assert (await client.get(f"{base}?scope=global", headers=other_tenant)).json()["total"] == 1
    assert (await client.get(f"{base}?scope=hidden", headers=other_tenant)).json()["total"] == 0
    assert (await client.put(public_url, json={"hidden": False}, headers=other_tenant)).status_code == 204
    assert (await client.get(f"{base}?scope=hidden")).json()["total"] == 1

    for _ in range(2):
        assert (await client.put(public_url, json={"hidden": False})).status_code == 204
    restored = (await client.get(f"{base}?scope=global")).json()["products"]
    assert [(row["document_id"], row["hidden"]) for row in restored] == [(public, False)]
    assert (await client.get(f"{base}?scope=hidden")).json()["total"] == 0
    store.delete.assert_not_awaited()
