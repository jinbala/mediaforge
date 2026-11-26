import asyncio
import base64
import json
import socket
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from loguru import logger
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import async_sessionmaker

from mediaforge.db import image_provider as store
from mediaforge.db.tables import TenantTable
from mediaforge.gateway.main import app
from mediaforge.gateway.routers import image_provider, models
from mediaforge.models.tenant import Tenant, TenantPlan
from mediaforge.workers import openrouter_client, provider_http
from mediaforge.workers.openrouter_client import ImageProvider, OpenRouterClient


@pytest.mark.asyncio
async def test_provider_settings_isolate_tenants_and_keep_key_private(db_engine, monkeypatch):
    sessions = async_sessionmaker(db_engine, expire_on_commit=False)
    tenant = Tenant(tenant_id=uuid4(), name="One", plan=TenantPlan.starter)
    other = Tenant(tenant_id=uuid4(), name="Two", plan=TenantPlan.starter)
    async with sessions.begin() as session:
        session.add_all([TenantTable(tenant_id=t.tenant_id, name=t.name, api_key_hash="unused") for t in (tenant, other)])

    def identify(request: image_provider.Request) -> Tenant:
        return tenant if request.headers["Authorization"] == "Bearer test-one" else other

    async def principal(request, *_):
        return None, identify(request)

    monkeypatch.setitem(app.dependency_overrides, image_provider._settings_tenant, identify)
    monkeypatch.setattr(models, "get_current_principal", principal)
    monkeypatch.setattr(store, "get_session", sessions.begin)
    monkeypatch.setattr(image_provider, "get_session", sessions.begin)
    monkeypatch.setattr(image_provider, "resolve_public_address", AsyncMock(return_value="8.8.8.8"))
    captured = []
    log_id = logger.add(lambda message: captured.append(str(message)))
    path = "/api/v1/image-provider"
    key = "fixture-image-key-do-not-log"
    alternate_key = "fixture-alternate-key-do-not-log"
    body = {"base_url": "https://relay.example/v1/", "api_format": "openai", "model_alias": "pro", "model_id": "custom/image-1"}

    def catalog(request):
        assert str(request.url) == "https://relay.example/v1/models"
        if request.headers["Authorization"] == "Bearer fixture-rejected-key":
            return httpx.Response(401, json={"error": "fixture-rejected-key"})
        if request.headers["Authorization"] == f"Bearer {alternate_key}":
            return httpx.Response(200, json={"data": [{"id": "custom/image-alternate"}]})
        assert request.headers["Authorization"] == f"Bearer {key}"
        return httpx.Response(200, json={"data": [{"id": "custom/image-1"}, {"id": "custom/image-2"}]})

    monkeypatch.setattr(image_provider, "PublicProviderTransport", lambda: httpx.MockTransport(catalog))
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver", headers={"Authorization": "Bearer test-one"}
        ) as client:
            assert (await client.put(path, json=body)).status_code == 422
            bad_shape = await client.put(path, json={**body, "api_key": {"secret": key}})
            assert bad_shape.status_code == 422 and key not in bad_shape.text
            response = await client.put(path, json={**body, "api_key": key})
            assert response.status_code == 200 and key not in response.text
            assert response.json()["connection"]["base_url"] == "https://relay.example/v1"
            assert (await client.post(f"{path}/models", json=body)).json()["model_ids"] == ["custom/image-1", "custom/image-2"]
            rejected = await client.post(f"{path}/models", json={**body, "api_key": "fixture-rejected-key"})
            assert rejected.status_code == 502 and "401" in rejected.json()["detail"]
            assert "fixture-rejected-key" not in rejected.text

            response = await client.get("/api/v1/models")
            assert response.json()["default_image_model"] == "pro"
            assert {item["alias"]: item["model_id"] for item in response.json()["image_models"]}["pro"] == "custom/image-1"
            assert response.headers["Cache-Control"] == "private, no-store"
            assert (await client.get(path, headers={"Authorization": "Bearer test-two"})).json()["connection"] is None
            assert (await client.get("/api/v1/models", headers={"Authorization": "Bearer test-two"})).json()["default_image_model"] == "fast"

            original = await store.get_image_provider(tenant.tenant_id)
            assert original.encrypted_api_key != key
            assert store.decrypt_api_key(original).get_secret_value() == key
            assert (await client.put(path, json={**body, "model_id": "custom/image-2"})).status_code == 200
            assert (await store.get_image_provider(tenant.tenant_id)).encrypted_api_key == original.encrypted_api_key
            for endpoint in (path, f"{path}/models"):
                response = await client.request("PUT" if endpoint == path else "POST", endpoint,
                                                json={**body, "base_url": "https://another.example/v1"})
                assert response.status_code == 422

            original_id = str(original.provider_id)
            new_profile = {**body, "name": "Alternate relay", "model_alias": "fast", "model_id": "custom/image-alternate"}
            assert (await client.post(f"{path}/profiles", json=new_profile)).status_code == 422
            response = await client.post(f"{path}/profiles", json={**new_profile, "api_key": alternate_key})
            assert response.status_code == 201 and alternate_key not in response.text and key not in response.text
            assert response.json()["connection"]["provider_id"] == original_id
            alternate = next(profile for profile in response.json()["profiles"] if profile["name"] == "Alternate relay")
            alternate_id = alternate["provider_id"]
            assert not alternate["is_active"]
            alternate_path = f"{path}/profiles/{alternate_id}"
            catalog_body = {**body, "provider_id": alternate_id}
            response = await client.post(f"{path}/models", json=catalog_body)
            assert response.json()["model_ids"] == ["custom/image-alternate"]

            other_headers = {"Authorization": "Bearer test-two"}
            assert (await client.get(path, headers=other_headers)).json()["profiles"] == []
            assert (await client.put(f"{alternate_path}/activate", headers=other_headers)).status_code == 404
            assert (await client.post(f"{path}/models", json=catalog_body, headers=other_headers)).status_code == 404
            assert (await client.delete(alternate_path, headers=other_headers)).status_code == 404

            response = await client.put(alternate_path, json={**new_profile, "name": "Backup"})
            assert response.status_code == 200 and response.json()["connection"]["provider_id"] == original_id
            response = await client.put(f"{alternate_path}/activate")
            assert response.json()["connection"]["name"] == "Backup"
            assert sum(profile["is_active"] for profile in response.json()["profiles"]) == 1
            current = await store.get_image_provider(tenant.tenant_id)
            assert store.decrypt_api_key(current).get_secret_value() == alternate_key
            catalog = (await client.get("/api/v1/models")).json()
            assert catalog["default_image_model"] == "fast"
            assert {item["alias"]: item["model_id"] for item in catalog["image_models"]}["fast"] == "custom/image-alternate"

            assert (await client.delete(path)).status_code == 204
            settings = (await client.get(path)).json()
            assert settings["connection"] is None and len(settings["profiles"]) == 2
            assert (await client.get("/api/v1/models")).json()["default_image_model"] == "fast"
            response = await client.put(f"{path}/profiles/{original_id}/activate")
            assert response.json()["connection"]["provider_id"] == original_id
            assert (await store.get_image_provider(tenant.tenant_id)).encrypted_api_key == original.encrypted_api_key
            response = await client.delete(alternate_path)
            assert [profile["provider_id"] for profile in response.json()["profiles"]] == [original_id]
    finally:
        logger.remove(log_id)
    assert all(key not in entry and alternate_key not in entry for entry in captured)


@pytest.mark.asyncio
@pytest.mark.parametrize("api_format,path", [("openai", "/v1/images/edits"), ("openrouter", "/v1/images")])
async def test_image_request_uses_configured_connection(monkeypatch, api_format, path):
    requests = []

    def respond(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, content=b"fixture-image")
        if api_format == "openai":
            return httpx.Response(200, json={"data": [{"url": "https://cdn.example/image?signature=test"}]})
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(b"fixture-image").decode()}]})

    monkeypatch.setattr(openrouter_client, "PublicProviderTransport", lambda: httpx.MockTransport(respond))
    monkeypatch.setattr(OpenRouterClient, "_encode_image", AsyncMock(return_value="aW1hZ2U="))
    connection = ImageProvider("https://relay.example/v1", api_format, "fast", "relay/image-custom", SecretStr("relay-key"))
    client = OpenRouterClient(api_key="system-key", image_provider=connection)
    assert await client.generate_image("Test", model="fast", product_image_path="fixture.jpg") == b"fixture-image"
    source = requests[0]
    assert source.url.host == "relay.example" and source.url.path == path
    assert source.headers["Authorization"] == "Bearer relay-key"
    assert b"relay/image-custom" in source.content
    assert client.model_name("fast") == "relay/image-custom"
    assert client._headers()["Authorization"] == "Bearer system-key"
    if api_format == "openai":
        assert b'name="image[]"' in source.content
        assert requests[1].url.host == "cdn.example" and "Authorization" not in requests[1].headers
    else:
        assert json.loads(source.content)["input_references"][0]["type"] == "image_url"


@pytest.mark.asyncio
async def test_provider_transport_pins_address_and_blocks_local_network(monkeypatch):
    with pytest.raises(ValueError, match="公网"):
        await provider_http.resolve_public_address(httpx.URL("https://127.0.0.1/"))

    async def send(_, request):
        assert request.url.host == "8.8.8.8"
        assert request.headers["Host"] == "relay.example:8443"
        assert request.extensions["sni_hostname"] == "relay.example"
        return httpx.Response(200)

    monkeypatch.setattr(provider_http, "resolve_public_address", AsyncMock(return_value="8.8.8.8"))
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", send)
    async with httpx.AsyncClient(transport=provider_http.PublicProviderTransport()) as client:
        response = await client.get("https://relay.example:8443/image?signature=test")
        assert response.status_code == 200 and response.request.url.host == "relay.example"


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["8.8.8.8", "192.168.1.1"])
async def test_fake_proxy_dns_checks_public_answer_before_sending_key(monkeypatch, answer):
    lookup = AsyncMock(return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.40", 443))])
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", lookup)
    requests = []

    async def send(_, request):
        requests.append(request)
        if str(request.url) == "https://1.1.1.1/dns-query?name=relay.example&type=1":
            assert "Authorization" not in request.headers
            return httpx.Response(200, json={"Status": 0, "Answer": [{"type": 1, "data": answer}]})
        assert request.url.host == "8.8.8.8"
        assert request.headers["Host"] == "relay.example"
        assert request.extensions["sni_hostname"] == "relay.example"
        assert request.headers["Authorization"] == "Bearer fixture-relay-key"
        return httpx.Response(200)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", send)
    async with httpx.AsyncClient(transport=provider_http.PublicProviderTransport()) as client:
        if answer == "8.8.8.8":
            response = await client.get("https://relay.example/models", headers={"Authorization": "Bearer fixture-relay-key"})
            assert response.status_code == 200
            assert len(requests) == 2
        else:
            with pytest.raises(ValueError, match="公网"):
                await client.get("https://relay.example/models", headers={"Authorization": "Bearer fixture-relay-key"})
            assert len(requests) == 1
