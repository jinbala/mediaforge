import asyncio
import json
import re
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import parse_qs, quote, unquote, urlencode, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup, Tag

from mediaforge.models.amazon_image_reference import amazon_image_url as _amazon_image_url
from mediaforge.models.amazon_research import (
    AmazonListing,
    AmazonProduct,
    AmazonResearchReport,
    AmazonResearchRequest,
    AmazonReview,
    AmazonReviewTopic,
    AmazonStorePage,
    AmazonStoreVariant,
    ResearchMode,
)
from mediaforge.rag.amazon_store_translation import localize_amazon_store

AMAZON = "https://www.amazon.com"
SUGGESTIONS = "https://completion.amazon.com/api/2017/suggestions"
MAX_PRODUCT_IMAGES = 24
GALLERY_INITIAL = re.compile(
    r'''(?:["']colorImages["']\s*:|\bcolorImages\s*=)\s*\{\s*["']initial["']\s*:\s*'''
    r"(?:[\w$.]+\.parseJSON\(\s*')?"
)
ASIN = re.compile(r"[A-Z0-9]{10}", re.I)
PRODUCT_PATH = re.compile(r"/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})(?:/|$)", re.I)
REVIEW_PATH = re.compile(r"/product-reviews/([A-Z0-9]{10})", re.I)
BESTSELLERS_PATH = re.compile(
    r"/(?:gp/bestsellers(?:/[\w-]+){0,3}|(?:Best-Sellers[\w-]*/)?zgbs(?:/[\w-]+){0,3})",
    re.I | re.ASCII,
)
STORE_PATH = re.compile(
    r"/stores(?:/[\w .,+&!~'()-]{1,120})?"
    r"/page/[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}",
    re.I,
)
MAX_PAGE_BYTES = 8 * 1024 * 1024
_research_slots = asyncio.Semaphore(4)
# 用评论样本中的关键词归纳拍摄建议，命中次数表示提及该主题的评论条数。
_review_rules = (
    (
        "尺寸与合身",
        r"\b(size|sizing|fit|fits|length|waist|runs small|runs large)\b|尺码|版型|裤长|腰围",
        "核对实测尺寸后补尺码表和上身对照，标注量法与模特数据。",
    ),
    (
        "面料与厚度",
        r"\b(fabric|material|thick|thin|soft|scratchy|sheer|see.through)\b|面料|厚薄|柔软|透光",
        "拍实物面料和内里特写，用真实光线展示纹理、垂感和透光程度。",
    ),
    (
        "颜色与外观",
        r"\b(colou?r|shade|looks?|photo|picture)\b|颜色|色差|实拍",
        "用一致光线展示正背面和真实色卡，核对详情图与实物颜色。",
    ),
    (
        "做工与耐用",
        r"\b(seam|stitch(?:ing|es)?|zipper|broke|broken|durab\w*|quality)\b|做工|开线|拉链|耐用",
        "补接缝、扣件或连接部位特写，先复核样品做工再写卖点。",
    ),
    (
        "规格与使用",
        r"\b(assembl\w*|instructions?|setup|install\w*|packag\w*|pieces|missing)\b|安装|操作|配件|缺件",
        "核对包装清单与使用步骤，用图展示配件、组装或实际使用方法。",
    ),
)


class AmazonSourceError(ValueError):
    """The input does not match a supported US Amazon page."""


class AmazonFetchError(Exception):
    """The public source could not supply a usable response."""

    def __init__(self, message: str, code: str = "unreadable", url: str | None = None):
        super().__init__(message)
        self.code = code
        self.url = url


def normalize_source(mode: ResearchMode | Literal["search", "reviews"], source: str) -> str:
    # 按调研类型验证美国站地址并重建链接，去掉追踪参数，只保留榜单第二页参数。
    value = source.strip()
    if mode == "product" and ASIN.fullmatch(value):
        return f"{AMAZON}/dp/{value.upper()}"

    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.netloc.lower() not in {"www.amazon.com", "amazon.com"}
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
        ):
            raise ValueError
        path = unquote(parsed.path).rstrip("/")
        if re.search(r"%|\\|[\x00-\x1f\x7f]", path) or any(
            part in {".", ".."} for part in path.split("/")
        ):
            raise ValueError
        if mode == "product" and (match := PRODUCT_PATH.search(path)):
            return f"{AMAZON}/dp/{match[1].upper()}"
        if mode == "bestsellers" and BESTSELLERS_PATH.fullmatch(path):
            page = parse_qs(parsed.query).get("pg", ["1"])[0]
            return f"{AMAZON}{path}" + ("?pg=2" if page == "2" else "")
        if mode == "search" and path == "/s":
            query = parse_qs(parsed.query).get("k", [""])[0].strip()
            if query and len(query) <= 100 and not any(ord(char) < 32 for char in query):
                return f"{AMAZON}/s?{urlencode({'k': query})}"
        if mode == "reviews" and (match := REVIEW_PATH.fullmatch(path)):
            bucket = parse_qs(parsed.query).get("filterByStar", [""])[0]
            sort = parse_qs(parsed.query).get("sortBy", ["recent"])[0]
            allowed = {"one_star", "two_star", "three_star", "four_star", "five_star"}
            if sort == "recent" and (not bucket or bucket in allowed):
                params = {"sortBy": "recent", "pageNumber": "1"}
                if bucket:
                    params["filterByStar"] = bucket
                return f"{AMAZON}/product-reviews/{match[1].upper()}?{urlencode(params)}"
        if mode == "store" and STORE_PATH.fullmatch(path):
            return f"{AMAZON}{quote(path, safe='/')}"
    except ValueError:
        pass

    message = {
        "product": "请输入 Amazon 美国站商品链接或 10 位 ASIN。",
        "bestsellers": "请输入 Amazon 美国站 Best Sellers 榜单链接。",
        "store": "请输入 Amazon 美国站品牌店铺链接，地址中应包含 /stores/ 和 /page/。",
        "search": "Amazon 美国站搜索词无效。",
        "reviews": "请输入 Amazon 美国站商品评论页。",
    }
    raise AmazonSourceError(message[mode])


def _text(node: Tag | None, limit: int = 500) -> str | None:
    if node is None:
        return None
    return " ".join(node.get_text(" ", strip=True).split())[:limit] or None


def _first_text(node: Tag, *selectors: str) -> str | None:
    for selector in selectors:
        if value := _text(node.select_one(selector)):
            return value
    return None


def _rating(value: str | None) -> str | None:
    if match := re.search(r"\b([0-5](?:\.\d+)?)\s*(?:out of 5|stars?\b|$)", value or "", re.I):
        return match[1]
    return None


def _rating_count(value: str | None) -> str | None:
    if match := re.search(r"\d[\d,]*", value or ""):
        return match[0]
    return None


def _image_from_tag(image: Tag | None) -> str | None:
    if image is None:
        return None
    for attr in ("src", "data-src", "data-old-hires"):
        if url := _amazon_image_url(image.get(attr)):
            return url
    # 有些图片的可用地址只在 Amazon 提供的分辨率映射里。
    try:
        candidates = json.loads(str(image.get("data-a-dynamic-image", "{}")))
    except (ValueError, RecursionError):
        return None
    if isinstance(candidates, dict):
        return next((url for source in candidates if (url := _amazon_image_url(source))), None)
    return None


def _product_gallery(soup: BeautifulSoup, main: str | None) -> list[str]:
    images: list[str] = []
    seen: set[str] = set()

    def include(*urls: str | None) -> None:
        candidates = [url for url in urls if url]
        if not candidates or len(images) >= MAX_PRODUCT_IMAGES:
            return
        # 同一图可能带尺寸后缀，也可能另有缩略图 ID。记录同一条目里所有标识。
        ids = {urlsplit(url).path.rsplit("/", 1)[-1].split(".", 1)[0] for url in candidates}
        if ids.isdisjoint(seen):
            images.append(candidates[0])
        seen.update(ids)

    # 只解码当前变体的 JSON 数组，包括 Amazon 放在 parseJSON 字符串里的数组。
    decoder = json.JSONDecoder()
    for script in soup.select("script"):
        raw = script.string or script.get_text()
        if len(raw) > 2_000_000 or not (match := GALLERY_INITIAL.search(raw)):
            continue
        try:
            entries, _ = decoder.raw_decode(raw, match.end())
        except (ValueError, RecursionError):
            continue
        if isinstance(entries, list):
            for entry in entries[:MAX_PRODUCT_IMAGES]:
                if isinstance(entry, dict) and entry.get("variant") != "VIDEO" and not entry.get("videoUrl"):
                    include(*(_amazon_image_url(entry.get(key)) for key in ("hiRes", "large", "thumb")))
            if images:
                break
    include(main)
    for tag in soup.select(
        "#altImages li.item:not(.videoThumbnail):not([data-action*='video']) img, "
        "#imageBlockThumbs img", limit=MAX_PRODUCT_IMAGES,
    ):
        include(_amazon_image_url(tag.get("data-old-hires")) or _image_from_tag(tag))
    return images


def _collect_reviews(soup: BeautifulSoup, samples: str) -> list[AmazonReview]:
    # 先收集页面可见评论，再补录用户粘贴的片段。两种来源一起去重，保留来源标记。
    reviews: list[AmazonReview] = []
    seen: set[str] = set()
    for item in soup.select('[data-hook="review"]', limit=16):
        body = _first_text(item, '[data-hook="review-body"]')
        if body and body.casefold() not in seen:
            seen.add(body.casefold())
            rating = _first_text(
                item, '[data-hook="review-star-rating"]', '[data-hook="cmps-review-star-rating"]'
            )
            reviews.append(AmazonReview(text=body, rating=_rating(rating)))
        if len(reviews) >= 8:
            break
    for line in samples.splitlines():
        body = " ".join(line.split())[:500]
        if body and body.casefold() not in seen:
            seen.add(body.casefold())
            reviews.append(AmazonReview(text=body, source="manual"))
        if len(reviews) >= 16:
            break
    return reviews


def _listing_structure(soup: BeautifulSoup, gallery: list[str]) -> dict:
    category_path = list(dict.fromkeys(
        text for link in soup.select("#wayfinding-breadcrumbs_feature_div li a", limit=20)
        if (text := _text(link))
    ))
    first_available = None
    for label in soup.select(
        "#productDetails_feature_div th, #productDetails_techSpec_section_1 th, "
        "#productDetails_detailBullets_sections1 th, "
        "#detailBulletsWrapper_feature_div .a-text-bold"
    ):
        normalized = re.sub(r"[\u200e\u200f]", "", _text(label) or "").strip(" :")
        if normalized.casefold() != "date first available":
            continue
        value = label.find_next_sibling("td" if label.name == "th" else "span")
        if first_available := _text(value, 100):
            break

    aplus_blocks = soup.select(
        "#aplus, #aplus_feature_div, #aplusBrandStory_feature_div, "
        ".aplus-v2, .a-plus-content, #aplusPageWidget", limit=20,
    )
    return {
        "description": _text(soup.select_one("#productDescription"), 5000),
        "category_path": category_path or None,
        "first_available": first_available,
        "image_count": len(gallery) or None,
        # 空占位不当作 A+；整块未返回时，也不能断言商品没有 A+。
        "has_aplus": any(_text(block) or block.select_one("img[src]")
                         for block in aplus_blocks) if aplus_blocks else None,
    }


def _parse_product(soup: BeautifulSoup, url: str, samples: str) -> AmazonProduct:
    title = _first_text(soup, "#productTitle")
    if not title:
        raise AmazonFetchError("Amazon 暂未返回可读取的商品详情，请核对 ASIN 或稍后重试。")
    reviews = _collect_reviews(soup, samples)
    topics = []
    for label, pattern, suggestion in _review_rules:
        matches = sum(bool(re.search(pattern, review.text, re.I)) for review in reviews)
        if matches:
            topics.append(AmazonReviewTopic(label=label, matches=matches, suggestion=suggestion))
    topics.sort(key=lambda topic: topic.matches, reverse=True)

    # 图片需求带入固定的主图和详情图要求，再补充最多三个高频主题的拍摄建议。
    brief = [
        "主图：使用自有商品原图，纯白背景，完整展示商品轮廓，真实保留颜色、结构和纹理。",
        "详情图：展示实际使用场景、可验证卖点和局部细节，尺寸与性能以已核实的商品资料为准。",
        *(f"{topic.label}：{topic.suggestion}" for topic in topics[:3]),
    ]
    highlights = [
        text for item in soup.select("#feature-bullets li .a-list-item", limit=10)
        if (text := _text(item))
    ]
    rank_rows = [
        tag.parent
        for tag in soup.select("th, #detailBulletsWrapper_feature_div .a-text-bold")
        if (_text(tag) or "").rstrip(": ").lower() == "best sellers rank"
    ]
    if old_rank := soup.select_one("#SalesRank"):
        rank_rows.append(old_rank)
    sales_ranks = list(dict.fromkeys(
        match.group(0).strip()
        for row in rank_rows
        if (text := _text(row, 1500))
        for match in re.finditer(r"#\d[\d,]*\s+in\s+[^#(]{1,100}", text, re.I)
    ))[:8]
    rating = soup.select_one("#acrPopover")
    brand_link = soup.select_one("#bylineInfo")
    store_url = _linked_source("store", brand_link) if brand_link else None
    brand = _text(brand_link)
    if brand:
        brand = re.sub(r"^(?:Visit the (.+) Store|Brand:\s*(.+))$", r"\1\2", brand, flags=re.I)
    image = soup.select_one("img#landingImage, img#imgBlkFront, img#ebooksImgBlkFront")
    social_image = soup.select_one('meta[property="og:image"]')
    image_url = _image_from_tag(image) or _amazon_image_url(
        social_image.get("content") if social_image else None
    )
    gallery = _product_gallery(soup, image_url)
    return AmazonProduct(
        asin=url.rsplit("/", 1)[-1],
        title=title,
        url=url,
        image_url=image_url,
        brand=brand,
        store_url=store_url,
        category=_first_text(soup, "#wayfinding-breadcrumbs_feature_div li:last-child a"),
        price=_first_text(
            soup,
            "#corePrice_feature_div .a-price .a-offscreen",
            "#corePriceDisplay_desktop_feature_div .a-price .a-offscreen",
            "#priceblock_ourprice",
            "#priceblock_dealprice",
        ),
        rating=_rating(str(rating.get("title", "")) if rating else None)
        or _rating(_first_text(soup, "#acrPopover .a-icon-alt")),
        rating_count=_rating_count(_first_text(soup, "#acrCustomerReviewText")),
        sales_ranks=sales_ranks,
        highlights=list(dict.fromkeys(highlights)),
        reviews=reviews,
        review_topics=topics,
        image_brief="\n".join(brief),
        gallery_image_urls=gallery,
        **_listing_structure(soup, gallery),
    )


def _linked_source(mode: ResearchMode, link: Tag) -> str | None:
    try:
        return normalize_source(mode, urljoin(AMAZON, str(link.get("href", ""))))
    except AmazonSourceError:
        return None


def _store_product_cards(soup: BeautifulSoup) -> dict[str, dict]:
    """Read embedded storefront card metadata by ASIN."""
    # 店铺 HTML 内嵌的商品数据包含主图、标题和规格表，按 ASIN 归档供商品链接匹配。
    by_asin: dict[str, dict] = {}
    decoder = json.JSONDecoder()
    for script in soup.find_all("script"):
        data = script.string or ""
        if '"detailPageLinkURL"' not in data:
            continue
        for match in re.finditer(r'"products"\s*:\s*(?=\[\s*\{)', data):
            try:
                products, _ = decoder.raw_decode(data, match.end())
            except (ValueError, RecursionError):
                continue
            for product in products:
                if not isinstance(product, dict):
                    continue
                asin = product.get("asin")
                if isinstance(asin, str) and ASIN.fullmatch(asin):
                    card = by_asin.setdefault(asin.upper(), {})
                    if type(has_variants := product.get("hasVariations")) is bool:
                        card.setdefault("hasVariations", has_variants)
                    for field in (
                        "productImages", "title", "twisterVariations", "price",
                        "starRating", "customerReviews", "reviews", "priceToPay",
                        "marketplaceOfferSummary", "customerReviewsSummary", "byLine", "productCategory",
                    ):
                        if isinstance(value := product.get(field), dict):
                            card.setdefault(field, value)
    return by_asin


def _variant_coordinates(card: dict) -> tuple[list, list, dict] | None:
    twister = card.get("twisterVariations")
    if not isinstance(twister, dict):
        return None
    dimensions = twister.get("dimensionKeys")
    values = twister.get("dimensionValues")
    variations = twister.get("variations")
    if not (
        isinstance(dimensions, list) and dimensions
        and isinstance(values, list) and len(dimensions) == len(values)
        and isinstance(variations, list)
    ):
        return None

    symbols = [
        dimension.get("symbol") if isinstance(dimension, dict) else None
        for dimension in dimensions
    ]
    # 每个 ASIN 的 choices 是各规格维度的选项下标，例如 [颜色下标, 尺码下标]。
    coordinates = {}
    conflicts = set()
    for variant in variations:
        if not isinstance(variant, dict):
            continue
        variant_asin, choices = variant.get("asin"), variant.get("values")
        if (
            isinstance(variant_asin, str) and ASIN.fullmatch(variant_asin)
            and isinstance(choices, list) and len(choices) == len(dimensions)
            and all(
                isinstance(options, list) and type(index) is int and 0 <= index < len(options)
                and isinstance(options[index], str) and options[index].strip()
                for options, index in zip(values, choices, strict=True)
            )
        ):
            key = variant_asin.upper()
            if key in coordinates and coordinates[key] != choices:
                conflicts.add(key)
            coordinates[key] = choices
    for key in conflicts:
        del coordinates[key]
    return symbols, values, coordinates


def _store_variant(card: dict, asin: str) -> AmazonStoreVariant | None:
    title, specs = card.get("title"), _variant_coordinates(card)
    name = title.get("displayString") if isinstance(title, dict) else None
    if specs is None or not isinstance(name, str) or not name.strip():
        return None
    symbols, values, coordinates = specs
    selected = coordinates.get(asin)
    if selected is None or symbols.count("size_name") != 1:
        return None
    size_index = symbols.index("size_name")
    # 去掉尺码后其余规格都相同才归为一组，配色或其他规格不同的商品各自显示。
    fixed = selected[:size_index] + selected[size_index + 1:]
    same_group = [
        code for code, choices in coordinates.items()
        if choices[:size_index] + choices[size_index + 1:] == fixed
    ]
    if len(same_group) < 2:
        return None

    color = None
    if "color_name" in symbols:
        index = symbols.index("color_name")
        color = " ".join(values[index][selected[index]].split())[:160]
    return AmazonStoreVariant(
        # 取组内字典序最小的 ASIN，让组内每个尺码得到相同的分组键。
        group_asin=min(same_group),
        size=" ".join(values[size_index][selected[size_index]].split())[:80],
        title=" ".join(name.split())[:500],
        color=color,
    )


def _store_main_image(product_images: dict) -> str | None:
    # 只取标记为 MAIN 的商品主图，缩略图优先用低分辨率版本。
    images = product_images.get("images")
    if not isinstance(images, list):
        return None
    for image in images:
        if not isinstance(image, dict) or image.get("variant") != "MAIN":
            continue
        for size in ("lowRes", "hiRes"):
            resolution = image.get(size)
            if isinstance(resolution, dict) and (url := _amazon_image_url(resolution.get("url"))):
                return url
    return None


def _parse_listings(
    soup: BeautifulSoup, mode: ResearchMode, *, limit: int = 30, store_cards: dict | None = None,
) -> list[AmazonListing]:
    listings: dict[str, AmazonListing] = {}
    if mode == "bestsellers":
        for item in soup.select(".zg-grid-general-faceout, .zg-item-immersion"):
            link = item.select_one('a[href*="/dp/"], a[href*="/gp/product/"]')
            if link is None or (url := _linked_source("product", link)) is None:
                continue
            asin = url.rsplit("/", 1)[-1]
            title = _first_text(
                item,
                '[class*="line-clamp"]', ".p13n-sc-truncate",
                '[class*="p13n-sc-css-line-clamp"]',
            )
            image = link.select_one("img")
            if not title and (title_image := item.select_one("a img[alt]")):
                title = str(title_image["alt"]).strip()[:500]
            if title and asin not in listings:
                listings[asin] = AmazonListing(
                    asin=asin,
                    title=title,
                    url=url,
                    image_url=_image_from_tag(image),
                    price=_first_text(item, '[class*="p13n-sc-price"]', ".a-price .a-offscreen"),
                    rating=_rating(_first_text(item, ".a-icon-alt")),
                    rating_count=_rating_count(_first_text(item, "a .a-size-small")),
                    rank=_first_text(item, ".zg-bdg-text")
                    or _first_text(item.parent, ".zg-bdg-text"),
                )
            if len(listings) >= limit:
                break
    else:
        by_asin = _store_product_cards(soup) if store_cards is None else store_cards
        for link in soup.select('a[href*="/dp/"], a[href*="/gp/product/"]'):
            if (url := _linked_source("product", link)) is None:
                continue
            asin = url.rsplit("/", 1)[-1]
            # 以页面可点开的商品链接为准，用同一 ASIN 的卡片资料补充主图和规格。
            card = by_asin.get(asin, {})
            product_images = card.get("productImages")
            if not isinstance(product_images, dict):
                product_images = {}
            alt = product_images.get("altText")
            title = " ".join(alt.split())[:500] if isinstance(alt, str) else None
            image = link.select_one("img")
            image_url = _store_main_image(product_images) or _image_from_tag(image)
            if asin in listings:
                if listings[asin].image_url is None:
                    listings[asin].image_url = image_url
                continue
            title = title or _text(link)
            if not title and image:
                title = str(image.get("alt", "")).strip()[:500]
            listings[asin] = AmazonListing(
                asin=asin, title=title or asin, url=url, image_url=image_url,
                store_variant=_store_variant(card, asin),
            )
            if len(listings) >= limit:
                break
    return list(listings.values())


def _store_pages(soup: BeautifulSoup | Tag, current_url: str, *, limit: int = 30,
) -> list[AmazonStorePage]:
    # 同一栏目可能在导航的多个位置出现，按栏目 ID 去重并跳过当前栏目。
    pages: dict[str, AmazonStorePage] = {}
    current_id = urlsplit(current_url).path.rsplit("/", 1)[-1].lower()
    for link in soup.select('a[href*="/stores/"][href*="/page/"]'):
        if (url := _linked_source("store", link)) is None:
            continue
        page_id = urlsplit(url).path.rsplit("/", 1)[-1].lower()
        title = _text(link, 150)
        if title and page_id != current_id and page_id not in pages:
            pages[page_id] = AmazonStorePage(title=title, url=url)
        if len(pages) >= limit:
            break
    return list(pages.values())


def _parse_report(
    mode: ResearchMode, url: str, html: bytes, samples: str
) -> AmazonResearchReport:
    soup = BeautifulSoup(html, "html.parser", from_encoding="utf-8")
    if soup.select_one('form[action*="validateCaptcha"], #captchacharacters'):
        raise AmazonFetchError("Amazon 返回了访问验证，请稍后重试。")
    report = AmazonResearchReport(
        mode=mode, source_url=url, title="", collected_at=datetime.now(UTC)
    )
    if mode == "product":
        report.product = _parse_product(soup, url, samples)
        report.title = report.product.title
        if not any(review.source == "amazon" for review in report.product.reviews):
            report.warnings.append("商品公开页面未返回评论样本，可粘贴已取得的评论片段后重新调研。")
        return report

    report.listings = _parse_listings(soup, mode)
    if mode == "bestsellers":
        report.title = next(
            (
                title for heading in soup.select("h1")
                if (title := _text(heading)) and title.startswith("Best Sellers in ")
            ),
            _first_text(soup, "h1", "title") or "Amazon Best Sellers",
        )
    else:
        report.title = _first_text(soup, "title") or "Amazon 品牌店铺"
    if mode == "store":
        report.store_pages = _store_pages(soup, url)
        if not report.listings and report.store_pages:
            # 店铺首页可能只有栏目导航，也返回报告供用户继续调研。
            report.warnings.append("当前页面未返回商品卡片，可继续调研下方店铺栏目。")
            return report
    if not report.listings:
        raise AmazonFetchError("Amazon 暂未返回可读取的榜单或店铺内容，请核对来源链接或稍后重试。")
    return report


async def _read_body(response: httpx.Response, limit: int) -> bytes:
    body = bytearray()
    async for chunk in response.aiter_bytes():
        if len(body) + len(chunk) > limit:
            raise AmazonFetchError("Amazon 响应超过大小限制，请换一个具体的页面链接。")
        body.extend(chunk)
    return bytes(body)


async def _fetch_page(
    client: httpx.AsyncClient, mode: ResearchMode | Literal["search"], url: str
) -> tuple[str, bytes]:
    try:
        async with asyncio.timeout(16):
            for _ in range(4):
                async with client.stream("GET", url) as response:
                    if response.is_redirect and (location := response.headers.get("location")):
                        # 每次跟随跳转前都重新校验目标地址是否属于支持的 Amazon 页面。
                        url = normalize_source(mode, urljoin(url, location))
                        continue
                    if response.status_code == 404:
                        raise AmazonFetchError("没有找到该 Amazon 页面，请核对来源链接或 ASIN。", "not_found", url)
                    if response.status_code in {401, 403, 429, 503}:
                        code = "blocked" if response.status_code in {401, 403} else "throttled"
                        raise AmazonFetchError(f"Amazon 页面暂不可访问（HTTP {response.status_code}）。", code, url)
                    response.raise_for_status()
                    return url, await _read_body(response, MAX_PAGE_BYTES)
    except AmazonSourceError as exc:
        raise AmazonFetchError("Amazon 要求登录或跳转到了其他页面，请核对来源链接。", "redirect", url) from exc
    except (httpx.TimeoutException, TimeoutError) as exc:
        raise AmazonFetchError("读取 Amazon 页面超时。", "timeout", url) from exc
    except httpx.HTTPError as exc:
        raise AmazonFetchError("Amazon 暂时无法访问，请稍后重试。", "network", url) from exc
    raise AmazonFetchError("Amazon 页面跳转次数过多，请使用最终页面链接。", "redirect", url)


def search_url(keyword: str) -> str:
    """Build the canonical US Amazon search URL shared by research flows."""
    value = " ".join(keyword.split())
    if not value or len(value) > 100 or any(ord(char) < 32 for char in value):
        raise AmazonSourceError("Amazon 搜索词无效。")
    return f"{AMAZON}/s?{urlencode({'k': value})}"


def suggestion_request(keyword: str) -> httpx.URL:
    return httpx.URL(
        SUGGESTIONS, params={"mid": "ATVPDKIKX0DER", "alias": "aps", "prefix": keyword}
    )


async def _fetch_suggestions(client: httpx.AsyncClient, keyword: str) -> list[str] | None:
    try:
        async with asyncio.timeout(7):
            async with client.stream("GET", suggestion_request(keyword)) as response:
                response.raise_for_status()
                data = json.loads(await _read_body(response, 256_000))
        return list(dict.fromkeys(
            value.strip()[:150] for item in data["suggestions"]
            if isinstance(item, dict) and isinstance(value := item.get("value"), str)
            and value.strip()
        ))[:10]
    except (AmazonFetchError, httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError):
        return None


async def run_amazon_research(request: AmazonResearchRequest) -> AmazonResearchReport:
    # 主流程先拉取公开页，再解析报告。店铺模式还会给栏目和商品补充中文译文。
    url = normalize_source(request.mode, request.source)
    keyword = request.keyword.strip()
    if _research_slots.locked():
        raise AmazonFetchError("当前调研请求较多，请稍后重试。")
    async with _research_slots, amazon_public_client() as client:
        if keyword:
            # 联想词是可选补充，可与页面并行读取，联想接口故障时仍返回页面报告。
            page, suggestions = await asyncio.gather(
                _fetch_page(client, request.mode, url),
                _fetch_suggestions(client, keyword),
                return_exceptions=True,
            )
            if isinstance(page, BaseException):
                raise page
            if isinstance(suggestions, BaseException):
                raise suggestions
            url, html = page
        else:
            url, html = await _fetch_page(client, request.mode, url)
            suggestions = []
        report = await asyncio.to_thread(
            _parse_report, request.mode, url, html, request.review_samples
        )
        if request.mode == "store":
            await localize_amazon_store(report)
    if keyword:
        report.keyword = keyword
        if suggestions is None:
            report.warnings.append("Amazon 搜索联想暂不可用，页面调研结果已保留。")
        else:
            report.suggestions = suggestions
    return report


def amazon_public_client() -> httpx.AsyncClient:
    client = httpx.AsyncClient(
        follow_redirects=False,
        trust_env=False,
        timeout=10,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    # 只设置本次请求的公开显示偏好。Amazon 仍可能按访问地返回其他显示币种或配送范围。
    client.cookies.set("i18n-prefs", "USD", domain="www.amazon.com")
    client.cookies.set("lc-main", "en_US", domain="www.amazon.com")
    return client
