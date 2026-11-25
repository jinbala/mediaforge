import base64
import io
import json
from types import SimpleNamespace
from urllib.parse import urlencode

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook
from PIL import Image

from mediaforge.gateway.routers import rag
from mediaforge.rag import product_share


@pytest.fixture
def imports(monkeypatch):
    app = FastAPI()
    app.include_router(rag.router)
    app.dependency_overrides[rag.get_tenant_only] = lambda: SimpleNamespace(tenant_id="tenant")
    monkeypatch.setattr(rag, "get_settings", lambda: SimpleNamespace(max_upload_size=1024 * 1024))
    monkeypatch.setattr(rag.limiter, "enabled", False)
    writes = []

    async def index(*args):
        writes.append(args)

    monkeypatch.setattr(rag, "_embed_and_upsert", index)
    return TestClient(app), writes


def test_share_preview_reads_product_cover_without_submitting(imports, monkeypatch):
    client, writes = imports
    short_url = "https://v.douyin.com/a_B-23/"
    cover_url = "https://p3-item.ecombdimg.com/img/ecom-shop-material/cover.png"
    cover = io.BytesIO()
    Image.new("RGB", (800, 800)).save(cover, format="PNG")
    title = '牛仔裤，"浅色"'
    detail = urlencode({"id": "3853123", "goods_detail": json.dumps({
        "title": title, "img": {"url_list": [cover_url]},
    })})
    forwarded_urls = []

    def upstream(request):
        url = str(request.url)
        forwarded_urls.append(url)
        if url == short_url:
            return httpx.Response(302, headers={"location": f"https://haohuo.jinritemai.com/ecommerce/trade/detail/index.html?{detail}"})
        assert url == cover_url
        return httpx.Response(200, content=cover.getvalue())

    async_client = httpx.AsyncClient
    monkeypatch.setattr(product_share.httpx, "AsyncClient", lambda **options: async_client(transport=httpx.MockTransport(upstream), **options))
    response = client.post("/api/v1/rag/ingest/link/preview", json={"text": r"【抖音商城】https\://v.douyin.com/a_B-23/ 牛仔裤"})
    assert response.status_code == 200, response.text
    assert response.json() == {
        "product_id": "DY-3853123",
        "title": title,
        "image_data": "data:image/png;base64," + base64.b64encode(cover.getvalue()).decode("ascii"),
    }
    assert forwarded_urls == [short_url, cover_url]
    assert writes == []


def test_share_preview_handles_pdd_and_rejects_unexpected_redirect(imports, monkeypatch):
    client, writes = imports
    forwarded_urls = []

    def upstream(request):
        forwarded_urls.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://127.0.0.1/ecommerce/trade/detail/index.html"})

    async_client = httpx.AsyncClient
    monkeypatch.setattr(product_share.httpx, "AsyncClient", lambda **options: async_client(transport=httpx.MockTransport(upstream), **options))
    endpoint = "/api/v1/rag/ingest/link/preview"
    pdd = client.post(endpoint, json={"text": "https://mobile.yangkeduo.com/goods.html?ps=abc"})
    assert pdd.status_code == 422
    assert "拼多多" in pdd.json()["detail"]
    assert forwarded_urls == []
    assert client.post(endpoint, json={"text": "https://v.douyin.com/abc/"}).status_code == 422
    assert forwarded_urls == ["https://v.douyin.com/abc/"]
    assert writes == []


def test_17zwd_preview_uses_gallery_cover_and_preserves_sku(imports, monkeypatch):
    client, writes = imports
    product_url = "https://cs.17zwd.com/item/168390742"
    cover_url = "https://img03-gms.17zwd.com/imgextra/123/cover.jpg"
    cover = io.BytesIO()
    Image.new("RGB", (80, 80)).save(cover, format="JPEG")

    async def read_page(url):
        assert url == product_url
        return "加绒弯刀裤", ["https://example.com/cover.jpg", cover_url]

    def upstream(request):
        assert str(request.url) == cover_url
        assert request.headers["Referer"] == product_url
        return httpx.Response(200, content=cover.getvalue())

    async_client = httpx.AsyncClient
    monkeypatch.setattr(product_share.zwd, "read_product", read_page)
    monkeypatch.setattr(product_share.httpx, "AsyncClient", lambda **options: async_client(transport=httpx.MockTransport(upstream), **options))
    response = client.post("/api/v1/rag/ingest/link/preview", json={"text": f"17网分享 {product_url}?action=1"})
    assert response.status_code == 200, response.text
    assert response.json() == {
        "product_id": "17ZWD-168390742",
        "title": "加绒弯刀裤",
        "image_data": "data:image/jpeg;base64," + base64.b64encode(cover.getvalue()).decode("ascii"),
    }
    assert writes == []


def test_17zwd_preview_reports_login_for_manual_fallback(imports, monkeypatch):
    client, writes = imports

    async def read_page(url):
        raise product_share.zwd.PageUnavailable("17 网要求登录后查看该商品。")

    monkeypatch.setattr(product_share.zwd, "read_product", read_page)
    response = client.post("/api/v1/rag/ingest/link/preview", json={"text": "https://cs.17zwd.com/item/168390742"})
    assert response.status_code == 422
    assert "要求登录" in response.json()["detail"]
    assert writes == []


@pytest.mark.parametrize("excel", [False, True])
def test_preview_and_complete_import_with_assigned_images(imports, excel):
    client, writes = imports
    records = [
        ("product_id", "category", "style", "color", "image_path", "image_url"),
        (540, "裙装", "通勤", "蓝", r"C:\photos\PIC.JPG", ""),
        ("B", "裙装", "通勤", "红", "", ""),
        ("C", "裙装", "通勤", "白", "other.png", "https://example.com/c.png"),
        (540, "重复", "通勤", "蓝", "", ""),
        (None, "无 SKU", "通勤", "白", "", ""),
    ]
    if excel:
        workbook = Workbook()
        for row in records:
            workbook.active.append(row)
        content = io.BytesIO()
        workbook.save(content)
        catalog = ("catalog.xlsx", content.getvalue())
    else:
        catalog = ("catalog.csv", "\n".join(",".join(str(cell) if cell is not None else "" for cell in row) for row in records).encode("gb18030"))

    response = client.post("/api/v1/rag/ingest/preview", files={"file": catalog})
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["skipped"] == 2
    assert [row["product_id"] for row in preview["rows"]] == ["540", "B", "C"]
    assert preview["rows"][0]["image_name"] == "pic.jpg"
    assert preview["rows"][2]["has_image_url"] is True
    assert writes == []

    response = client.post("/api/v1/rag/ingest", files={"file": catalog, "images": ("PIC.JPG", b"legacy")})
    assert response.status_code == 422
    assert "B（未填写图片文件名）" in response.json()["detail"]
    assert "540" not in response.json()["detail"]
    assert writes == []

    response = client.post(
        "/api/v1/rag/ingest",
        files=[("file", catalog), ("images", ("image-1.jpg", b"first")), ("images", ("image-2.jpg", b"second"))],
        data={"image_assignments": json.dumps({"540": "image-1.jpg", "B": "image-2.jpg", "C": "image-2.jpg"})},
    )
    assert response.status_code == 200, response.text
    assert response.json()["accepted"] == 3
    rows, tenant, uploaded = writes[0]
    assert tenant == "tenant"
    assert [(row["image_path"], row["image_url"]) for row in rows] == [
        ("image-1.jpg", ""), ("image-2.jpg", ""), ("image-2.jpg", ""),
    ]
    assert uploaded["image-1.jpg"][0] == b"first"
    assert uploaded["image-2.jpg"][0] == b"second"


def test_preview_reports_invalid_excel_and_missing_headers(imports):
    client, writes = imports
    corrupt = client.post("/api/v1/rag/ingest/preview", files={"file": ("bad.xlsx", b"not a file")})
    assert corrupt.status_code == 422
    missing = client.post("/api/v1/rag/ingest/preview", files={"file": ("bad.csv", b"product_id,image_path\n1,image.jpg")})
    assert missing.status_code == 422
    assert "category, color, style" in missing.json()["detail"]
    assert writes == []
