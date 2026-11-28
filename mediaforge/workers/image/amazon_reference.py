import asyncio
from io import BytesIO

import httpx
from PIL import Image

from mediaforge.models.amazon_image_reference import amazon_image_url

MAX_REFERENCE_BYTES = 8 * 1024 * 1024
MAX_REFERENCE_PIXELS = 16_000_000


def _verify_image(data: bytes) -> None:
    with Image.open(BytesIO(data), formats=("JPEG", "PNG", "WEBP")) as image:
        if image.width * image.height > MAX_REFERENCE_PIXELS:
            raise ValueError("Amazon 参考图分辨率超过限制")
        image.verify()


async def load_amazon_reference(url: str) -> bytes:
    # 再校验一次，旧任务或补生成任务也不能通过普通 URL 跳转读取任意图片。
    url = amazon_image_url(url)
    if not url:
        raise ValueError("Amazon 参考图地址无效")
    try:
        async with asyncio.timeout(12), httpx.AsyncClient(
            follow_redirects=False, trust_env=False, timeout=8,
        ) as client, client.stream("GET", url) as response:
            response.raise_for_status()
            data = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                if len(data) + len(chunk) > MAX_REFERENCE_BYTES:
                    raise ValueError("Amazon 参考图超过 8 MB 限制")
                data.extend(chunk)
        image_bytes = bytes(data)
        await asyncio.to_thread(_verify_image, image_bytes)
        return image_bytes
    except (httpx.HTTPError, TimeoutError, OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ValueError("无法读取这张 Amazon 参考图，可重试或在创作表单更换参考图") from exc
