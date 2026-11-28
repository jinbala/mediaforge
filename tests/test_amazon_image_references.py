from io import BytesIO
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from bs4 import BeautifulSoup
from PIL import Image
from pydantic import ValidationError

from mediaforge.models.amazon_research import AmazonProduct
from mediaforge.models.job import GenerationContext, SkuInput
from mediaforge.rag.amazon_research import _parse_product
from mediaforge.workers.image import amazon_reference
from mediaforge.workers.image.detail_page import DetailPageWorker
from mediaforge.workers.image.main_image import MainImageWorker

CDN = "https://m.media-amazon.com/images/I/"


def test_gallery_keeps_current_variant_and_deduplicates_thumbnail_sizes():
    soup = BeautifulSoup(f"""
        <span id="productTitle">Work trousers</span>
        <img id="landingImage" src="{CDN}main._AC_SX300_.jpg">
        <script>var data = {{'colorImages': {{'initial': A.$.parseJSON('[
          {{"hiRes":"{CDN}main.jpg","thumb":"{CDN}main-thumb._SS40_.jpg"}},
          {{"hiRes":null,"large":"{CDN}scene.jpg","thumb":"{CDN}scene-thumb._SS40_.jpg"}},
          {{"hiRes":"https://example.test/foreign.jpg","large":"{CDN}feature.jpg"}}
        ]'), 'Blue': [{{"large": "{CDN}other-color.jpg"}}]}}}};</script>
        <ul id="altImages">
          <li class="item"><img src="{CDN}scene-thumb._SS40_.jpg"></li>
          <li class="item"><img src="{CDN}macro._SS40_.jpg" data-old-hires="{CDN}macro.jpg"></li>
          <li class="item videoThumbnail"><img src="{CDN}video.jpg"></li>
        </ul>
    """, "html.parser")
    product = _parse_product(soup, "https://www.amazon.com/dp/B0DMT1FFQF", "")
    assert product.gallery_image_urls == [CDN + name + ".jpg" for name in ("main", "scene", "feature", "macro")]
    assert product.image_count == 4
    old_product = product.model_dump(exclude={"gallery_image_urls"})
    assert AmazonProduct.model_validate(old_product).gallery_image_urls == []


@pytest.mark.parametrize("role,url", [
    ("video", CDN + "image.jpg"),
    ("main", "https://m.media-amazon.com.evil.test/images/I/image.jpg"),
    ("main", "https://m.media-amazon.com:443/images/I/image.jpg"),
    ("scene", "https://m.media-amazon.com/images/G/image.jpg"),
])
def test_job_validates_roles_and_cdn(role, url):
    with pytest.raises(ValidationError):
        SkuInput(sku_id="test", product_image_url="own.png", product_name="Trousers",
                 category="apparel", target_platforms=["amazon"], output_types=["main_image"],
                 amazon_image_references={role: {"image_url": url}})


@pytest.mark.asyncio
async def test_reference_loader_validates_bytes_and_never_follows_redirects(monkeypatch):
    image = BytesIO()
    Image.new("RGB", (12, 12)).save(image, "JPEG")
    real_client = httpx.AsyncClient
    requested = []

    def serve(request):
        requested.append(str(request.url))
        if request.url.path.endswith("redirect.jpg"):
            return httpx.Response(302, headers={"Location": "https://example.test/other.jpg"})
        if request.url.path.endswith("broken.jpg"):
            return httpx.Response(200, content=b"not an image")
        return httpx.Response(200, content=image.getvalue())

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(
        transport=httpx.MockTransport(serve), **kwargs,
    ))
    assert await amazon_reference.load_amazon_reference(CDN + "valid.jpg") == image.getvalue()
    for name in ("redirect", "broken"):
        with pytest.raises(ValueError, match="无法读取这张 Amazon 参考图"):
            await amazon_reference.load_amazon_reference(CDN + name + ".jpg")
    assert requested == [CDN + name + ".jpg" for name in ("valid", "redirect", "broken")]


@pytest.mark.asyncio
async def test_workers_use_only_the_selected_amazon_image_for_each_shot(monkeypatch):
    store = AsyncMock()
    store.get_asset.return_value = None
    monkeypatch.setattr("mediaforge.db.job_store.JobStore", lambda *_: store)
    monkeypatch.setattr("mediaforge.db.get_engine", lambda: None)
    monkeypatch.setattr("mediaforge.db.get_redis", AsyncMock())
    storage = AsyncMock()
    storage.save_asset.return_value = "saved.png"
    monkeypatch.setattr("mediaforge.workers.image.base.get_storage", lambda: storage)

    async def load(url):
        if url.endswith("feature.jpg"):
            raise ValueError("无法读取这张 Amazon 参考图")
        return url.encode()

    loader = AsyncMock(side_effect=load)
    monkeypatch.setattr("mediaforge.workers.image.base.load_amazon_reference", loader)
    client = AsyncMock()
    client.generate_image.return_value = b"generated"
    client.model_name = MagicMock(return_value="test-model")
    sku = SkuInput(sku_id="test", product_image_url="own.png", product_name="Trousers",
                   category="apparel", target_platforms=["amazon", "shopee"],
                   output_types=["main_image", "detail_page"],
                   amazon_image_references={role: {"image_url": CDN + role + ".jpg",
                                                    "direction": f"{role}-only-note"}
                                            for role in ("main", "scene", "feature", "closeup")})
    context = GenerationContext(product_image_path="own.png", input_matches=True, ref_image_path="generic.jpg")
    hero = MainImageWorker(client=client, model="pro")
    details = DetailPageWorker(client=client, model="pro")
    for worker in (hero, details):
        monkeypatch.setattr(worker, "_validate_generated_image", AsyncMock(return_value={"passed": True}))
    monkeypatch.setattr(details, "_validate_detail_set", AsyncMock(return_value={"passed": True}))

    assert len((await hero.run(sku, "tenant", "job", context)).success) == 2
    result = await details.run(sku, "tenant", "job", context)
    assert [(item.platform, item.error) for item in result.failed] == [
        ("amazon:feature", "无法读取这张 Amazon 参考图"),
    ]
    assert [item.platform for item in result.success] == [
        "amazon:scene", "amazon:closeup", "shopee:scene", "shopee:feature", "shopee:closeup",
    ]
    assert [call.args[0] for call in loader.await_args_list] == [CDN + role + ".jpg" for role in ("main", "scene", "feature", "closeup")]
    for call, role in zip(client.generate_image.await_args_list, ("main", None, "scene", "closeup", None, None, None), strict=True):
        assert call.kwargs["product_image_path"] == "own.png"
        assert call.kwargs["ref_image_path"] == ((CDN + role + ".jpg").encode() if role else "generic.jpg")
        for other_role in ("main", "scene", "feature", "closeup"):
            assert (f"{other_role}-only-note" in call.kwargs["prompt"]) == (role == other_role)
