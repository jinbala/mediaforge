from fastapi import APIRouter, Request, Response

from mediaforge.config import get_settings
from mediaforge.db.image_provider import get_image_provider
from mediaforge.gateway.dependencies import get_current_principal
from mediaforge.workers.openrouter_client import get_image_models, get_video_models

router = APIRouter(prefix="/api/v1/models")


@router.get("")
async def list_models(request: Request, response: Response):
    """Return available image and video model aliases with display labels."""
    image_catalog = get_image_models()
    response.headers["Cache-Control"] = "private, no-store"
    default_image = "fast"
    authorization = request.headers.get("Authorization")
    api_key = request.headers.get("X-Api-Key")
    if request.cookies.get(get_settings().cookie_access_name) or authorization or api_key:
        _, tenant = await get_current_principal(request, authorization, api_key)
        provider = await get_image_provider(tenant.tenant_id)
        if provider and provider.model_alias in tenant.quotas.allowed_models:
            default_image = provider.model_alias
            image_catalog[default_image] = provider.model_id
    image_models = [
        {"alias": alias, "model_id": model_id, "label": model_id}
        for alias, model_id in image_catalog.items()
    ]
    video_models = [
        {"alias": alias, "model_id": model_id, "label": model_id}
        for alias, model_id in get_video_models().items()
    ]
    return {"image_models": image_models, "video_models": video_models, "default_image_model": default_image}
