"""Best-effort, offline viewport capture of a fetched public Amazon product page."""

import asyncio
import base64
import os
import re

_CANONICAL_URL = re.compile(r"https://www\.amazon\.com/dp/[A-Z0-9]{10}")
_RENDER_SLOT = asyncio.Semaphore(1)
_MAX_HTML_BYTES = 8 * 1024 * 1024
_MAX_JPEG_BYTES = 300_000
_JPEG_PREFIX = "data:image/jpeg;base64,"
_DOCUMENT_HEADERS = {
    "content-type": "text/html; charset=utf-8",
    "content-security-policy": (
        "sandbox; default-src 'none'; style-src 'unsafe-inline'; "
        "base-uri 'none'; form-action 'none'"
    ),
    "x-dns-prefetch-control": "off",
}


async def capture_product_screenshot(url: str, html: bytes) -> str | None:
    """Render already-fetched HTML at its canonical US Amazon product URL.

    Return a JPEG data URL or None. This offline capture omits external assets
    and never substitutes a placeholder for a failed render.
    """
    if (not _CANONICAL_URL.fullmatch(url) or not html or len(html) > _MAX_HTML_BYTES
            or _RENDER_SLOT.locked()):
        return None

    try:
        from playwright.async_api import Error, async_playwright
    except ImportError:
        return None

    browser_env = {
        key: os.environ[key] for key in ("PATH", "HOME", "SYSTEMROOT", "TEMP", "TMP")
        if key in os.environ
    }
    try:
        async with _RENDER_SLOT, asyncio.timeout(10), async_playwright() as engine:
            browser = await engine.chromium.launch(
                env=browser_env,
                args=["--no-proxy-server", "--host-resolver-rules=MAP * ~NOTFOUND"],
                timeout=5000,
            )
            try:
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 900},
                    device_scale_factor=1,
                    java_script_enabled=False,
                    service_workers="block",
                    accept_downloads=False,
                    offline=True,
                )
                page = await context.new_page()
                served = False

                async def serve_document(route):
                    nonlocal served
                    request = route.request
                    if (not served and request.url == url and request.method == "GET"
                            and request.resource_type == "document"
                            and request.frame == page.main_frame):
                        served = True
                        await route.fulfill(status=200, headers=_DOCUMENT_HEADERS, body=html)
                    else:
                        await route.abort()

                await context.route("**/*", serve_document)
                response = await page.goto(url, wait_until="domcontentloaded", timeout=3000)
                if not served or response is None or not response.ok or page.url != url:
                    return None
                captcha = page.locator('form[action*="validateCaptcha" i], #captchacharacters')
                if await captcha.count():
                    return None
                title = page.locator("#productTitle").first
                if not await title.is_visible():
                    return None
                if not (await title.inner_text(timeout=1000)).strip():
                    return None
                await title.scroll_into_view_if_needed(timeout=1000)
                jpeg = await page.screenshot(
                    type="jpeg", quality=75, full_page=False, animations="disabled", timeout=2000,
                )
                if page.url == url and 0 < len(jpeg) <= _MAX_JPEG_BYTES:
                    return _JPEG_PREFIX + base64.b64encode(jpeg).decode("ascii")
                return None
            finally:
                async with asyncio.timeout(1):
                    await browser.close()
    except (Error, NotImplementedError, OSError, TimeoutError):
        return None
