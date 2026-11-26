import base64
import hmac
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mediaforge.config import get_settings
from mediaforge.db.engine import get_session
from mediaforge.db.tables import TenantImageProviderTable, TenantTable


def _cipher(tenant_id: UUID) -> Fernet:
    secret = get_settings().jwt_secret
    if not secret:
        raise RuntimeError("图片生成服务缺少服务器加密密钥")
    key = hmac.digest(secret.encode(), b"mediaforge:tenant-image-provider:" + tenant_id.bytes, "sha256")
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_api_key(tenant_id: UUID, api_key: SecretStr) -> str:
    return _cipher(tenant_id).encrypt(api_key.get_secret_value().encode()).decode()


def decrypt_api_key(provider: TenantImageProviderTable) -> SecretStr:
    try:
        return SecretStr(_cipher(provider.tenant_id).decrypt(provider.encrypted_api_key.encode()).decode())
    except InvalidToken:
        raise RuntimeError("图片生成 API Key 无法解密，请在设置中重新填写") from None


async def get_image_provider(tenant_id: UUID, provider_id: UUID | None = None) -> TenantImageProviderTable | None:
    match = (
        TenantImageProviderTable.provider_id == provider_id
        if provider_id is not None else TenantImageProviderTable.is_active.is_(True)
    )
    async with get_session() as session:
        return await session.scalar(
            select(TenantImageProviderTable).where(TenantImageProviderTable.tenant_id == tenant_id, match)
        )


async def list_provider_profiles(session: AsyncSession, tenant_id: UUID) -> list[TenantImageProviderTable]:
    profiles = await session.scalars(
        select(TenantImageProviderTable)
        .where(TenantImageProviderTable.tenant_id == tenant_id)
        .order_by(TenantImageProviderTable.name, TenantImageProviderTable.provider_id)
    )
    return list(profiles)


async def lock_provider_settings(session: AsyncSession, tenant_id: UUID) -> list[TenantImageProviderTable]:
    # Serialize additions and switches even when the tenant has no saved profiles.
    await session.execute(
        select(TenantTable.tenant_id).where(TenantTable.tenant_id == tenant_id).with_for_update()
    )
    return await list_provider_profiles(session, tenant_id)
