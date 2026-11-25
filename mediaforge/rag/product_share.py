"""Read product titles and covers from supported share links."""

import asyncio
import base64
import io
import json
import re
from urllib.parse import parse_qs, unquote, urlsplit

import httpx
from PIL import Image

from mediaforge.rag import zwd

_SHORT_HOST = "v.douyin.com"
_PRODUCT_HOST = "haohuo.jinritemai.com"
_PRODUCT_PATH = "/ecommerce/trade/detail/index.html"
_PDD_HOSTS = {"mobile.yangkeduo.com", "yangkeduo.com", "p.pinduoduo.com", "mobile.pinduoduo.com"}
_URL = re.compile(r"https://[^\s<>「」【】（）。，！]+")
_IMAGE_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
_MAX_COVER_BYTES = 5 * 1024 * 1024


class ProductShareError(ValueError):
    pass


def _https_url(value: str):
    try:
        parsed = urlsplit(value)
        if parsed.scheme == "https" and parsed.hostname and parsed.netloc.lower() == parsed.hostname:
            return parsed
    except ValueError:
        pass
    raise ProductShareError("请粘贴有效的 HTTPS 商品分享链接。")


def _product_details(url: str) -> tuple[str, str, list]:
    parsed = _https_url(url)
    if parsed.hostname != _PRODUCT_HOST or parsed.path != _PRODUCT_PATH:
        raise ProductShareError("这条抖音链接没有跳转到商城商品页，请从商品页复制分享链接。")

    params = parse_qs(parsed.query)
    product_id = params.get("id", [""])[0]
    detail = params.get("goods_detail", [""])[0]
    try:
        product = json.loads(unquote(detail) if detail.startswith("%") else detail)
        title = product["title"]
        sources = product["img"]["url_list"]
        if not re.fullmatch(r"[0-9]{1,24}", product_id) or not isinstance(title, str) or not title.strip():
            raise ValueError()
        if not isinstance(sources, list) or not sources:
            raise ValueError()
    except (ValueError, KeyError, TypeError, RecursionError) as exc:
        raise ProductShareError("这条分享链接没有附带可读取的商品主图，请从抖音商城商品页重新复制分享。") from exc
    return f"DY-{product_id}", title.strip()[:500], sources


async def _image_data(client: httpx.AsyncClient, source: str, **options) -> str:
    async with client.stream("GET", source, **options) as response:
        response.raise_for_status()
        data = bytearray()
        async for chunk in response.aiter_bytes(64 * 1024):
            data.extend(chunk)
            if len(data) > _MAX_COVER_BYTES:
                raise ValueError("Product cover exceeds upload limit")
    with Image.open(io.BytesIO(data)) as photo:
        mime = _IMAGE_TYPES.get(photo.format)
        if not mime or photo.width * photo.height > 24_000_000:
            raise ValueError("Unsupported product cover")
        photo.verify()
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


async def _read_cover(client: httpx.AsyncClient, sources: list) -> str:
    for source in sources[:3]:
        if not isinstance(source, str):
            continue
        try:
            parsed = _https_url(source)
            if not re.fullmatch(r"[a-z0-9-]+\.ecombdimg\.com", parsed.hostname):
                continue
            if not parsed.path.startswith(("/img/ecom-shop-material/", "/obj/ecom-shop-material/")):
                continue
            return await _image_data(client, source)
        except (httpx.HTTPError, OSError, ValueError, SyntaxError, Image.DecompressionBombError):
            continue
    raise ProductShareError("商品主图暂时无法读取，请重新解析或上传商品图片。")


async def _preview_zwd(host: str, path: str) -> dict[str, str]:
    match = re.fullmatch(r"/item/([0-9]{1,24})/?", path)
    if not match:
        raise ProductShareError("请复制 17 网商品详情页的链接。")
    item_id = match[1]
    url = f"https://{host}/item/{item_id}"
    try:
        async with asyncio.timeout(20), httpx.AsyncClient(timeout=6, trust_env=False, follow_redirects=False) as client:
            title, sources = await zwd.read_product(url)
            for source in sources[:3]:
                if not zwd.is_product_image(source):
                    continue
                try:
                    data = await _image_data(client, source, headers={"Referer": url})
                    return {"product_id": f"17ZWD-{item_id}", "title": title, "image_data": data}
                except (httpx.HTTPError, OSError, ValueError, SyntaxError, Image.DecompressionBombError):
                    continue
    except zwd.PageUnavailable as exc:
        raise ProductShareError(str(exc)) from exc
    except TimeoutError as exc:
        raise ProductShareError("读取 17 网商品超时，请重试或手动补图。") from exc
    raise ProductShareError("17 网主图暂时无法读取，请重试或手动补图。")


async def preview_product_share(text: str) -> dict[str, str]:
    links = _URL.findall(text.replace(r"\:", ":"))
    for link in links:
        parsed = _https_url(link)
        if parsed.hostname in zwd.PRODUCT_HOSTS:
            return await _preview_zwd(parsed.hostname, parsed.path)
        if parsed.hostname in _PDD_HOSTS:
            raise ProductShareError("拼多多商品分享链接暂未接入。请使用「表格导入」添加商品图片。")
        if parsed.hostname in {_SHORT_HOST, _PRODUCT_HOST}:
            break
    else:
        raise ProductShareError("请粘贴抖音商城、拼多多或 17 网商品的分享链接，整段分享文案也可以。")

    try:
        async with asyncio.timeout(15), httpx.AsyncClient(timeout=6, trust_env=False, follow_redirects=False) as client:
            if parsed.hostname == _SHORT_HOST:
                short_path = parsed.path.rstrip("/")
                if not re.fullmatch(r"/[a-zA-Z0-9_-]{3,64}", short_path):
                    raise ProductShareError("抖音短链接格式有误，请重新复制商品分享链接。")
                async with client.stream("GET", f"https://{_SHORT_HOST}{short_path}/") as response:
                    link = response.headers.get("location", "") if response.is_redirect else ""
                if not link:
                    raise ProductShareError("抖音短链接无法打开，请重新复制商品分享链接。")
            sku, title, sources = _product_details(link)
            return {"product_id": sku, "title": title, "image_data": await _read_cover(client, sources)}
    except (TimeoutError, httpx.HTTPError) as exc:
        raise ProductShareError("读取商品链接超时或网络异常，请重试。") from exc
