import asyncio
import json
import re
from datetime import UTC, datetime
from string import ascii_lowercase
from urllib.parse import urlencode
from uuid import uuid4

import httpx

from mediaforge.models.amazon_script_tools import (
    HsCandidate,
    HsInput,
    HsResult,
    KeywordEvidence,
    KeywordInput,
    KeywordResult,
    ListingEvidence,
    ListingInput,
    ListingResult,
    PpcInput,
    PpcResult,
    ProductEvidence,
    QuerySample,
)
from mediaforge.rag.amazon_research import (
    AmazonFetchError,
    AmazonSourceError,
    _fetch_page,
    _fetch_suggestions,
    _parse_report,
    _read_body,
    amazon_public_client,
    normalize_source,
    suggestion_request,
)
from mediaforge.rag.amazon_store_collection import _currency

HTS = "https://hts.usitc.gov"
HTS_SEARCH = f"{HTS}/reststop/search"
CBP_RULINGS = "https://rulings.cbp.gov/"
_slots = asyncio.Semaphore(4)
_EVIDENCE_META = {
    "asin", "source_url", "collected_at", "status", "reason", "field_sources", "missing_fields",
}


def normalize_hs_code(value: str) -> str:
    return re.sub(r"[.\s]", "", value)


def keyword_queries(keyword: str) -> list[str]:
    return [
        keyword,
        *(f"{prefix} {keyword}" for prefix in ("best", "cheap", "top")),
        *(f"{keyword} {letter}" for letter in ascii_lowercase),
    ]


async def sample_keywords(body: KeywordInput) -> KeywordResult:
    allowed = keyword_queries(body.keyword)
    if body.queries and not set(body.queries) <= set(allowed):
        raise AmazonSourceError("重试查询必须来自同一种子词的扩展范围。")
    queries = list(dict.fromkeys(body.queries or allowed))
    started = datetime.now(UTC)
    slots = asyncio.Semaphore(3)
    samples: dict[str, QuerySample] = {}

    async def collect(client: httpx.AsyncClient, query: str):
        async with slots:
            words = await _fetch_suggestions(client, query)
        samples[query] = QuerySample(
            query=query,
            source_url=str(suggestion_request(query)),
            collected_at=datetime.now(UTC),
            status="failed" if words is None else "success" if words else "empty",
            suggestions=words or [],
            reason="Amazon 联想接口暂不可用。" if words is None else None,
        )

    # 请求级时间预算到达后保留先完成的查询；未取到的查询可单独重试。
    if _slots.locked():
        raise AmazonFetchError("采样请求较多，请稍后重试。", "busy")
    async with _slots, amazon_public_client() as client:
        try:
            async with asyncio.timeout(18):
                await asyncio.gather(*(collect(client, query) for query in queries))
        except TimeoutError:
            pass
    ordered = [
        samples.get(query)
        or QuerySample(
            query=query,
            source_url=str(suggestion_request(query)),
            collected_at=datetime.now(UTC),
            status="failed",
            suggestions=[],
            reason="本批采样达到时间上限。",
        )
        for query in queries
    ]
    grouped: dict[str, KeywordEvidence] = {}
    for sample in ordered:
        for word in sample.suggestions:
            label = " ".join(word.split())
            key = label.casefold()
            if key == body.keyword.casefold():
                continue
            item = grouped.setdefault(key, KeywordEvidence(keyword=label, queries=[]))
            if sample.query not in item.queries:
                item.queries.append(sample.query)
    failed = sum(item.status == "failed" for item in ordered)
    status = (
        "failed"
        if failed == len(ordered)
        else "partial_fail"
        if failed
        else "success"
        if grouped
        else "empty"
    )
    return KeywordResult(
        report_id=uuid4(),
        collected_at=started,
        keyword=body.keyword,
        samples=ordered,
        keywords=list(grouped.values()),
        status=status,
        warnings=["联想样本不代表实际搜索量；每个查询最多保留 10 条。"],
    )


async def _sample_product(client: httpx.AsyncClient, url: str, listing: bool):
    asin = url.rsplit("/", 1)[-1]
    final_url = url
    model = ListingEvidence if listing else ProductEvidence
    fields = set(model.model_fields) - _EVIDENCE_META
    try:
        final_url, html = await _fetch_page(client, "product", url)
        if final_url.rsplit("/", 1)[-1] != asin:
            raise AmazonFetchError("Amazon 跳转到其他 ASIN，未将该商品归入样本。", "redirect")
        report = await asyncio.to_thread(_parse_report, "product", final_url, html, "")
        product = report.product
        assert product is not None  # 商品模式解析失败会抛出 AmazonFetchError。
        values = product.model_dump(include=fields)
        values.update(price_currency=_currency(product.price))
        values = {key: None if value == [] else value for key, value in values.items()}
        return model(
            asin=asin,
            source_url=final_url,
            collected_at=report.collected_at,
            status="success",
            field_sources={key: final_url for key in sorted(values) if values[key] is not None},
            missing_fields=sorted(key for key in fields if values.get(key) is None),
            **values,
        )
    except AmazonFetchError as exc:
        return model(
            asin=asin,
            source_url=final_url,
            collected_at=datetime.now(UTC),
            status="failed",
            reason=str(exc),
            missing_fields=sorted(fields),
        )


async def sample_products(body: ListingInput | PpcInput) -> ListingResult | PpcResult:
    listing = isinstance(body, ListingInput)
    sources = [body.source] if listing else body.sources
    urls = list(dict.fromkeys(normalize_source("product", source) for source in sources))
    if _slots.locked():
        raise AmazonFetchError("采样请求较多，请稍后重试。", "busy")
    started = datetime.now(UTC)
    slots = asyncio.Semaphore(3)
    results: dict[str, ProductEvidence] = {}

    async def collect(client: httpx.AsyncClient, url: str):
        async with slots:
            results[url] = await _sample_product(client, url, listing)

    async with _slots, amazon_public_client() as client:
        try:
            async with asyncio.timeout(22):
                await asyncio.gather(*(collect(client, url) for url in urls))
        except TimeoutError:
            pass
    model = ListingEvidence if listing else ProductEvidence
    rows = [
        results.get(url)
        or model(
            asin=url.rsplit("/", 1)[-1],
            source_url=url,
            collected_at=datetime.now(UTC),
            status="failed",
            reason="本批采样达到时间上限。",
            missing_fields=sorted(set(model.model_fields) - _EVIDENCE_META),
        )
        for url in urls
    ]
    if listing:
        return ListingResult(
            report_id=uuid4(),
            collected_at=started,
            status=rows[0].status,
            listing=ListingEvidence.model_validate(rows[0].model_dump()),
            warnings=["图片数为公开图组中实际识别的去重图片数；缺失不代表商品没有该内容。"],
        )
    failed = sum(row.status == "failed" for row in rows)
    return PpcResult(
        report_id=uuid4(),
        collected_at=started,
        status="failed" if failed == len(rows) else "partial_fail" if failed else "success",
        competitors=rows,
        warnings=["评价量采用页面公开的评分数，可能包含共享变体评分，不等于文字评论条数。"],
    )


def _hts_text(item: dict, key: str, limit: int = 2000) -> str | None:
    value = item.get(key)
    return " ".join(value.split())[:limit] or None if isinstance(value, str) else None


async def lookup_hs(body: HsInput) -> HsResult:
    started = datetime.now(UTC)
    query = body.query
    compact = normalize_hs_code(query)
    by_code = bool(re.fullmatch(r"[0-9]+", compact))
    if by_code and len(compact) not in {4, 6, 8, 10}:
        raise AmazonSourceError("请输入 4、6、8 或 10 位的 HS / HTSUS 编码。")
    if by_code:
        query = ".".join(filter(None, (compact[:4], compact[4:6], compact[6:8], compact[8:])))
    link = f"{HTS}/?{urlencode({'query': query})}"
    pending = [
        "核对材质、用途、构造、归类总规则及章注；候选条目不是正式归类。",
        "查询现行表；申报时核对进口日的 HTSUS 版本、附注与 CBP 裁定。",
    ]
    if _slots.locked():
        raise AmazonFetchError("查询请求较多，请稍后重试。", "busy")
    try:
        async with (
            _slots,
            asyncio.timeout(9),
            httpx.AsyncClient(
                follow_redirects=False,
                trust_env=False,
                timeout=7,
            ) as client,
        ):
            async with client.stream("GET", HTS_SEARCH, params={"keyword": query}) as response:
                response.raise_for_status()
                data = json.loads(await _read_body(response, 1_000_000))
        if not isinstance(data, list) or (data and not any(isinstance(x, dict) for x in data)):
            raise ValueError("unexpected USITC response")
    except (ValueError, httpx.HTTPError, AmazonFetchError, TimeoutError):
        return HsResult(
            report_id=uuid4(),
            collected_at=started,
            status="failed",
            query=body.query,
            source_url=link,
            observed_count=0,
            candidates=[],
            pending=pending,
            warnings=["USITC 暂时无法读取，可打开官方链接查询或稍后重试。"],
        )
    candidates: dict[str, HsCandidate] = {}
    for row in data:
        if not isinstance(row, dict):
            continue
        code, desc = _hts_text(row, "htsno", 15), _hts_text(row, "description")
        if not code or not desc or not re.fullmatch(r"[0-9]{4}(?:\.[0-9]{2}){0,3}", code):
            continue
        digits = normalize_hs_code(code)
        if (by_code and not digits.startswith(compact)) or (not by_code and code.startswith("99")):
            continue
        general = _hts_text(row, "general")
        candidates.setdefault(
            code,
            HsCandidate(
                code=code,
                description=desc,
                general_rate_text=general,
                special_rate_text=_hts_text(row, "special"),
                column_2_rate_text=_hts_text(row, "other"),
                official_effective_period=_hts_text(row, "effectivePeriod"),
                source_url=f"{HTS}/?{urlencode({'query': code})}",
                pending=["该条未列一般税率，须核对下级或上级带税率的税目。"] if not general else [],
            ),
        )
    rows = (
        sorted(candidates.values(), key=lambda candidate: candidate.code)
        if by_code
        else list(candidates.values())
    )
    return HsResult(
        report_id=uuid4(),
        collected_at=started,
        status="success" if rows else "empty",
        query=body.query,
        source_url=link,
        observed_count=len(rows),
        candidates=rows[:20],
        pending=pending,
        warnings=["请优先使用英文品名、材质关键词或已知编码；未命中不代表无适用编码。"],
    )
