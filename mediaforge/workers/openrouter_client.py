import asyncio
import base64
from dataclasses import dataclass
from typing import Literal, TypeVar

import httpx
from loguru import logger
from pydantic import SecretStr

from mediaforge.config import get_settings
from mediaforge.workers.provider_http import PublicProviderTransport

StructuredResult = TypeVar("StructuredResult")

MAX_RETRIES = 3
RETRY_BACKOFF = 2.0
RETRYABLE_STATUS = {502, 503, 504, 429}


def _safe_payload_summary(payload: dict) -> str:
    """Return request metadata without prompts, user content, URLs or image bytes."""
    import json

    messages = payload.get("messages", [])
    image_count = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            image_count += sum(1 for item in content if item.get("type") == "image_url")
    image_count += sum(
        1 for item in payload.get("input_references", []) if item.get("type") == "image_url"
    )
    return json.dumps(
        {
            "model": payload.get("model"),
            "message_count": len(messages),
            "roles": [message.get("role") for message in messages],
            "tool_names": [
                tool.get("function", {}).get("name") for tool in payload.get("tools", [])
            ],
            "image_count": image_count,
            "stream": bool(payload.get("stream")),
        },
        ensure_ascii=False,
    )


async def _retry_request(client: httpx.AsyncClient, url: str, headers: dict, payload: dict) -> dict:
    """POST with automatic retry on 5xx / rate-limit / network errors."""
    from mediaforge.workers.circuit_breaker import CircuitOpenError, circuit_breaker

    breaker = circuit_breaker("openrouter")
    if not breaker.allow_request():
        raise CircuitOpenError(breaker.name)

    model = payload.get("model", "?")
    logger.info(
        "→ OpenRouter  model={}  url={}\n  REQ {}", model, url, _safe_payload_summary(payload)
    )
    for attempt in range(1, MAX_RETRIES + 1):
        t0 = asyncio.get_running_loop().time()
        try:
            resp = await client.post(url, headers=headers, json=payload)
            elapsed = asyncio.get_running_loop().time() - t0
            if resp.status_code in RETRYABLE_STATUS:
                raise httpx.HTTPStatusError(
                    f"Retryable status {resp.status_code}",
                    request=resp.request,
                    response=resp,
                )
            resp.raise_for_status()
            data = resp.json()
            usage = data.get("usage", {})
            logger.info(
                "← OpenRouter  model={}  status={}  {:.2f}s  tokens={}",
                model,
                resp.status_code,
                elapsed,
                usage.get("total_tokens", "?"),
            )
            breaker.record_success()
            return data
        except (
            httpx.HTTPStatusError,
            httpx.ConnectError,
            httpx.ReadTimeout,
            httpx.WriteTimeout,
        ) as exc:
            elapsed = asyncio.get_running_loop().time() - t0
            err_body = ""
            if isinstance(exc, httpx.HTTPStatusError):
                try:
                    err_body = exc.response.text[:500]
                except Exception:
                    pass
            status_code = (
                exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            )
            can_retry = status_code is None or status_code in RETRYABLE_STATUS
            if attempt == MAX_RETRIES or not can_retry:
                logger.error(
                    "← OpenRouter  model={}  FAILED after {} attempts  {:.2f}s  {}\n  {}",
                    model,
                    attempt,
                    elapsed,
                    exc,
                    err_body,
                )
                if can_retry:
                    breaker.record_failure()
                if isinstance(exc, httpx.HTTPStatusError):
                    raise RuntimeError(
                        f"OpenRouter request failed ({status_code}): {err_body or str(exc)}"
                    ) from exc
                raise
            wait = RETRY_BACKOFF * (2 ** (attempt - 1))
            logger.warning(
                "← OpenRouter  model={}  attempt {}/{}  status={}  retrying in {:.1f}s\n  {}",
                model,
                attempt,
                MAX_RETRIES,
                exc,
                wait,
                err_body,
            )
            await asyncio.sleep(wait)
    raise RuntimeError("Unreachable")


def _build_image_models() -> dict[str, str]:
    s = get_settings()
    return {
        "pro": s.image_model_pro,
        "fast": s.image_model_fast,
        "base": s.image_model_base,
    }


def _build_video_models() -> dict[str, str]:
    s = get_settings()
    return {"veo": s.video_model_veo, "seedance": s.video_model_seedance}


def get_image_models() -> dict[str, str]:
    return _build_image_models()


def get_video_models() -> dict[str, str]:
    return _build_video_models()


_VIDEO_SEMAPHORES: dict[str, asyncio.Semaphore] | None = None
_IMAGE_SEMAPHORES: dict[str, asyncio.Semaphore] | None = None


def _get_video_semaphores() -> dict[str, asyncio.Semaphore]:
    global _VIDEO_SEMAPHORES
    if _VIDEO_SEMAPHORES is None:
        s = get_settings()
        _VIDEO_SEMAPHORES = {
            s.video_model_veo: asyncio.Semaphore(s.semaphore_veo),
            s.video_model_seedance: asyncio.Semaphore(s.semaphore_seedance),
        }
    return _VIDEO_SEMAPHORES


def _get_image_semaphores() -> dict[str, asyncio.Semaphore]:
    global _IMAGE_SEMAPHORES
    if _IMAGE_SEMAPHORES is None:
        s = get_settings()
        _IMAGE_SEMAPHORES = {
            s.image_model_pro: asyncio.Semaphore(s.semaphore_gemini_pro_image),
            s.image_model_fast: asyncio.Semaphore(s.semaphore_gpt_image),
            s.image_model_base: asyncio.Semaphore(s.semaphore_gpt_image),
        }
    return _IMAGE_SEMAPHORES


@dataclass(frozen=True)
class ImageProvider:
    base_url: str
    api_format: str
    model_alias: str
    model_id: str
    api_key: SecretStr


class OpenRouterClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        *,
        image_provider: ImageProvider | None = None,
    ):
        s = get_settings()
        self.api_key = api_key or s.openrouter_api_key
        self.base_url = (base_url or s.openrouter_base_url).rstrip("/")
        self.image_base_url = (s.openrouter_image_base_url or self.base_url).rstrip("/")
        self.api_format = s.openrouter_api_format
        self.image_provider = image_provider

    def _headers(self) -> dict:
        s = get_settings()
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": s.site_url,
            "X-Title": s.site_title,
        }

    def model_name(self, alias: str) -> str:
        if self.image_provider and alias == self.image_provider.model_alias:
            return self.image_provider.model_id
        return get_image_models().get(alias, alias)

    async def chat_completion(
        self,
        messages: list[dict],
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> str:
        """Generic chat completion through retry + circuit breaker."""
        s = get_settings()
        payload = {
            "model": model or s.agent_model,
            "messages": messages,
        }
        if self.api_format == "openai":
            payload["max_completion_tokens"] = max_tokens
        else:
            payload.update(max_tokens=max_tokens, temperature=temperature)
        client = await self._get_client()
        data = await _retry_request(
            client,
            f"{self.base_url}/chat/completions",
            self._headers(),
            payload,
        )
        content = data["choices"][0]["message"].get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            text = "".join(
                str(item.get("text", ""))
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            ).strip()
            if text:
                return text
        raise ValueError("Chat completion returned no text content")

    async def structured_chat_completion(
        self,
        messages: list[dict],
        schema: type[StructuredResult],
        model: str | None = None,
        max_tokens: int = 800,
    ) -> StructuredResult:
        """Call OpenRouter with provider-enforced JSON Schema output.

        The OpenRouter SDK receives the same dedicated async client used by
        image generation. Its explicit proxy is isolated from the process and
        therefore cannot affect MinIO, PostgreSQL, Redis, or internal HTTP.
        """
        settings = get_settings()
        if self.api_format == "openai":
            from langchain_openai import ChatOpenAI

            llm = ChatOpenAI(
                model=model or settings.quality_model,
                api_key=self.api_key,
                base_url=self.base_url,
                http_async_client=await self._get_client(),
                max_completion_tokens=max_tokens,
                max_retries=0,
            )
            structured_llm = llm.with_structured_output(schema, method="json_schema", strict=True)
            return await structured_llm.ainvoke(messages)

        import openrouter
        from langchain_openrouter import ChatOpenRouter

        sync_client = httpx.Client(
            timeout=180.0,
            proxy=settings.http_proxy or None,
            trust_env=False,
            follow_redirects=True,
        )
        sdk_client = openrouter.OpenRouter(
            api_key=self.api_key,
            server_url=self.base_url,
            http_referer=settings.site_url,
            x_open_router_title=settings.site_title,
            client=sync_client,
            async_client=await self._get_client(),
        )
        llm = ChatOpenRouter(
            model=model or settings.quality_model,
            api_key=self.api_key,
            base_url=self.base_url,
            client=sdk_client,
            temperature=0,
            max_tokens=max_tokens,
            max_retries=2,
            reasoning={"effort": "low"},
            openrouter_provider={"require_parameters": True},
        )
        structured_llm = llm.with_structured_output(
            schema,
            method="json_schema",
            include_raw=True,
            strict=True,
        )
        try:
            result = await structured_llm.ainvoke(messages)
        finally:
            sync_client.close()

        parsed = result.get("parsed")
        if parsed is None:
            raw = result.get("raw")
            finish_reason = getattr(raw, "response_metadata", {}).get("finish_reason")
            parsing_error = result.get("parsing_error")
            raise ValueError(
                "OpenRouter structured output failed"
                f" (finish_reason={finish_reason}): {parsing_error}"
            )
        return parsed

    @staticmethod
    async def _encode_image(image_path: str | bytes, max_px: int = 1024) -> str:
        """Read a local or HTTP(S) image, resize it, and return base64 JPEG."""
        import io

        from PIL import Image as _Image

        if isinstance(image_path, bytes):
            source = io.BytesIO(image_path)
        elif image_path.startswith("/api/v1/files/"):
            from mediaforge.storage import get_storage

            object_key = image_path.removeprefix("/api/v1/files/")
            data = await get_storage().get_internal(object_key)
            if data is None:
                raise FileNotFoundError(f"MinIO object not found: {object_key}")
            source = io.BytesIO(data)
        elif image_path.startswith(("http://", "https://")):
            async with httpx.AsyncClient(timeout=60.0, trust_env=False) as client:
                response = await client.get(image_path, follow_redirects=True)
            response.raise_for_status()
            source = io.BytesIO(response.content)
        else:
            source = image_path

        def _resize(source, max_px: int) -> bytes:
            with _Image.open(source) as img:
                img = img.convert("RGB")
                w, h = img.size
                if max(w, h) > max_px:
                    ratio = max_px / max(w, h)
                    img = img.resize((int(w * ratio), int(h * ratio)), _Image.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=85)
                return buf.getvalue()

        raw = await asyncio.to_thread(_resize, source, max_px)
        return base64.b64encode(raw).decode("utf-8")

    async def _image_payload(
        self,
        prompt: str,
        model: Literal["pro", "fast", "base"],
        size: str = "2K",
        aspect_ratio: str = "1:1",
        product_image_path: str | None = None,
        ref_image_path: str | None = None,
    ) -> dict:
        full_model = self.model_name(model)
        provider = self.image_provider if self.image_provider and model == self.image_provider.model_alias else None
        api_format = provider.api_format if provider else self.api_format
        references: list[dict] = []
        reference_notes: list[str] = []
        # Image 1: new product photo (if provided)
        if product_image_path:
            reference_notes.append(
                "Reference Image 1 is the authoritative SOURCE PRODUCT IMAGE. Preserve its "
                "category, silhouette, construction, color, material, patterns and distinctive "
                "details exactly."
            )
            b64 = await self._encode_image(product_image_path, max_px=1024)
            references.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                }
            )
        # Image 2: style reference photo (if provided)
        if ref_image_path:
            reference_number = len(references) + 1
            reference_notes.append(
                f"Reference Image {reference_number} is the STYLE REFERENCE IMAGE. Use only its "
                "composition, lighting, background, camera angle and mood. Never copy or "
                "substitute its product."
            )
            b64 = await self._encode_image(ref_image_path, max_px=1024)
            references.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                }
            )

        payload = {
            "model": full_model,
            "prompt": "\n\n".join([*reference_notes, prompt]),
            "n": 1,
            "aspect_ratio": aspect_ratio,
        }
        if references:
            payload["input_references"] = references
        if api_format == "openai" or full_model == "openai/gpt-image-2":
            payload["quality"] = {"1K": "low", "2K": "medium", "4K": "high"}.get(
                size, "medium"
            )
        else:
            payload["resolution"] = size
        return payload

    async def _get_client(self) -> httpx.AsyncClient:
        from mediaforge.http_clients import get_openrouter_client

        return await get_openrouter_client()

    async def generate_image(
        self,
        prompt: str,
        model: Literal["pro", "fast", "base"] = "pro",
        size: str = "2K",
        aspect_ratio: str = "1:1",
        product_image_path: str | None = None,
        ref_image_path: str | None = None,
    ) -> bytes:
        full_model = get_image_models()[model]
        sem = _get_image_semaphores()[full_model]
        async with sem:
            payload = await self._image_payload(
                prompt, model, size, aspect_ratio, product_image_path, ref_image_path
            )
            if self.image_provider and self.image_provider.model_alias == model:
                return await self._request_custom_image(self.image_provider, payload)
            client = await self._get_client()
            if self.api_format == "openai":
                return await self._request_openai_image(client, payload)
            data = await _retry_request(
                client, f"{self.image_base_url}/images", self._headers(), payload
            )
        return self._extract_image_bytes(data)

    async def _request_custom_image(self, provider: ImageProvider, payload: dict) -> bytes:
        headers = {"Authorization": f"Bearer {provider.api_key.get_secret_value()}"}
        try:
            async with httpx.AsyncClient(
                transport=PublicProviderTransport(), follow_redirects=False, trust_env=False, timeout=180.0
            ) as client:
                if provider.api_format == "openai":
                    return await self._request_openai_image(
                        client, payload, base_url=provider.base_url, headers=headers
                    )
                response = await client.post(f"{provider.base_url}/images", headers=headers, json=payload)
                response.raise_for_status()
                return await self._decode_custom_image(client, response.json())
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in (408, 504, 524):
                message = f"图片生成服务超时（HTTP {status}），请先查看中转用量再决定是否重试"
            elif status in (401, 403):
                message = f"图片生成服务拒绝访问（HTTP {status}），请检查 API Key 和模型权限"
            else:
                message = f"图片生成服务请求失败（HTTP {status}），请检查接口地址、协议和模型 ID"
            raise RuntimeError(message) from None
        except httpx.TimeoutException:
            raise RuntimeError("图片生成服务超时，请先查看中转用量再决定是否重试") from None
        except (httpx.HTTPError, httpx.InvalidURL, ValueError, TypeError, AttributeError, KeyError):
            raise RuntimeError("无法读取图片生成服务结果，请检查接口地址、模型和返回格式") from None

    async def _request_openai_image(
        self, client: httpx.AsyncClient, payload: dict, *,
        base_url: str | None = None, headers: dict | None = None,
    ) -> bytes:
        custom = base_url is not None
        base_url = base_url or self.image_base_url
        if headers is None:
            headers = self._headers()
            del headers["Content-Type"]
        width, height = (int(part) for part in payload["aspect_ratio"].split(":"))
        provider_width, provider_height = min(width, 3 * height), min(height, 3 * width)
        needs_crop = (provider_width, provider_height) != (width, height)
        scale = 1024 / min(provider_width, provider_height)
        prompt = payload["prompt"]
        if needs_crop:
            axis = "height" if width > height else "width"
            prompt += f"\n\nKeep the entire product and key details inside the central 70% of the {axis}; leave room for cropping."
        fields = {
            "model": payload["model"],
            "prompt": prompt,
            "n": payload["n"],
            "size": f"{round(provider_width * scale / 16) * 16}x{round(provider_height * scale / 16) * 16}",
            "quality": payload["quality"],
        }
        references = payload.get("input_references", [])
        if references:
            files = [
                (
                    "image[]",
                    (
                        f"reference-{number}.jpg",
                        base64.b64decode(reference["image_url"]["url"].split(",", 1)[1]),
                        "image/jpeg",
                    ),
                )
                for number, reference in enumerate(references, 1)
            ]
            response = await client.post(
                f"{base_url}/images/edits", headers=headers, data=fields, files=files
            )
        else:
            response = await client.post(
                f"{base_url}/images/generations", headers=headers, json=fields
            )
        if response.status_code == 524:
            raise httpx.HTTPStatusError(
                "出图网关超时（HTTP 524）。本次出图和计费结果未知，请先查看中转用量，再按需重试。",
                request=response.request,
                response=response,
            )
        response.raise_for_status()
        data = response.json()
        image_bytes = await self._decode_custom_image(client, data) if custom else self._extract_image_bytes(data)
        if not needs_crop:
            return image_bytes

        import io

        from PIL import Image, ImageOps

        output = io.BytesIO()
        with Image.open(io.BytesIO(image_bytes)) as image:
            crop_scale = min(image.width // width, image.height // height)
            cropped = ImageOps.fit(image, (width * crop_scale, height * crop_scale))
            cropped.save(output, format=image.format)
        return output.getvalue()

    @staticmethod
    async def _decode_custom_image(client: httpx.AsyncClient, data: dict) -> bytes:
        try:
            return OpenRouterClient._extract_image_bytes(data)
        except ValueError:
            url = next((item.get("url") for item in data.get("data") or [] if item.get("url")), None)
            if url is None:
                raise
        # Image URLs may be signed and belong to a separate CDN. Send no API key.
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            image = bytearray()
            async for chunk in response.aiter_bytes():
                if len(image) + len(chunk) > 32 * 1024 * 1024:
                    raise ValueError("Image response exceeds the size limit")
                image.extend(chunk)
        return bytes(image)

    @staticmethod
    def _extract_image_bytes(data: dict) -> bytes:
        for item in data.get("data") or []:
            encoded = item.get("b64_json")
            if encoded:
                return base64.b64decode(encoded)
        for choice in data.get("choices", []):
            message = choice.get("message", {})
            # Primary location: message["images"] (Gemini via OpenRouter)
            for item in message.get("images") or []:
                if item.get("type") == "image_url":
                    url = item["image_url"]["url"]
                    if url.startswith("data:image"):
                        _, b64 = url.split(",", 1)
                        return base64.b64decode(b64)
            # Fallback: message["content"] list (other providers)
            for item in message.get("content") or []:
                if item.get("type") == "image_url":
                    url = item["image_url"]["url"]
                    if url.startswith("data:image"):
                        _, b64 = url.split(",", 1)
                        return base64.b64decode(b64)
        raise ValueError("No image in OpenRouter response")

    async def generate_video(
        self,
        prompt: str,
        model: Literal["veo", "seedance"] = "veo",
        duration: int = 5,
        aspect_ratio: str = "9:16",
        ref_image_path: str | None = None,
    ) -> str:
        full_model = get_video_models()[model]
        sem = _get_video_semaphores()[full_model]
        async with sem:
            content = []
            if ref_image_path:
                b64 = await self._encode_image(ref_image_path)
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"},
                    }
                )
            content.append({"type": "text", "text": prompt})

            payload = {
                "model": full_model,
                "modalities": ["video", "text"],
                "messages": [{"role": "user", "content": content}],
                "video_config": {"duration": duration, "aspect_ratio": aspect_ratio},
            }

            client = await self._get_client()
            data = await _retry_request(
                client,
                f"{self.base_url}/chat/completions",
                self._headers(),
                payload,
            )
        return self._extract_video_url(data)

    @staticmethod
    def _extract_video_url(data: dict) -> str:
        for choice in data.get("choices", []):
            message = choice.get("message", {})
            for item in message.get("content", []):
                if item.get("type") == "video_url":
                    return item["video_url"]["url"]
        raise ValueError("No video URL in OpenRouter response")
