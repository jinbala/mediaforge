import asyncio
import hashlib
import re
from datetime import UTC, datetime
from time import monotonic
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from mediaforge.models.amazon_research_run import AmazonEvidence, AmazonStoreSample
from mediaforge.rag.amazon_research import (
    AmazonFetchError,
    AmazonSourceError,
    _fetch_page,
    _first_text,
    _linked_source,
    _parse_listings,
    _text,
    normalize_source,
)
from mediaforge.rag.amazon_store_collection import _currency


def normalize_board(source: str) -> str:
    # 深调研从榜单第一页取样；单页 API 保留原先 pg=2 的兼容行为。
    # 美国站导航把 ref= 跟踪码放在路径末端；只移除这一段，再走原有站点校验。
    source = re.sub(r"/ref=[^/?#]*(?=[?#]|$)", "", source)
    return normalize_source("bestsellers", source).split("?", 1)[0]


def board_identity(url: str) -> str:
    path = urlsplit(url).path.lower()
    suffix = re.split(r"/(?:gp/bestsellers|zgbs)(?:/|$)", path, maxsplit=1)[-1]
    # 两种榜单路径的数字节点 ID 相同；无 ID 时才按类目 slug 去重。
    tail = suffix.strip("/").split("/")
    return tail[-1] if tail[-1].isdigit() else "/".join(tail)


def board_step(url: str, title: str, role: str) -> AmazonEvidence:
    code = hashlib.sha256(board_identity(url).encode()).hexdigest()[:16]
    return AmazonEvidence(key="board-" + code, kind="board", url=url, title=title, role=role)


def _discover(soup: BeautifulSoup, current: str) -> list[tuple[str, str]]:
    # 只取当前类目下的导航分支，不把同栏的父类、兄弟类或榜单页脚误选为子类。
    root = soup.select_one(
        '#zg_browseRoot, #zg-browse-root, [class*="zg-browse-root"], [role="tree"]'
    )
    if root is None:
        return []
    selected = root.select_one(
        '[aria-current="page"], .zg_selected, [aria-selected="true"], [class*="zg-selected"]'
    )
    if selected is None:
        marker = root.find(string=re.compile(r"^\s*\(Current\)\s*$", re.I))
        selected = marker.parent if marker else None
    if selected is not None:
        branch = (
            selected
            if selected.name == "li" or selected.get("role") == "treeitem"
            else (
                selected.find_parent(["li", "div"], role="treeitem") or selected.find_parent("li")
            )
        )
        if branch is not None and branch is not root:
            children = branch.select_one('ul, [role="group"]')
            if children is None:
                next_node = branch.find_next_sibling()
                if next_node and (next_node.name == "ul" or next_node.get("role") == "group"):
                    children = next_node
            if children is None:
                # 现版导航将子列表放在选中条目的下一 li 中。
                sibling = branch.find_next_sibling("li")
                if sibling is not None:
                    children = sibling.select_one('ul[class*="zg-browse-group"]')
            if children is None:
                return []
            root = children
        else:
            next_node = selected.find_next_sibling()
            if next_node and (next_node.name == "ul" or next_node.get("role") == "group"):
                root = next_node
            else:
                return []
    links: dict[str, tuple[str, str]] = {}
    for link in root.select("a[href]", limit=120):
        if not (title := _text(link, 150)):
            continue
        try:
            url = normalize_board(urljoin(current, str(link["href"])))
        except (AmazonSourceError, ValueError):
            continue
        identity = board_identity(url)
        if identity != board_identity(current) and identity:
            links.setdefault(identity, (title, url))
    return list(links.values())


def _parse(item: AmazonEvidence, html: bytes, limit: int) -> None:
    soup = BeautifulSoup(html, "html.parser", from_encoding="utf-8")
    if soup.select_one('form[action*="validateCaptcha"], #captchacharacters') or soup.find(
        string=re.compile(r"Sorry, we just need to make sure you.re not a robot", re.I)
    ):
        raise AmazonFetchError("Amazon 返回访问验证。", "captcha", item.final_url)
    item.title = _first_text(soup, "h1", "title") or item.title
    if item.role == "main":
        item.links = _discover(soup, item.final_url or item.url)
    listings = _parse_listings(soup, "bestsellers", limit=limit + 1)
    card_details: dict[str, tuple[str | None, str | None, bool | None]] = {}
    for card in soup.select(".zg-grid-general-faceout, .zg-item-immersion", limit=100):
        link = card.select_one('a[href*="/dp/"], a[href*="/gp/product/"]')
        if link and (url := _linked_source("product", link)):
            brand = _first_text(
                card, '[data-cy="title-recipe"] .a-color-secondary, [data-brand], .p13n-sc-brand'
            )
            price_code = card.select_one('[itemprop="priceCurrency"]')
            explicit = str(price_code.get("content", "")) if price_code else None
            asin = url.rsplit("/", 1)[-1]
            old_brand, old_code, old_variants = card_details.get(asin, (None, None, None))
            # 只使用明确存在的颜色/尺寸选择器，不以标题里出现颜色词推断变体。
            swatches = card.select_one('[data-cy="variation-swatches"], .p13n-variation-swatches')
            card_details[asin] = (
                old_brand or brand,
                old_code or explicit,
                True if swatches else old_variants,
            )
    for row in listings[:limit]:
        brand, explicit, variants = card_details.get(row.asin, (None, None, None))
        item.products.append(
            AmazonStoreSample(
                **row.model_dump(),
                brand=brand,
                has_variants=variants,
                price_currency=_currency(row.price, explicit),
                price_label="榜单卡片公开报价" if row.price else None,
            )
        )
    item.observed_count = len(item.products)
    if len(listings) > limit:
        item.status, item.code = "incomplete", "sample_limit"
        item.reason = f"只保留榜单前 {limit} 个可识别 ASIN，未采集剩余排名。"
    elif item.products:
        item.status = "success"
    elif soup.find(string=re.compile(r"no (?:products|items|results) (?:found|available)", re.I)):
        item.status, item.code, item.reason = "empty", "no_products", "榜单公开页明确显示没有商品。"
    elif soup.select_one('[aria-busy="true"], [data-loading="true"], .a-spinner'):
        item.status, item.code, item.reason = (
            "incomplete",
            "dynamic",
            "公开榜单仅看到动态加载占位。",
        )
    else:
        item.status, item.code, item.reason = "incomplete", "markup", "未识别可用榜单卡片。"


async def collect_board(
    client: httpx.AsyncClient, previous: AmazonEvidence, limit: int
) -> AmazonEvidence:
    started = monotonic()
    item = previous.model_copy(
        update={
            "links": [],
            "products": [],
            "reason": None,
            "code": None,
            "final_url": None,
            "observed_count": None,
        }
    )
    for attempt in range(2):
        item.attempts += 1
        item.collected_at = datetime.now(UTC)
        try:
            item.final_url, html = await _fetch_page(client, "bestsellers", item.url)
            if item.role != "main" and board_identity(item.final_url) != board_identity(item.url):
                raise AmazonFetchError(
                    "链接跳转到其他榜单，未将其计入本子类样本。", "board_mismatch"
                )
            await asyncio.to_thread(_parse, item, html, limit)
            break
        except AmazonFetchError as exc:
            item.status, item.code, item.reason = "failed", exc.code, str(exc)
            item.final_url = exc.url or item.final_url
            if exc.code not in {"timeout", "network"} or attempt == 1:
                break
    item.duration_ms = round((monotonic() - started) * 1000)
    # 若重试没有返回更好的样本，不让已有商品证据从历史中消失。
    if previous.products and not item.products:
        return previous.model_copy(
            update={
                "status": "incomplete",
                "attempts": item.attempts,
                "reason": f"重试未获得新证据：{item.reason}；保留原样本。",
            }
        )
    return item


def discover_boards(
    main: AmazonEvidence, manual: list[str], scope_limit: int
) -> tuple[list[AmazonEvidence], bool]:
    root_ids = {board_identity(main.url), board_identity(main.final_url or main.url)}
    choices: dict[str, AmazonEvidence] = {}
    for title, url, role in [
        *(("用户指定子类目", url, "manual") for url in manual),
        *((title, url, "child") for title, url in main.links),
    ]:
        identity = board_identity(url)
        if identity not in root_ids:
            choices.setdefault(identity, board_step(url, title, role))
    return list(choices.values())[:scope_limit], len(choices) > scope_limit
