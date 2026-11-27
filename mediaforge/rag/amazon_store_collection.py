import asyncio
import re
from datetime import UTC, datetime
from time import monotonic
from typing import Literal
from urllib.parse import unquote, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from mediaforge.models.amazon_research import AmazonStoreVariant
from mediaforge.models.amazon_research_run import (
    AmazonEvidence,
    AmazonListingDate,
    AmazonStoreSample,
    AmazonStoreSampleScope,
)
from mediaforge.rag.amazon_research import (
    AMAZON,
    AmazonFetchError,
    AmazonSourceError,
    _fetch_page,
    _first_text,
    _linked_source,
    _parse_listings,
    _rating,
    _store_pages,
    _store_product_cards,
    _text,
    _variant_coordinates,
    normalize_source,
)

SECTION_LIMIT = 30
SECTION_ASIN_LIMIT = 30
VARIANT_PROOF_LIMIT = 100
FETCH_CONCURRENCY = 3
MAX_ATTEMPTS = 2

_HOME = re.compile(r"^(?:home|store home|homepage|首页|店铺首页)$", re.I)
_ROLES = {
    "home": _HOME,
    "new": re.compile(r"new arrivals?|new releases?|what.s new|新品", re.I),
    "popular": re.compile(r"best.?sellers?|hot sellings?|畅销|热销", re.I),
    "about": re.compile(r"about(?: us)?|brand story|关于|品牌故事", re.I),
    "collection": re.compile(r"collections?|合集|系列", re.I),
}
_RETRY_CODES = {"timeout", "network"}
_STOREFRONT = 'main, [role="main"], [data-testid*="store"], [data-module-type]'


def page_id(url: str) -> str:
    return urlsplit(url).path.rsplit("/", 1)[-1].lower()


def section_step(url: str, title: str, role: str | None = None) -> AmazonEvidence:
    return AmazonEvidence(
        key=f"section-{page_id(url)}", kind="section", url=url, title=title, role=role
    )


def _slug(url: str) -> str | None:
    parts = unquote(urlsplit(url).path).split("/")
    return parts[2].casefold() if len(parts) == 5 else None


def _role(title: str) -> str:
    return next((key for key, regex in _ROLES.items() if regex.search(title)), "category")


def _card_value(card: dict, groups: tuple[str, ...], fields: tuple[str, ...]) -> str | None:
    for group in groups:
        values = card.get(group, {})
        for field in fields:
            value = values.get(field)
            if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                return str(value).strip()[:100] or None
    return None


def _record(data: object, key: str) -> dict:
    value = data.get(key) if isinstance(data, dict) else None
    return value if isinstance(value, dict) else {}


def _string(data: dict, key: str, limit: int = 100) -> str | None:
    value = data.get(key)
    return value.strip()[:limit] or None if isinstance(value, str) else None


def _currency(price: str | None, explicit: str | None = None) -> str | None:
    if explicit and re.fullmatch(r"[A-Z]{3}", explicit):
        return explicit
    if not price:
        return None
    for marker, code in (
        ("S$", "SGD"),
        ("CA$", "CAD"),
        ("A$", "AUD"),
        ("$", "USD"),
        ("€", "EUR"),
        ("£", "GBP"),
    ):
        if price.startswith(marker):
            return code
    return None


def _store_brand(card: dict) -> tuple[str | None, str | None]:
    contributors = _record(card, "byLine").get("contributors", [])
    if not isinstance(contributors, list):
        return None, None
    for contributor in contributors:
        if not isinstance(contributor, dict):
            continue
        roles = contributor.get("roles", [])
        if not isinstance(roles, list) or not any(
            isinstance(role, dict) and role.get("type") == "BRAND" for role in roles
        ):
            continue
        links = contributor.get("links", [])
        if isinstance(links, list):
            for link in links:
                if isinstance(link, dict) and (href := _string(link, "url", 2000)):
                    try:
                        return _string(contributor, "name"), normalize_source(
                            "store", urljoin(AMAZON, href)
                        )
                    except AmazonSourceError:
                        continue
        return _string(contributor, "name"), None
    return None, None


def _store_card_price(card: dict) -> tuple[str | None, str | None, str | None]:
    # Amazon 卡片以亚马逊展示的新报价为准。区间仍保留原显示，单价计算会排除它。
    offer = _record(_record(card, "marketplaceOfferSummary"), "newOfferSummary")
    minimum, maximum = _record(offer, "minPrice"), _record(offer, "maxPrice")
    low, high = _string(minimum, "displayString"), _string(maximum, "displayString")
    currency = _string(minimum, "currencyCode")
    if low and high and low != high:
        return f"{low} – {high}", currency, "栏目卡片公开新报价范围"
    if low:
        return low, currency, "栏目卡片公开新报价"
    raw = _card_value(card, ("priceToPay", "price"), ("displayString", "formattedPrice"))
    return raw, _currency(raw), "栏目卡片价格" if raw else None


def _section_products(soup: BeautifulSoup, limit: int) -> list[AmazonStoreSample]:
    cards = _store_product_cards(soup)
    samples = []
    for listing in _parse_listings(soup, "store", limit=limit + 1, store_cards=cards):
        card = cards.get(listing.asin, {})
        title = card.get("title", {}).get("displayString")
        price, currency, price_label = _store_card_price(card)
        summary = _record(card, "customerReviewsSummary")
        score = _string(_record(summary, "rating"), "displayString") or _card_value(
            card,
            ("starRating", "customerReviews", "reviews"),
            ("displayString", "rating", "value"),
        )
        count_data = _record(summary, "count")
        count = _string(count_data, "displayString") or _card_value(
            card,
            ("customerReviews", "reviews", "starRating"),
            ("count", "ratingCount", "reviewCount"),
        )
        if type(exact_count := count_data.get("value")) is int and exact_count >= 0:
            count = f"{exact_count:,}"
        elif count:
            # 保留 K/M 等近似原文，避免将如「1.2K」误缩成个位数。
            count = re.sub(r"\s+(?:ratings|reviews)$", "", count, flags=re.I)
        brand, store_url = _store_brand(card)
        sample = AmazonStoreSample(
            **listing.model_dump(exclude={"title", "price", "rating", "rating_count"}),
            title=title.strip()[:500]
            if listing.title == listing.asin and isinstance(title, str) and title.strip()
            else listing.title,
            price=price,
            has_variants=card.get("hasVariations"),
            price_currency=currency,
            price_label=price_label,
            product_type=_string(_record(card, "productCategory"), "productType"),
            brand=brand,
            store_url=store_url,
            rating=_rating(score),
            rating_count=count,
        )
        if (specs := _variant_coordinates(card)) and (
            choices := specs[2].get(listing.asin)
        ) is not None:
            symbols, values, coordinates = specs
            # 有限保留规格表中的关联 ID，以便跨栏目核对部分重叠的规格表。
            # 只有栏目中实际采到的 ASIN 进入商品计数。
            if len(coordinates) > 1:
                sample.family_members = sorted(coordinates)[:VARIANT_PROOF_LIMIT]
                sample.family_asin = sample.family_members[0]
            for symbol, field in (("color_name", "color"), ("size_name", "size")):
                if symbols.count(symbol) == 1:
                    index = symbols.index(symbol)
                    setattr(sample, field, values[index][choices[index]].strip()[:160])
            if sample.color:
                index = symbols.index("color_name")
                members = [
                    asin for asin, row in coordinates.items() if row[index] == choices[index]
                ]
                sample.color_members = sorted(members)[:VARIANT_PROOF_LIMIT]
                sample.color_group_asin = sample.color_members[0]
            if sample.size:
                index = symbols.index("size_name")
                signature = choices[:index] + choices[index + 1 :]
                fixed = [
                    asin
                    for asin, row in coordinates.items()
                    if row[:index] + row[index + 1 :] == signature
                ]
                sample.size_group_members = sorted(fixed)[:VARIANT_PROOF_LIMIT]
                if sample.store_variant is None:
                    sample.store_variant = AmazonStoreVariant(
                        group_asin=sample.size_group_members[0],
                        size=sample.size,
                        color=sample.color,
                        title=title.strip()[:500]
                        if isinstance(title, str) and title.strip()
                        else sample.title,
                    )
        samples.append(sample)
    return samples


def _parse_section(
    soup: BeautifulSoup,
    item: AmazonEvidence,
    scope: AmazonStoreSampleScope,
) -> None:
    url = item.final_url or item.url
    current_slug = _slug(url) or _slug(item.url)
    title = _first_text(soup, "title", "h1")
    if title and item.role in {None, "home"}:
        item.title = title
    if title and item.role is None:
        # 店铺标签可为 Amazon.com: Brand: Section，只有栏目部分用于判定角色。
        item.role = _role(title.removeprefix("Amazon.com:").strip().partition(":")[2])
        item.role = None if item.role == "category" else item.role

    navigation = soup.select_one('nav[aria-label*="Navigation Bar"]') or soup.select_one("nav")
    item.links = [
        (page.title, page.url)
        for page in _store_pages(navigation or soup, url, limit=scope.section_limit + 1)
        if not current_slug or _slug(page.url) in {None, current_slug}
    ]
    # 当前页在旧导航解析中被省略。以公开头部品牌标记或导航中的 Home 项确认首页。
    home = soup.select_one('a[data-testid="header-logo"][href]')
    if not home and navigation:
        home = next(
            (link for link in navigation.select("a[href]") if _HOME.fullmatch(_text(link) or "")),
            None,
        )
    if home and (home_url := _linked_source("store", home)):
        item.home_url = home_url
        if page_id(home_url) == page_id(url):
            item.role = "home"
        elif _slug(home_url) in {None, current_slug}:
            item.links = [("Home", home_url)] + [
                (title, link) for title, link in item.links if page_id(link) != page_id(home_url)
            ]
    canonical = soup.select_one('link[rel="canonical"], meta[property="og:url"]')
    if canonical is not None:
        try:
            current = normalize_source(
                "store",
                urljoin(url, str(canonical.get("href") or canonical.get("content") or "")),
            )
            if page_id(current) == page_id(url):
                item.final_url = current
        except AmazonSourceError:
            pass

    candidates = _section_products(soup, scope.asin_limit_per_section)
    item.products = candidates[: scope.asin_limit_per_section]
    item.observed_count = len(item.products)
    if item.role == "about":
        item.excerpt = _text(soup.select_one(_STOREFRONT), 240)
    dynamic = soup.select_one(
        '[aria-busy="true"], [data-testid*="lazy"], [data-loading="true"], .a-spinner',
    )
    empty_message = soup.find(
        string=re.compile(
            r"no (?:products|items|results)(?: (?:available|found))?"
            r"|no matching products|0 products",
            re.I,
        )
    )
    if len(candidates) > scope.asin_limit_per_section:
        item.status, item.code = "incomplete", "sample_limit"
        item.reason = f"已保留前 {scope.asin_limit_per_section} 个 ASIN，页面完整数量未知。"
    elif item.products:
        item.status = "success"
    elif empty_message or item.role in {"home", "about"} and (item.links or item.excerpt):
        item.status, item.code, item.reason = (
            "empty",
            "no_products",
            "可读取栏目内容，未见可取样商品。",
        )
    elif dynamic:
        item.status, item.code, item.reason = (
            "incomplete",
            "dynamic",
            "公开 HTML 仅有动态加载占位。",
        )
    elif item.links:
        item.status, item.code, item.reason = (
            "empty",
            "no_products",
            "可读取栏目导航，未见可取样商品。",
        )
    else:
        item.status, item.code, item.reason = (
            "incomplete",
            "markup",
            "未识别店铺导航或商品卡片，公开页面结构可能变化。",
        )


def _parse_evidence(
    item: AmazonEvidence,
    html: bytes,
    scope: AmazonStoreSampleScope,
) -> None:
    soup = BeautifulSoup(html, "html.parser", from_encoding="utf-8")
    if soup.select_one('form[action*="validateCaptcha"], #captchacharacters') or soup.find(
        string=re.compile(r"Sorry, we just need to make sure you.re not a robot", re.I),
    ):
        raise AmazonFetchError("Amazon 返回了访问验证。", "captcha", item.final_url)
    _parse_section(soup, item, scope)


async def collect_evidence(
    client: httpx.AsyncClient,
    original: AmazonEvidence,
    *,
    expected_root: str | None = None,
    scope: AmazonStoreSampleScope | None = None,
) -> AmazonEvidence:
    scope = scope or AmazonStoreSampleScope(
        section_limit=SECTION_LIMIT,
        asin_limit_per_section=SECTION_ASIN_LIMIT,
    )
    item = original.model_copy(
        update={
            "products": [],
            "links": [],
            "final_url": None,
            "home_url": None,
            "observed_count": None,
            "reason": None,
            "code": None,
            "excerpt": None,
        }
    )
    started = monotonic()
    for attempt in range(MAX_ATTEMPTS):
        item.attempts += 1
        item.collected_at = datetime.now(UTC)
        try:
            item.final_url, html = await _fetch_page(client, "store", item.url)
            await asyncio.to_thread(_parse_evidence, item, html, scope)
            if item.home_url and expected_root and page_id(item.home_url) != page_id(expected_root):
                # 同一美国站也会链接其他品牌。确认不同首页后不把对方商品纳入本店。
                item.status, item.code = "failed", "store_mismatch"
                item.reason = "栏目公开导航属于其他店铺，未计入本店样本。"
                item.products, item.links, item.excerpt, item.observed_count = [], [], None, None
            break
        except AmazonFetchError as exc:
            item.status, item.code, item.reason = "failed", exc.code, str(exc)
            item.final_url = exc.url or item.final_url
            if exc.code not in _RETRY_CODES or attempt + 1 == MAX_ATTEMPTS:
                break
            item.code, item.reason = None, None
            await asyncio.sleep(0.4)
    item.duration_ms = round((monotonic() - started) * 1000)
    return item


def discover_sections(
    steps: list[AmazonEvidence],
    root: str | None,
    limit: int,
) -> tuple[list[AmazonEvidence], str | None, bool]:
    # 从输入页和已确认栏目继续发现导航，使用栏目 UUID 去掉带品牌名和不带品牌名的别名。
    known = {page_id(item.url): item for item in steps}
    for item in steps:
        if item.final_url:
            known[page_id(item.final_url)] = item
        if item.status == "failed":
            continue
        if item.home_url:
            root = root or item.home_url
        if item.role == "home":
            root = item.final_url or root or item.url
    added = []
    limited = False
    current_slug = _slug(root or steps[0].url)
    for item in steps:
        if item.status == "failed":
            continue
        for title, url in sorted(item.links, key=lambda link: not bool(_HOME.fullmatch(link[0]))):
            if current_slug and _slug(url) not in {None, current_slug}:
                continue
            role = _role(title)
            key = page_id(url)
            if key in known:
                if known[key].role in {None, "category"}:
                    known[key].role = role
                if role == "home":
                    root = known[key].final_url or root or url
                continue
            if role == "home":
                root = root or url
            if len(steps) + len(added) >= limit:
                limited = True
                continue
            section = section_step(url, title, role)
            known[key] = section
            added.append(section)
    return added, root, limited


def _visible_listing_date(
    html: bytes,
) -> tuple[Literal["first_available", "release_date"] | None, str | None]:
    soup = BeautifulSoup(html, "html.parser", from_encoding="utf-8")
    if soup.select_one('form[action*="validateCaptcha"], #captchacharacters'):
        raise AmazonFetchError("Amazon 返回访问验证。", "captcha")
    labels: dict[str, Literal["first_available", "release_date"]] = {
        "date first available": "first_available",
        "release date": "release_date",
    }
    found = {}
    for row in soup.select(
        "tr, #detailBullets_feature_div li, #detailBulletsWrapper_feature_div li"
    ):
        cells = row.find_all(["th", "td"], recursive=False)
        heading = cells[0] if len(cells) >= 2 else row.select_one(".a-text-bold")
        heading_text = _text(heading) or ""
        label = labels.get(
            heading_text.translate({0x200E: None, 0x200F: None}).strip(" :").casefold()
        )
        if not label:
            continue
        value = (
            _text(cells[1]) if len(cells) >= 2 else (_text(row) or "").removeprefix(heading_text)
        )
        if not value:
            continue
        cleaned = value.translate({0x200E: None, 0x200F: None}).strip(" :")
        for pattern in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d", "%m/%d/%Y"):
            try:
                datetime.strptime(cleaned, pattern)
            except ValueError:
                continue
            # 保留 Amazon 的原始日期措辞。发布日期和在 Amazon 首次可售日期含义不同。
            found.setdefault(label, cleaned[:100])
            break
    for label in labels.values():
        if label in found:
            return label, found[label]
    return None, None


async def collect_listing_date(client: httpx.AsyncClient, asin: str) -> AmazonListingDate:
    url = normalize_source("product", asin)
    item = AmazonListingDate(asin=asin, url=url, collected_at=datetime.now(UTC))
    try:
        final, html = await _fetch_page(client, "product", url)
        if final != url:
            raise AmazonFetchError("商品链接转向其他 ASIN，日期未知。", "asin_redirect")
        item.label, item.date_text = await asyncio.to_thread(_visible_listing_date, html)
        if not item.date_text:
            item.problem = "公开详情未见明确的首次可售日期或发布日期。"
    except AmazonFetchError as exc:
        item.problem = str(exc)
    return item
