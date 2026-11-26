import os

os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("OPENROUTER_API_KEY", "test-key")
import mediaforge.config  # noqa: E402

mediaforge.config.clear_settings_cache()

import asyncio  # noqa: E402
from datetime import datetime  # noqa: E402
from unittest.mock import AsyncMock, Mock  # noqa: E402
from uuid import UUID  # noqa: E402

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from fakeredis.aioredis import FakeRedis  # noqa: E402
from fastapi import Depends, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from kombu.exceptions import OperationalError  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from mediaforge.db import AssetStatus  # noqa: E402
from mediaforge.db.job_store import JobStore  # noqa: E402
from mediaforge.db.tables import Base  # noqa: E402
from mediaforge.gateway.dependencies import (  # noqa: E402
    get_job_store,
    get_tenant_only,
)
from mediaforge.gateway.main import app  # noqa: E402
from mediaforge.models.job import AssetOutput, BatchSubmitPayload, GenerationContext, SkuInput  # noqa: E402
from mediaforge.models.tenant import Tenant, TenantPlan  # noqa: E402
from mediaforge.orchestrator import dispatcher  # noqa: E402
from mediaforge.orchestrator import nodes, tasks as worker_tasks  # noqa: E402
from mediaforge.workers.image.base import BaseImageWorker  # noqa: E402

TENANT_ID = UUID("550e8400-e29b-41d4-a716-446655440000")
OTHER_TENANT_ID = UUID("660e8400-e29b-41d4-a716-446655440001")


def _make_payload() -> BatchSubmitPayload:
    return BatchSubmitPayload(
        skus=[
            SkuInput(
                sku_id="SKU-001",
                product_image_url="https://example.com/img.jpg",
                product_name="Dress",
                category="apparel",
                target_platforms=["amazon"],
                output_types=["main_image"],
                market="US",
            )
        ]
    )


@pytest_asyncio.fixture
async def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    redis = FakeRedis(decode_responses=True)
    store = JobStore(engine, redis)
    tenant = Tenant(
        tenant_id=TENANT_ID,
        name="Test Pro",
        api_key_hash="",
        plan=TenantPlan.pro,
    )

    async def override_get_tenant_only(_principal=Depends(lambda: None)):
        return tenant

    async def override_get_job_store():
        return store

    app.dependency_overrides[get_tenant_only] = override_get_tenant_only
    app.dependency_overrides[get_job_store] = override_get_job_store

    test_client = TestClient(app)
    test_client.store = store
    try:
        yield test_client
    finally:
        app.dependency_overrides.clear()
        await redis.aclose()
        await engine.dispose()


@pytest.mark.asyncio
async def test_retry_task_reuses_failed_skus_and_deduplicates_requests(client, monkeypatch):
    store = client.store
    payload = _make_payload()
    successful = payload.skus[0]
    failed = successful.model_copy(update={"sku_id": "SKU-002", "ref_sku_id": "REF-001"})
    partial = successful.model_copy(update={"sku_id": "SKU-003"})
    payload.skus.extend([failed, partial])
    original_id = await store.create_job(str(TENANT_ID), payload)
    await store.add_asset(
        job_id=original_id,
        tenant_id=str(TENANT_ID),
        sku_id=successful.sku_id,
        output_type="main_image",
        model_used="openai/gpt-image-2",
        status=AssetStatus.success,
        file_path="https://example.com/result.png",
    )
    await store.complete_job_item(original_id, successful.sku_id, completed_assets=1, failed_assets=0)
    await store.complete_job_item(original_id, failed.sku_id, completed_assets=0, failed_assets=1)
    await store.complete_job_item(original_id, partial.sku_id, completed_assets=1, failed_assets=1)
    original = await store.get_job(original_id)
    original_assets = await store.get_assets_for_job(original_id)
    publish = Mock()
    monkeypatch.setattr(dispatcher.process_sku, "apply_async", publish)

    path = f"/api/v1/tasks/{original_id}/retry"
    first = client.post(path)
    second = client.post(path)

    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    retry_id = first.json()["job_id"]
    assert retry_id != original_id
    retry = await store.get_job(retry_id)
    expected_payload = payload.model_copy(update={"skus": [failed, partial]})
    assert retry["input_data"] == expected_payload.model_dump(mode="json")
    assert retry["source"] == "retry"
    assert publish.call_count == 2
    for queued, sku in zip(publish.call_args_list, expected_payload.skus, strict=True):
        assert queued.kwargs["args"] == [
            retry_id,
            str(TENANT_ID),
            sku.model_dump(mode="json"),
            payload.image_model.value,
            payload.video_model.value,
        ]
        assert queued.kwargs["queue"] == payload.priority
    assert await store.get_job(original_id) == original
    assert await store.get_assets_for_job(original_id) == original_assets


@pytest.mark.asyncio
async def test_retry_task_can_recover_after_queue_publish_failure(client, monkeypatch):
    store = client.store
    payload = _make_payload()
    sku_id = payload.skus[0].sku_id
    original_id = await store.create_job(str(TENANT_ID), payload)
    await store.complete_job_item(original_id, sku_id, completed_assets=0, failed_assets=1)
    publish = Mock(side_effect=OperationalError("Queue unavailable"))
    monkeypatch.setattr(dispatcher.process_sku, "apply_async", publish)

    first = client.post(f"/api/v1/tasks/{original_id}/retry")
    assert first.status_code == 202
    retry_id = first.json()["job_id"]
    retry = await store.get_job(retry_id)
    assert retry["status"] == "failed"
    assert retry["done_skus"] == 1
    assert not await store.start_job_item(retry_id, sku_id)

    publish.side_effect = None
    second = client.post(f"/api/v1/tasks/{retry_id}/retry")
    assert second.status_code == 202
    assert second.json()["job_id"] != retry_id
    assert publish.call_count == 2


@pytest.mark.asyncio
async def test_repair_processes_only_the_selected_failed_image_in_place(client, monkeypatch):
    store = client.store
    payload = _make_payload()
    sku = payload.skus[0]
    sku.output_types.append("detail_page")
    job_id = await store.create_job(str(TENANT_ID), payload)
    asset_ids = {}
    for output_type, platform, status in [
        ("main_image", "amazon", AssetStatus.success),
        ("detail_page", "amazon:scene", AssetStatus.success),
        ("detail_page", "amazon:feature", AssetStatus.failed),
        ("detail_page", "amazon:closeup", AssetStatus.failed),
    ]:
        asset_ids[platform] = await store.add_asset(
            job_id=job_id, tenant_id=str(TENANT_ID), sku_id=sku.sku_id,
            output_type=output_type, platform=platform, model_used="test",
            status=status, file_path=f"/{platform}.png",
        )
    await store.complete_job_item(job_id, sku.sku_id, completed_assets=2, failed_assets=2)
    publish = Mock()
    monkeypatch.setattr(worker_tasks.process_sku, "apply_async", publish)
    path = f"/api/v1/tasks/{job_id}/repair-images"

    assert client.post(path, json={"asset_ids": [asset_ids["amazon"]]}).status_code == 409
    request = {"asset_ids": [asset_ids["amazon:closeup"]], "instruction": "Focus on the seam"}
    response = client.post(path, json=request)
    assert response.status_code == 202, response.text
    assert response.json() == {"queued_images": 1, "unqueued_images": 0}
    assert client.post(path, json=request).status_code == 409
    assert publish.call_count == 1
    assert (await store.get_job(job_id))["status"] == "running"

    monkeypatch.setattr(worker_tasks, "_get_store", AsyncMock(return_value=store))
    monkeypatch.setattr(nodes, "get_image_provider", AsyncMock(return_value=None))
    monkeypatch.setattr(nodes, "get_vector_store", Mock(return_value=None))
    monkeypatch.setattr(
        BaseImageWorker, "prepare_generation_context",
        AsyncMock(return_value=GenerationContext(input_matches=True)),
    )
    calls = []

    async def generate(worker, **kwargs):
        calls.append((worker.output_type, kwargs["variant"], worker.repair_instruction))
        return AssetOutput(
            sku_id=sku.sku_id, output_type=worker.output_type, platform="amazon:closeup",
            model_used="test", status="success", file_path="/repaired.png",
        )

    monkeypatch.setattr(BaseImageWorker, "_generate_and_save", generate)
    queued = publish.call_args.kwargs["args"]
    result = await asyncio.to_thread(worker_tasks.process_sku.run, *queued)
    assert result["status"] == "partial_fail"
    assert calls == [("detail_page", "closeup", "Focus on the seam")]
    assert (await asyncio.to_thread(worker_tasks.process_sku.run, *queued))["status"] == "skipped"
    detail = client.get(f"/api/v1/tasks/{job_id}").json()
    assert detail["job_items"][0]["completed_assets"] == 3
    assert detail["job_items"][0]["failed_assets"] == 1
    assert {asset["asset_id"] for asset in detail["assets"]} >= set(asset_ids.values())
    assert client.post(path, json=request).status_code == 409


@pytest.mark.asyncio
async def test_repair_queue_failure_restores_terminal_counts_and_can_be_retried(client, monkeypatch):
    store = client.store
    payload = _make_payload()
    sku_id = payload.skus[0].sku_id
    job_id = await store.create_job(str(TENANT_ID), payload)
    asset_id = await store.add_asset(
        job_id=job_id, tenant_id=str(TENANT_ID), sku_id=sku_id,
        output_type="main_image", platform="amazon", model_used="test", status=AssetStatus.failed,
    )
    await store.complete_job_item(job_id, sku_id, completed_assets=0, failed_assets=1)
    publish = Mock(side_effect=OperationalError("Queue unavailable"))
    monkeypatch.setattr(worker_tasks.process_sku, "apply_async", publish)

    path = f"/api/v1/tasks/{job_id}/repair-images"
    request = {"asset_ids": [asset_id]}
    assert client.post(path, json=request).status_code == 503
    assert (await store.get_job(job_id))["status"] == "failed"
    assert (await store.get_job_items(job_id))[0]["failed_assets"] == 1
    publish.side_effect = None
    assert client.post(path, json=request).status_code == 202
    assert not await store.start_job_item(job_id, sku_id, repair_id="stale")


@pytest.mark.asyncio
async def test_list_tasks_requires_auth():
    """No dependency overrides -> real auth gate kicks in and returns 401."""
    response = TestClient(app).get("/api/v1/tasks")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_list_tasks_returns_jobs_for_tenant(client):
    store = client.store
    payload = _make_payload()
    payload.skus.append(payload.skus[0].model_copy(update={
        "sku_id": "SKU-002", "target_platforms": ["amazon", "pinduoduo"],
    }))
    auth_job_ids = []
    for _ in range(3):
        job_id = await store.create_job(tenant_id=str(TENANT_ID), payload=payload)
        auth_job_ids.append(job_id)
        await asyncio.sleep(0.01)

    for _ in range(2):
        await store.create_job(tenant_id=str(OTHER_TENANT_ID), payload=payload)

    response = client.get("/api/v1/tasks", headers={"X-Api-Key": "test-api-key-pro"})
    assert response.status_code == 200
    data = response.json()
    jobs = data["jobs"]
    assert isinstance(jobs, list)
    assert len(jobs) == 3
    assert {job["job_id"] for job in jobs} == set(auth_job_ids)

    timestamps = [datetime.fromisoformat(job["created_at"]) for job in jobs]
    assert all(timestamps[i] >= timestamps[i + 1] for i in range(len(timestamps) - 1))

    for job in jobs:
        assert job["status"] == "pending"
        assert job["total_skus"] == 2
        assert job["done_skus"] == 0
        assert job["platforms"] == ["amazon", "pinduoduo"]
        assert "input_data" not in job
        assert datetime.fromisoformat(job["created_at"])


@pytest.mark.asyncio
async def test_list_tasks_limit_is_respected(client):
    store = client.store
    payload = _make_payload()
    for _ in range(3):
        await store.create_job(tenant_id=str(TENANT_ID), payload=payload)
        await asyncio.sleep(0.01)

    response = client.get(
        "/api/v1/tasks?limit=2", headers={"X-Api-Key": "test-api-key-pro"}
    )
    assert response.status_code == 200
    assert len(response.json()["jobs"]) == 2


@pytest.mark.asyncio
async def test_list_tasks_limit_above_max_returns_422(client):
    response = client.get(
        "/api/v1/tasks?limit=101", headers={"X-Api-Key": "test-api-key-pro"}
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_list_tasks_invalid_tenant_id_returns_400(client):
    class _BadStore:
        async def list_jobs_for_tenant(self, *a, **kw):
            raise ValueError("bad tenant id")

    invalid_tenant = Tenant(
        tenant_id=UUID("00000000-0000-0000-0000-000000000099"),
        name="Bad Tenant",
        api_key_hash="",
        plan=TenantPlan.pro,
    )

    async def override_invalid_tenant(_principal=Depends(lambda: None)):
        return invalid_tenant

    app.dependency_overrides[get_tenant_only] = override_invalid_tenant
    app.dependency_overrides[get_job_store] = lambda: _BadStore()
    try:
        response = client.get("/api/v1/tasks", headers={"X-Api-Key": "test-api-key-pro"})
        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid tenant ID"
    finally:
        app.dependency_overrides.clear()
