from typing import Annotated, Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, SecretStr, StringConstraints

from mediaforge.auth.tenant import TenantResolver
from mediaforge.config import get_settings
from mediaforge.db.engine import get_session
from mediaforge.db.image_provider import (
    decrypt_api_key,
    encrypt_api_key,
    get_image_provider,
    list_provider_profiles,
    lock_provider_settings,
)
from mediaforge.db.tables import TenantImageProviderTable
from mediaforge.gateway.dependencies import get_tenant_only
from mediaforge.models.job import ImageModelAlias
from mediaforge.models.tenant import Tenant
from mediaforge.workers.openrouter_client import get_image_models
from mediaforge.workers.provider_http import (
    ProviderAddressError,
    PublicProviderTransport,
    provider_url,
    resolve_public_address,
)


class _ProviderSettingsRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def validate(request: Request):
            try:
                response = await handler(request)
                response.headers["Cache-Control"] = "private, no-store"
                return response
            except RequestValidationError:
                # Validation diagnostics may contain the submitted key or raw JSON.
                raise HTTPException(422, "图片生成服务配置格式有误") from None

        return validate


router = APIRouter(prefix="/api/v1/image-provider", tags=["settings"], route_class=_ProviderSettingsRoute)
ProviderFormat = Literal["openai", "openrouter"]


class ProviderConnectionInput(BaseModel):
    base_url: str = ""
    api_format: ProviderFormat
    api_key: SecretStr | None = None


class ProviderSettingsInput(ProviderConnectionInput):
    model_alias: ImageModelAlias
    model_id: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] = ""


class ProviderProfileInput(ProviderSettingsInput):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class ProviderModelListInput(ProviderConnectionInput):
    provider_id: UUID | None = None


def _connection_url(body: ProviderConnectionInput) -> httpx.URL:
    default_base = "https://api.openai.com/v1" if body.api_format == "openai" else "https://openrouter.ai/api/v1"
    try:
        url = provider_url(body.base_url.strip().rstrip("/") or default_base)
        if url.path.endswith(("/images", "/images/generations", "/images/edits", "/chat/completions", "/models")):
            raise ValueError("请填写 API 根地址，例如 https://example.com/v1")
        return url
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


def _new_key(body: ProviderConnectionInput) -> SecretStr | None:
    key = body.api_key.get_secret_value().strip() if body.api_key is not None else ""
    if len(key) > 4096 or (key and not (key.isascii() and key.isprintable())):
        raise HTTPException(422, "API Key 格式有误")
    return SecretStr(key) if key else None


def _require_saved_key(provider: TenantImageProviderTable | None, url: httpx.URL) -> TenantImageProviderTable:
    if provider is None:
        raise HTTPException(422, "请填写图片生成服务的 API Key")
    previous = httpx.URL(provider.base_url)
    if (previous.host, previous.port) != (url.host, url.port):
        raise HTTPException(422, "更换中转域名或端口时，请重新填写 API Key")
    return provider


def _active_profile(profiles: list[TenantImageProviderTable]) -> TenantImageProviderTable | None:
    return next((profile for profile in profiles if profile.is_active), None)


def _find_profile(profiles: list[TenantImageProviderTable], provider_id: UUID) -> TenantImageProviderTable:
    for profile in profiles:
        if profile.provider_id == provider_id:
            return profile
    raise HTTPException(404, "图片服务配置不存在")


def _view(tenant: Tenant, profiles: list[TenantImageProviderTable]):
    connections = [
        {
            "provider_id": provider.provider_id,
            "name": provider.name,
            "is_active": provider.is_active,
            "base_url": provider.base_url,
            "api_format": provider.api_format,
            "model_alias": provider.model_alias,
            "model_id": provider.model_id,
        }
        for provider in profiles
    ]
    return {
        "connection": next((connection for connection in connections if connection["is_active"]), None),
        "profiles": connections,
        "models": [
            {"alias": alias, "model_id": model_id}
            for alias, model_id in get_image_models().items()
            if alias in tenant.quotas.allowed_models
        ],
    }


async def _settings_tenant(request: Request, tenant: Tenant = Depends(get_tenant_only)) -> Tenant:
    if not (request.cookies.get(get_settings().cookie_access_name) or request.headers.get("Authorization")):
        raise HTTPException(403, "请登录后管理图片生成服务")
    return tenant


@router.get("")
async def read_provider(tenant: Tenant = Depends(_settings_tenant)):
    async with get_session() as session:
        return _view(tenant, await list_provider_profiles(session, tenant.tenant_id))


@router.post("/models")
async def list_provider_models(body: ProviderModelListInput, tenant: Tenant = Depends(_settings_tenant)):
    url = _connection_url(body)
    key = _new_key(body)
    provider = None
    if body.provider_id is not None:
        provider = await get_image_provider(tenant.tenant_id, body.provider_id)
        if provider is None:
            raise HTTPException(404, "图片服务配置不存在")
    if key is None:
        if body.provider_id is None:
            provider = await get_image_provider(tenant.tenant_id)
        provider = _require_saved_key(provider, url)
        try:
            key = decrypt_api_key(provider)
        except RuntimeError as exc:
            raise HTTPException(422, str(exc)) from None
    path = "/models" if body.api_format == "openai" else "/images/models"
    secret = key.get_secret_value()
    try:
        async with httpx.AsyncClient(
            transport=PublicProviderTransport(), follow_redirects=False, trust_env=False, timeout=10.0
        ) as client:
            response = await client.get(
                f"{str(url).rstrip('/')}{path}", headers={"Authorization": f"Bearer {secret}"}
            )
            response.raise_for_status()
    except ProviderAddressError as exc:
        raise HTTPException(502, str(exc)) from None
    except httpx.TimeoutException:
        raise HTTPException(502, "连接图片生成服务超时，请检查网络后重试") from None
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in (401, 403):
            raise HTTPException(502, f"中转服务拒绝了模型列表请求（HTTP {status}），请检查 API Key 和权限") from None
        if status == 404 or 300 <= status < 400:
            raise HTTPException(502, "中转服务未返回模型接口，请核对 API 根地址（常见以 /v1 结尾）和接口协议") from None
        raise HTTPException(502, f"读取模型列表失败（HTTP {status}），可直接填写模型 ID") from None
    except httpx.RequestError:
        raise HTTPException(502, "无法连接图片生成服务，请检查 API 地址和网络") from None

    try:
        payload = response.json()
    except ValueError:
        payload = None
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise HTTPException(502, "中转服务未返回兼容的模型列表，请核对 API 根地址和接口协议")
    ids = {
        item["id"] for item in payload["data"]
        if isinstance(item, dict) and isinstance(item.get("id"), str)
        and 0 < len(item["id"]) <= 200 and secret not in item["id"]
    }
    return {"model_ids": sorted(ids)}


def _check_model(tenant: Tenant, alias: str) -> None:
    try:
        TenantResolver().check_model_allowed(tenant, alias)
    except ValueError:
        raise HTTPException(422, "当前套餐无法使用所选图片模型") from None


async def _save_provider(
    body: ProviderSettingsInput,
    tenant: Tenant,
    *,
    provider_id: UUID | None = None,
    new_profile: bool = False,
):
    alias = body.model_alias.value
    _check_model(tenant, alias)
    url = _connection_url(body)
    key = _new_key(body)
    try:
        await resolve_public_address(url)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None

    async with get_session() as session:
        profiles = await lock_provider_settings(session, tenant.tenant_id)
        if provider_id is not None:
            provider = _find_profile(profiles, provider_id)
        else:
            provider = None if new_profile else _active_profile(profiles)
        if key is None:
            _require_saved_key(provider, url)
        if provider is None:
            provider = TenantImageProviderTable(
                tenant_id=tenant.tenant_id,
                name=url.host[:80],
                is_active=not new_profile or not profiles,
            )
            session.add(provider)
            profiles.append(provider)
        if isinstance(body, ProviderProfileInput):
            provider.name = body.name
        provider.base_url = str(url).rstrip("/")
        provider.api_format = body.api_format
        provider.model_alias = alias
        default_model = get_image_models()[alias]
        if body.api_format == "openai":
            default_model = default_model.removeprefix("openai/")
        provider.model_id = body.model_id or default_model
        if key is not None:
            try:
                provider.encrypted_api_key = encrypt_api_key(tenant.tenant_id, key)
            except RuntimeError as exc:
                raise HTTPException(503, str(exc)) from None
        await session.flush()
        return _view(tenant, profiles)


@router.put("")
async def save_provider(body: ProviderSettingsInput, tenant: Tenant = Depends(_settings_tenant)):
    return await _save_provider(body, tenant)


@router.post("/profiles", status_code=201)
async def create_profile(body: ProviderProfileInput, tenant: Tenant = Depends(_settings_tenant)):
    return await _save_provider(body, tenant, new_profile=True)


@router.put("/profiles/{provider_id}")
async def update_profile(
    provider_id: UUID, body: ProviderProfileInput, tenant: Tenant = Depends(_settings_tenant)
):
    return await _save_provider(body, tenant, provider_id=provider_id)


@router.put("/profiles/{provider_id}/activate")
async def activate_profile(provider_id: UUID, tenant: Tenant = Depends(_settings_tenant)):
    async with get_session() as session:
        profiles = await lock_provider_settings(session, tenant.tenant_id)
        provider = _find_profile(profiles, provider_id)
        _check_model(tenant, provider.model_alias)
        current = _active_profile(profiles)
        if current is not provider:
            if current is not None:
                current.is_active = False
                await session.flush()
            provider.is_active = True
        return _view(tenant, profiles)


@router.delete("/profiles/{provider_id}")
async def delete_profile(provider_id: UUID, tenant: Tenant = Depends(_settings_tenant)):
    async with get_session() as session:
        profiles = await lock_provider_settings(session, tenant.tenant_id)
        provider = _find_profile(profiles, provider_id)
        await session.delete(provider)
        profiles.remove(provider)
        return _view(tenant, profiles)


@router.delete("", status_code=204)
async def reset_provider(tenant: Tenant = Depends(_settings_tenant)):
    async with get_session() as session:
        provider = _active_profile(await lock_provider_settings(session, tenant.tenant_id))
        if provider is not None:
            provider.is_active = False
