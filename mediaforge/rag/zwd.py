"""Read the cover displayed in the public 17 网 product gallery."""

import asyncio
import os
import re
from urllib.parse import urlsplit

PRODUCT_HOSTS = {"17zwd.com", "www.17zwd.com", "gz.17zwd.com", "cs.17zwd.com", "st.17zwd.com", "xt.17zwd.com"}
_ASSET_HOST = "resource.17.shop"
_PHOTO_HOST = re.compile(r"img[0-9]+-gms\.17zwd\.com")
_RENDER_SLOTS = asyncio.Semaphore(2)

_GALLERY = """
() => {
    const gallery = document.querySelector('[class*="index-introSwiper-"]');
    const title = document.querySelector('[class*="index-titleBox-"]')?.textContent?.trim();
    if (!gallery || !title) return null;
    const selector = 'img[src*="/imgextra/"]';
    const cover = gallery.querySelector('[class*="index-bigImg-"] ' + selector);
    const images = [...new Set([cover, ...gallery.querySelectorAll(selector)]
        .filter(Boolean).map(image => image.src))];
    return images.length ? { title, images } : null;
}
"""


class PageUnavailable(ValueError):
    pass


def is_product_image(source: str) -> bool:
    try:
        url = urlsplit(source)
        return (
            url.scheme == "https"
            and bool(_PHOTO_HOST.fullmatch(url.netloc))
            and url.path.startswith("/imgextra/")
        )
    except ValueError:
        return False


def _allowed_request(url: str, method: str, resource: str, product_url: str) -> bool:
    if method != "GET":
        return False
    target = urlsplit(url)
    if target.scheme != "https":
        return False
    if resource == "document":
        return url == product_url
    if resource in {"script", "stylesheet"}:
        return target.netloc == _ASSET_HOST and target.path.startswith("/item/public/")
    if resource in {"fetch", "xhr"}:
        return target.netloc == urlsplit(product_url).netloc and target.path.startswith("/item/")
    return False


async def read_product(url: str) -> tuple[str, list[str]]:
    try:
        from playwright.async_api import Error, async_playwright
    except ImportError as exc:
        raise PageUnavailable("当前服务无法读取 17 网商品页，可以手动补图。") from exc

    page_error = "17 网暂时无法打开，请重试或手动补图。"
    async with _RENDER_SLOTS, async_playwright() as engine:
        try:
            # Keep application credentials out of the browser process.
            browser_env = {
                key: os.environ[key] for key in ("PATH", "HOME", "SYSTEMROOT", "TEMP", "TMP")
                if key in os.environ
            }
            browser = await engine.chromium.launch(env=browser_env, timeout=6000)
            try:
                context = await browser.new_context(service_workers="block", accept_downloads=False)

                async def allow_request(route):
                    nonlocal page_error
                    request = route.request
                    if not _allowed_request(request.url, request.method, request.resource_type, url):
                        await route.abort()
                        return
                    try:
                        response = await route.fetch(max_redirects=0, timeout=6000)
                    except Error:
                        await route.abort()
                        return
                    try:
                        if 300 <= response.status < 400:
                            location = response.headers.get("location", "")
                            if request.resource_type == "document" and "pp.17zwd.com/login" in location:
                                page_error = "17 网要求登录后查看该商品，可以打开商品页并补图。"
                            await route.abort()
                        else:
                            await route.fulfill(response=response)
                    finally:
                        await response.dispose()

                await context.route("**/*", allow_request)
                page = await context.new_page()
                response = await page.goto(url, wait_until="domcontentloaded", timeout=9000)
                if response is None or not response.ok:
                    raise PageUnavailable(page_error)
                page_error = "17 网商品页暂未显示主图，请重试或手动补图。"
                result = await page.wait_for_function(_GALLERY, timeout=6000)
                product = await result.json_value()
                return product["title"][:500], product["images"]
            finally:
                await browser.close()
        except Error as exc:
            raise PageUnavailable(page_error) from exc
