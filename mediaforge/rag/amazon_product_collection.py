import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime
from urllib.parse import urlencode, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup, Tag

from mediaforge.models.amazon_product import (
    ProductCandidate,
    ProductMedia,
    ProductPage,
    ProductReviewPage,
    ProductReviewSample,
    ProductSearch,
    ProductSearchRow,
)
from mediaforge.rag.amazon_product_screenshot import capture_product_screenshot
from mediaforge.rag.amazon_research import (
    AMAZON,
    ASIN,
    AmazonFetchError,
    _amazon_image_url,
    _fetch_page,
    _first_text,
    _image_from_tag,
    _parse_product,
    _text,
    normalize_source,
    search_url,
)

MAX_SEARCH_ROWS = 30
MAX_REVIEW_ROWS = 10
MAX_GALLERY_ITEMS = 50
MAX_REVIEW_SAMPLES = 16
_REVIEW_STARS = {"one": 1, "two": 2, "three": 3}
_PRICE_CURRENCY = re.compile(
    r"(?<![A-Z])(?:CA?\$|US\$|AU?\$|SG?\$|\$|(?:CAD|USD|AUD|SGD|GBP|EUR|JPY)(?![A-Z]))"
    r"|[£€¥]",
    re.I,
)
_CURRENCY_SYMBOLS = {
    "C$": "CAD", "CA$": "CAD", "US$": "USD", "$": "USD", "A$": "AUD", "AU$": "AUD",
    "S$": "SGD", "SG$": "SGD", "£": "GBP", "€": "EUR", "¥": "JPY",
}
_PRICE_NUMBER = re.compile(r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]{1,2})?")
_FEATURE_BULLETS = "#feature-bullets li"
_IMPORTANT_INFORMATION = (
    "#important-information",
    "#importantInformation_feature_div",
    "#safetyInformation_feature_div",
    "#legalDisclaimer_feature_div",
)


def _amount(value: str | None) -> tuple[float | None, str | None]:
    if not value:
        return None, None
    markers = _PRICE_CURRENCY.findall(value.upper())
    currencies = {_CURRENCY_SYMBOLS.get(marker, marker) for marker in markers}
    if len(currencies) > 1:
        return None, None
    currency = next(iter(currencies), None)
    # 范围价不冒充单价，避免将下限用于财务估算。
    number = _PRICE_CURRENCY.sub(" ", value).strip()
    if not _PRICE_NUMBER.fullmatch(number):
        return None, currency
    amount = float(number.replace(",", ""))
    return (amount if amount <= 1_000_000 else None), currency


def _field_specs(soup: BeautifulSoup) -> dict[str, str]:
    specs: dict[str, str] = {}
    for row in soup.select(
        "#productDetails_feature_div tr, #productDetails_techSpec_section_1 tr, "
        "#productDetails_detailBullets_sections1 tr, #technicalSpecifications_section_1 tr",
    ):
        label = _text(row.select_one("th, td:first-child"), 160)
        value = _text(row.select_one("td:last-child"), 800)
        if label and value and label.casefold() != value.casefold():
            specs[label.rstrip(": ")] = value
        if len(specs) >= 80:
            break
    for item in soup.select(
        "#detailBullets_feature_div li, #detailBulletsWrapper_feature_div li",
        limit=80,
    ):
        label_node = item.select_one(".a-text-bold")
        if not label_node:
            continue
        label = (_text(label_node, 160) or "").rstrip(": ")
        value = _text(item, 800)
        prefix = _text(label_node, 160)
        if value and prefix and value.startswith(prefix):
            value = value[len(prefix):].strip(" :\u200e\u200f")
        if label and value:
            specs.setdefault(label, value)
        if len(specs) >= 80:
            break
    return specs


def _image_dimensions(image: Tag) -> tuple[str | None, int | None, int | None]:
    candidates: list[tuple[str, int | None, int | None]] = []
    for attr in ("data-a-dynamic-image",):
        try:
            mapping = json.loads(str(image.get(attr, "{}")))
        except (ValueError, RecursionError):
            mapping = {}
        if isinstance(mapping, dict):
            for raw_url, dimensions in mapping.items():
                url = _amazon_image_url(raw_url)
                if not url:
                    continue
                width = height = None
                if (isinstance(dimensions, list) and len(dimensions) >= 2
                        and all(isinstance(size, (int, float)) for size in dimensions[:2])):
                    width, height = int(dimensions[0]), int(dimensions[1])
                candidates.append((url, width, height))
    for attr in ("data-old-hires", "src", "data-src"):
        if url := _amazon_image_url(image.get(attr)):
            candidates.append((url, None, None))
    if not candidates:
        return None, None, None
    return max(candidates, key=lambda candidate: (candidate[1] or 0) * (candidate[2] or 0))


def _product_images(soup: BeautifulSoup) -> list[ProductMedia]:
    images: list[ProductMedia] = []
    seen: set[str] = set()
    # Amazon 图库常把高分辨率链接放在 colorImages.initial 脚本数据中。
    for script in soup.select("script"):
        raw = script.string or script.get_text()
        if "colorImages" not in raw or len(raw) > 2_000_000:
            continue
        start = raw.find("{", raw.find("colorImages") + len("colorImages"))
        if start < 0:
            continue
        try:
            data, _ = json.JSONDecoder().raw_decode(raw[start:])
        except (ValueError, RecursionError):
            continue
        initial = data.get("initial", []) if isinstance(data, dict) else []
        for entry in initial if isinstance(initial, list) else []:
            if not isinstance(entry, dict):
                continue
            url = _amazon_image_url(entry.get("hiRes") or entry.get("large"))
            if url and url not in seen:
                seen.add(url)
                images.append(ProductMedia(url=url))
            if len(images) >= MAX_GALLERY_ITEMS:
                return images
    nodes = soup.select(
        "#landingImage, #imgBlkFront, #ebooksImgBlkFront, #altImages li.item img, "
        "#imageBlockThumbs img, #main-image-container img",
        limit=MAX_GALLERY_ITEMS,
    )
    for image in nodes:
        url, width, height = _image_dimensions(image)
        url = url or _image_from_tag(image)
        if not url or url in seen:
            continue
        seen.add(url)
        label = str(image.get("alt") or image.get("title") or "").strip()[:300] or None
        images.append(ProductMedia(url=url, label=label, width=width, height=height, poster=None))
    return images


def _safe_video_url(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 2000 or re.search(r"[\s\\\x00-\x1f\x7f]", value):
        return None
    url = urljoin(AMAZON + "/", value.strip())
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return None
    host = parsed.hostname.lower() if parsed.hostname else ""
    if (parsed.scheme != "https" or parsed.username or parsed.password or port is not None
            or not (host == "amazon.com" or host.endswith(".amazon.com")
                    or host == "media-amazon.com" or host.endswith(".media-amazon.com"))):
        return None
    return url


def _product_videos(soup: BeautifulSoup) -> list[ProductMedia]:
    videos: list[ProductMedia] = []
    seen: set[str] = set()
    for node in soup.select(
        "video, #altImages li.videoThumbnail, [data-video-url], [data-a-video-src]",
        limit=20,
    ):
        raw_url = (node.get("src") or node.get("data-video-url")
                   or node.get("data-a-video-src"))
        if not raw_url and isinstance(node, Tag):
            source = node.select_one("source[src]")
            raw_url = source.get("src") if source else None
        url = _safe_video_url(raw_url)
        if not url or url in seen:
            continue
        seen.add(url)
        poster = _amazon_image_url(node.get("poster"))
        label = _text(node, 250) or str(node.get("title") or "").strip() or None
        videos.append(ProductMedia(url=url, label=label, width=None, height=None, poster=poster))
    return videos


def _rating_distribution(soup: BeautifulSoup) -> dict[int, float]:
    distribution: dict[int, float] = {}
    for row in soup.select("#histogramTable tr, #reviewsMedley .cr-widget-FocalReviews"):
        text = " ".join(filter(None, [_text(row, 500), str(row.get("aria-label", ""))]))
        stars = re.search(r"\b([1-5])\s*(?:star|stars)\b", text, re.I)
        percent = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
        if stars and percent:
            distribution[int(stars[1])] = float(percent[1])
    return distribution


def _variant_asins(soup: BeautifulSoup, current: str) -> list[str]:
    values: list[str] = []
    for node in soup.select(
        "#twister [data-asin], #variation_size_name [data-defaultasin], "
        "#variation_color_name [data-defaultasin], "
        "select[name^='dropdown_selected_'] option[value]",
        limit=200,
    ):
        for attr in ("data-asin", "data-defaultasin", "value"):
            raw = node.get(attr)
            if isinstance(raw, str) and ASIN.fullmatch(raw) and raw.upper() != current:
                values.append(raw.upper())
    return list(dict.fromkeys(values))[:100]


def _parent_asin(soup: BeautifulSoup, current: str) -> str | None:
    for selector in ("#parentAsin", "input[name='parentAsin']", "#twister[data-asin]"):
        node = soup.select_one(selector)
        if not node:
            continue
        value = node.get("value") or node.get("data-asin")
        if isinstance(value, str) and ASIN.fullmatch(value) and value.upper() != current:
            return value.upper()
    return None


def _evidence(soup: BeautifulSoup, specs: dict[str, str], images: list[ProductMedia],
              videos: list[ProductMedia], important: list[str], reviews: list[ProductReviewSample],
              price_amount: float | None) -> dict[str, str]:
    evidence: dict[str, str] = {}
    for field, selector in (
        ("title", "#productTitle"),
        ("brand", "#bylineInfo"),
        ("price_amount", "#corePrice_feature_div"),
        ("rating", "#acrPopover"),
        ("specs", "#productDetails_feature_div, #productDetails_techSpec_section_1"),
        ("aplus", "#aplus, #aplus_feature_div, .aplus-v2"),
        ("rating_distribution", "#histogramTable"),
        ("reviews", '[data-hook="review"]'),
        ("images", "#altImages, #imageBlock"),
        ("videos", "video, #altImages li.videoThumbnail"),
        ("parent_asin", "#parentAsin, input[name='parentAsin']"),
    ):
        if soup.select_one(selector):
            evidence[field] = selector
    if specs:
        evidence["specs"] = evidence.get("specs", "#detailBullets_feature_div")
    if images and "images" not in evidence:
        evidence["images"] = "#landingImage"
    if videos and "videos" not in evidence:
        evidence["videos"] = "video[src]"
    if important:
        selectors = [
            selector for selector in (*_IMPORTANT_INFORMATION, "#feature-bullets")
            if soup.select_one(selector)
        ]
        if selectors:
            evidence["important"] = ", ".join(selectors)
    if reviews and "reviews" not in evidence:
        evidence["reviews"] = '[data-hook="review"]'
    if price_amount is not None and "price_amount" not in evidence:
        evidence["price_amount"] = ".a-price .a-offscreen"
    return evidence


def _parse_product_page(html: bytes, url: str, review_samples: str) -> ProductPage:
    soup = BeautifulSoup(html, "html.parser", from_encoding="utf-8")
    if soup.select_one('form[action*="validateCaptcha"], #captchacharacters'):
        raise AmazonFetchError("Amazon 返回了访问验证，请稍后重试。", "captcha", url)
    if not _first_text(soup, "#productTitle"):
        raise AmazonFetchError(
            "Amazon 暂未返回可读取的商品详情，请核对 ASIN 或稍后重试。", "not_found", url,
        )
    product = _parse_product(soup, url, review_samples[:4000])
    specs = _field_specs(soup)
    warnings = [
        text for section in soup.select(", ".join(_IMPORTANT_INFORMATION), limit=8)
        if (text := _text(section, 800))
    ]
    bullets = [
        text for item in soup.select(_FEATURE_BULLETS, limit=20)
        if (text := _text(item.select_one(".a-list-item") or item, 800))
    ]
    ranks = [f"Best Sellers Rank: {rank}" for rank in product.sales_ranks if rank]
    important = list(dict.fromkeys([*warnings, *bullets, *ranks]))[:24]
    aplus = list(dict.fromkeys(
        text for block in soup.select(
            "#aplus, #aplus_feature_div, #aplusBrandStory_feature_div, "
            ".aplus-v2, .a-plus-content, #aplusPageWidget",
            limit=20,
        )
        if (text := _text(block, 3000))
    ))
    images = _product_images(soup)
    videos = _product_videos(soup)
    price_amount, currency = _amount(product.price)
    collected_at = datetime.now(UTC)
    amazon_reviews, _ = _parse_reviews(html, url)
    reviews = amazon_reviews[:8]
    manual_reviews = [review for review in product.reviews if review.source == "manual"]
    for index, review in enumerate(manual_reviews, start=1):
        if len(reviews) >= MAX_REVIEW_SAMPLES:
            break
        reviews.append(ProductReviewSample(
            id=f"manual-{index}", text=review.text, source="manual", url=None, stars=None,
            date=None, variant=None, verified=None, helpful=None,
        ))
    return ProductPage(
        product=product,
        collected_at=collected_at,
        specs=specs,
        important=important,
        aplus=aplus,
        images=images,
        videos=videos,
        currency=currency,
        price_amount=price_amount,
        rating_distribution=_rating_distribution(soup),
        reviews=reviews,
        parent_asin=_parent_asin(soup, product.asin),
        variant_asins=_variant_asins(soup, product.asin),
        field_evidence=_evidence(
            soup, specs, images, videos, important, reviews, price_amount,
        ),
        screenshot_data_url=None,
    )


async def collect_product(
    client: httpx.AsyncClient, url: str, *, review_samples: str = "",
    include_screenshot: bool = False,
) -> ProductPage:
    canonical = normalize_source("product", url)
    requested_asin = canonical.rsplit("/", 1)[-1]
    final, html = await _fetch_page(client, "product", canonical)
    final_asin = final.rstrip("/").rsplit("/", 1)[-1].upper()
    if final_asin != requested_asin:
        raise AmazonFetchError(
            "商品链接跳转到其他 ASIN，未将其作为原商品返回。", "asin_redirect", final,
        )
    page = await asyncio.to_thread(_parse_product_page, html, final, review_samples)
    if include_screenshot:
        page.screenshot_data_url = await capture_product_screenshot(final, html)
    return page


def _sponsored(card: Tag) -> bool | None:
    if card.select_one(
        '[aria-label="Sponsored"], .puis-sponsored-label-text, '
        '[data-component-type*="sp-sponsored"], .s-sponsored-label-info-icon'
    ):
        return True
    if _text(card, 300) and re.search(r"\bSponsored\b", _text(card, 300) or "", re.I):
        return True
    return False


def _search_count(soup: BeautifulSoup) -> tuple[int | None, int | None, str | None]:
    text = _first_text(
        soup,
        "#search .s-desktop-toolbar .a-text-normal",
        "#search .s-desktop-toolbar .a-section .a-text-normal",
        "#search .s-result-info-bar",
        "#s-result-count",
        ".s-result-count",
    )
    if not text:
        return None, None, None
    total = re.search(r"of\s+(?P<over>over\s+)?(?P<count>[\d,]+)\s+results?", text, re.I)
    if total:
        count = int(total["count"].replace(",", ""))
        lower = count + 1 if total["over"] else count
        return lower, None if total["over"] else count, text
    total = re.search(r"(?:over\s+)?([\d,]+)\+?\s+results?", text, re.I)
    if total:
        count = int(total[1].replace(",", ""))
        return count, None, text
    return None, None, text


def _parse_search(html: bytes, query: str, url: str, collected_at: datetime) -> ProductSearch:
    soup = BeautifulSoup(html, "html.parser", from_encoding="utf-8")
    if soup.select_one('form[action*="validateCaptcha"], #captchacharacters'):
        raise AmazonFetchError("Amazon 搜索返回访问验证。", "captcha", url)
    rows: list[ProductSearchRow] = []
    seen: set[str] = set()
    cards = soup.select('[data-component-type="s-search-result"][data-asin]')
    for card in cards:
        asin = str(card.get("data-asin", "")).upper()
        if not ASIN.fullmatch(asin) or asin in seen:
            continue
        link = card.select_one("h2 a[href], a[href]:has(h2)")
        title = _first_text(card, "h2 a span", "h2")
        if not link or not title:
            continue
        product_url = urljoin(AMAZON, str(link.get("href", "")))
        try:
            product_url = normalize_source("product", product_url)
        except ValueError:
            continue
        if not product_url.endswith("/" + asin):
            continue
        seen.add(asin)
        price_text = _first_text(card, ".a-price .a-offscreen")
        price_amount, currency = _amount(price_text)
        brand = _first_text(
            card, '[data-cy="title-recipe"] .a-size-base-plus', ".a-row .a-size-base-plus",
        )
        rows.append(ProductSearchRow(
            asin=asin,
            title=title,
            url=product_url,
            rank=len(rows) + 1,
            brand=brand,
            price_amount=price_amount,
            currency=currency,
            sponsored=_sponsored(card),
        ))
        if len(rows) >= MAX_SEARCH_ROWS:
            break
    result_min, result_max, result_label = _search_count(soup)
    return ProductSearch(
        query=query,
        url=url,
        collected_at=collected_at,
        rows=rows,
        result_min=result_min,
        result_max=result_max,
        result_label=result_label,
    )


async def collect_search(client: httpx.AsyncClient, query: str) -> ProductSearch:
    value = query.strip()
    canonical = search_url(value)
    collected_at = datetime.now(UTC)
    final, html = await _fetch_page(client, "search", canonical)
    if final != canonical:
        raise AmazonFetchError("Amazon 搜索跳转到了不同查询，未将其结果混用。", "redirect", final)
    return _parse_search(html, value, final, collected_at)


def _review_url(asin: str, bucket: str) -> str:
    if not ASIN.fullmatch(asin):
        raise ValueError("asin must be a 10-character Amazon identifier")
    if bucket != "recent" and bucket not in _REVIEW_STARS:
        raise ValueError("unsupported review bucket")
    params = {"sortBy": "recent"}
    if bucket != "recent":
        params["filterByStar"] = f"{bucket}_star"
    params["pageNumber"] = "1"
    return normalize_source(
        "reviews", f"{AMAZON}/product-reviews/{asin.upper()}?{urlencode(params)}",
    )


def review_url(asin: str, bucket: str) -> str:
    return _review_url(asin, bucket)


def _parse_review_date(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    formats = ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d", "%m/%d/%Y")
    for date_format in formats:
        try:
            return datetime.strptime(value, date_format).date().isoformat()
        except ValueError:
            continue
    return value[:100] or None


def _review_link(item: Tag) -> str | None:
    link = item.select_one('a[data-hook="review-title"], a[data-hook="review-cite"]')
    if not link or not link.get("href"):
        return None
    url = urljoin(AMAZON, str(link["href"]))
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if parsed.scheme == "https" and parsed.netloc.lower() in {"www.amazon.com", "amazon.com"}:
        return url
    return None


def _parse_reviews(
    html: bytes, url: str, *, expected_stars: int | None = None,
) -> tuple[list[ProductReviewSample], bool]:
    soup = BeautifulSoup(html, "html.parser", from_encoding="utf-8")
    if soup.select_one('form[action*="validateCaptcha"], #captchacharacters'):
        raise AmazonFetchError("Amazon 评论页返回访问验证。", "captcha", url)
    rows: list[ProductReviewSample] = []
    seen: set[str] = set()
    limited = False
    for item in soup.select('[data-hook="review"]'):
        text = _first_text(item, '[data-hook="review-body"]')
        if not text:
            continue
        rating_text = _first_text(
            item, '[data-hook="review-star-rating"]', '[data-hook="cmps-review-star-rating"]',
        )
        rating_match = re.fullmatch(
            r"([1-5])(?:\.0+)?(?:\s+(?:out of 5(?:\s+stars?)?|stars?))?",
            rating_text or "", re.I,
        )
        stars = int(rating_match[1]) if rating_match else None
        if expected_stars is not None and stars != expected_stars:
            limited = True
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        review_id = str(item.get("id") or item.get("data-review-id") or "").strip()
        if not review_id:
            review_id = hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]
        date_text = _first_text(item, '[data-hook="review-date"]')
        date_match = re.search(r"\b(?:on\s+)?([A-Z][a-z]+\s+\d{1,2},\s+\d{4})\b", date_text or "")
        date = _parse_review_date(date_match[1] if date_match else date_text)
        variant = _first_text(item, '[data-hook="format-strip"]')
        verified = None
        if item.select_one('[data-hook="avp-badge"], [data-hook="verified-purchase"]'):
            verified = True
        elif re.search(r"\bVerified Purchase\b", _text(item, 1500) or "", re.I):
            verified = True
        helpful_text = _first_text(item, '[data-hook="helpful-vote-statement"]')
        helpful_match = re.search(r"([\d,]+|one)\b", helpful_text or "", re.I)
        rows.append(ProductReviewSample(
            id=review_id,
            text=text,
            source="amazon",
            url=_review_link(item),
            stars=stars,
            date=date,
            variant=variant,
            verified=verified,
            helpful=(1 if helpful_match and helpful_match[1].casefold() == "one"
                     else int(helpful_match[1].replace(",", "")) if helpful_match else None),
        ))
        if len(rows) >= MAX_REVIEW_ROWS:
            break
    page_text = (_text(soup, 2000) or "").casefold()
    restricted_phrases = (
        "sign in to see more reviews", "only showing reviews", "reviews are limited",
        "we couldn't find reviews", "we could not find reviews",
    )
    limited = limited or len(rows) >= MAX_REVIEW_ROWS or any(
        phrase in page_text for phrase in restricted_phrases
    )
    return rows, limited


async def collect_reviews(
    client: httpx.AsyncClient, asin: str, bucket: str,
) -> ProductReviewPage:
    url = review_url(asin.upper(), bucket)
    collected_at = datetime.now(UTC)
    final, html = await _fetch_page(client, "reviews", url)
    if final != url:
        raise AmazonFetchError("Amazon 评论页转向其他页面，未混用其内容。", "redirect", final)
    rows, limited = _parse_reviews(html, final, expected_stars=_REVIEW_STARS.get(bucket))
    return ProductReviewPage(
        bucket=bucket,
        url=final,
        collected_at=collected_at,
        rows=rows,
        limited=limited,
    )


def choose_queries(page: ProductPage, keyword: str = "") -> list[str]:
    title = " ".join(page.product.title.split())
    seed = " ".join(keyword.split()) or title
    category = " ".join((page.product.category or "").split())
    features = [" ".join(value.split()) for value in page.product.highlights if value.strip()]
    # 五个查询分别组合明确输入的关键词、商品标题、类目和已观测卖点，不凭空添加属性。
    proposals = [
        seed,
        f"{seed} {category}" if category else f"{seed} {title}",
        f"{seed} {title}",
        f"{seed} {features[0]}" if features else f"{seed} {category} products",
        f"{seed} {title} {features[0]}" if features else f"{seed} {title} comparison",
    ]
    queries: list[str] = []
    for proposal in proposals:
        query = " ".join(proposal.split())[:100].rstrip()
        if query and query.casefold() not in {value.casefold() for value in queries}:
            queries.append(query)
    # 当可观测文本很少时，用不同标题片段补齐，但不拼接无关属性。
    for word in title.split():
        candidate = f"{seed} {word}"[:100].rstrip()
        if candidate.casefold() not in {value.casefold() for value in queries}:
            queries.append(candidate)
        if len(queries) >= 5:
            break
    return queries[:5]


def choose_competitors(
    page: ProductPage, searches: list[ProductSearch],
) -> list[ProductCandidate]:
    target_asin = page.product.asin.upper()
    candidates: dict[str, dict] = {}
    stopwords = {"and", "for", "from", "the", "with"}

    def terms(value: str) -> set[str]:
        return {
            word for word in re.findall(r"[a-z0-9]+", value.casefold())
            if len(word) > 2 and word not in stopwords
        }

    source_terms = terms(page.product.title)
    accessory_terms = {
        "case", "cover", "replacement", "refill", "accessory", "accessories", "holder", "bag",
    }
    for search in searches:
        for row in search.rows:
            asin = row.asin.upper()
            if asin == target_asin or row.sponsored is True:
                continue
            candidate_terms = terms(row.title)
            if not candidate_terms & source_terms:
                continue
            # 避免主体商品搜索意外混入保护壳、替换件等配件结果。
            if candidate_terms & accessory_terms and not source_terms & accessory_terms:
                continue
            item = candidates.setdefault(asin, {
                "asin": asin,
                "title": row.title,
                "url": row.url,
                "brand": row.brand,
                "rank_total": 0,
                "rank_count": 0,
                "queries": [],
            })
            if search.query not in item["queries"]:
                item["queries"].append(search.query)
                item["rank_total"] += row.rank
                item["rank_count"] += 1
    ordered = sorted(
        candidates.values(),
        key=lambda item: (
            -len(item["queries"]),
            -(len(terms(item["title"]) & source_terms) / max(1, len(source_terms))),
            item["rank_total"] / item["rank_count"], item["asin"],
        ),
    )
    result: list[ProductCandidate] = []
    for item in ordered[:30]:
        reasons = [f"Appeared in Amazon search: {query}" for query in item["queries"]]
        source_brand = (page.product.brand or "").strip().casefold()
        candidate_brand = (item["brand"] or "").strip().casefold()
        if source_brand and candidate_brand and source_brand != candidate_brand:
            reasons.append(f"Different listed brand: {item['brand']}")
        result.append(ProductCandidate(
            asin=item["asin"],
            title=item["title"],
            url=item["url"],
            reasons=reasons,
            queries=item["queries"],
        ))
    return result
