import base64
import json
from io import BytesIO

import httpx
import pytest
from PIL import Image

from mediaforge.config import get_settings
from mediaforge.workers.openrouter_client import (
    OpenRouterClient,
    get_image_models,
    get_video_models,
)


@pytest.mark.asyncio
async def test_model_maps_contain_only_specified_models():
    settings = get_settings()
    assert get_image_models()["pro"] == settings.image_model_pro
    assert get_image_models()["fast"] == settings.image_model_fast
    assert get_video_models()["veo"] == settings.video_model_veo
    assert get_video_models()["seedance"] == settings.video_model_seedance


@pytest.mark.asyncio
async def test_aspect_ratio_parsing():
    client = OpenRouterClient(api_key="test")
    payload = await client._image_payload(
        prompt="p", model="pro", size="2K", aspect_ratio="1:1"
    )
    assert payload["model"] == get_settings().image_model_pro
    assert payload["aspect_ratio"] == "1:1"
    assert payload["resolution"] == "2K"
    assert "modalities" not in payload
    assert "messages" not in payload


def test_extract_dedicated_image_api_response():
    assert OpenRouterClient._extract_image_bytes(
        {"data": [{"b64_json": "aW1hZ2U="}]}
    ) == b"image"


@pytest.mark.parametrize(
    ("image_base_url", "image_host"),
    [("", "relay.example"), ("https://images.relay.example/v1/", "images.relay.example")],
)
@pytest.mark.asyncio
async def test_openai_image_format_with_and_without_references(
    monkeypatch, tmp_path, image_base_url, image_host
):
    settings = get_settings()
    monkeypatch.setattr(settings, "openrouter_api_format", "openai")
    monkeypatch.setattr(settings, "openrouter_image_base_url", image_base_url)
    monkeypatch.setattr(settings, "image_model_fast", "gpt-image-2")
    monkeypatch.setattr("mediaforge.workers.openrouter_client._IMAGE_SEMAPHORES", None)
    relay = OpenRouterClient(api_key="test", base_url="https://relay.example/v1")
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"data": [{"b64_json": "aW1hZ2U="}]})

    product = tmp_path / "product.jpg"
    style = tmp_path / "style.jpg"
    for path, color in [(product, "red"), (style, "blue")]:
        with Image.new("RGB", (16, 16), color) as image:
            image.save(path)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        async def get_client():
            return client

        monkeypatch.setattr(relay, "_get_client", get_client)
        assert await relay.generate_image("test prompt", model="fast") == b"image"
        assert (
            await relay.generate_image(
                "test prompt", model="fast", aspect_ratio="3:4",
                product_image_path=str(product), ref_image_path=str(style),
            )
            == b"image"
        )

    generated, edited = calls
    assert relay.base_url == "https://relay.example/v1"
    assert generated.url.host == edited.url.host == image_host
    assert generated.url.path == "/v1/images/generations"
    assert json.loads(generated.content) == {
        "model": "gpt-image-2", "n": 1, "prompt": "test prompt",
        "quality": "medium", "size": "1024x1024",
    }
    assert edited.url.path == "/v1/images/edits"
    assert edited.headers["content-type"].startswith("multipart/form-data;")
    assert edited.content.count(b'name="image[]"') == 2
    assert b"1024x1360" in edited.content
    assert edited.content.index(b"SOURCE PRODUCT IMAGE") < edited.content.index(b"STYLE REFERENCE IMAGE")
    parts = edited.content.split(b"Content-Type: image/jpeg\r\n\r\n")[1:]
    with Image.open(BytesIO(parts[0])) as image:
        assert image.getpixel((0, 0))[0] > 250
    with Image.open(BytesIO(parts[1])) as image:
        assert image.getpixel((0, 0))[2] > 250


@pytest.mark.asyncio
async def test_openai_image_timeout_reports_unknown_result_without_resending():
    relay = OpenRouterClient(api_key="test", base_url="https://relay.example/v1")
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(524, text="Gateway timeout")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(httpx.HTTPStatusError, match="HTTP 524.*本次出图和计费结果未知") as error:
            await relay._request_openai_image(
                client,
                {
                    "model": "gpt-image-2",
                    "prompt": "test prompt",
                    "n": 1,
                    "aspect_ratio": "1:1",
                    "quality": "medium",
                },
            )

    assert error.value.response.status_code == 524
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("aspect_ratio", "relay_size", "source_size", "output_size"),
    [
        ("4:1", "3072x1024", (96, 32), (96, 24)),
        ("1:4", "1024x3072", (32, 96), (24, 96)),
    ],
)
@pytest.mark.asyncio
async def test_openai_image_crops_extreme_ratios(aspect_ratio, relay_size, source_size, output_size):
    relay = OpenRouterClient(api_key="test", base_url="https://relay.example/v1")
    source = BytesIO()
    with Image.new("RGB", source_size) as image:
        image.save(source, format="PNG")

    def respond(request):
        payload = json.loads(request.content)
        assert payload["size"] == relay_size
        assert "leave room for cropping" in payload["prompt"]
        return httpx.Response(
            200, json={"data": [{"b64_json": base64.b64encode(source.getvalue()).decode()}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await relay._request_openai_image(
            client,
            {"model": "gpt-image-2", "prompt": "test prompt", "n": 1,
             "aspect_ratio": aspect_ratio, "quality": "low"},
        )

    with Image.open(BytesIO(result)) as image:
        assert image.size == output_size
