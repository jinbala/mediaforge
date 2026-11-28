import json
from collections import OrderedDict
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from mediaforge.models.amazon_research import AmazonProduct
from mediaforge.models.amazon_research_run import (
    AmazonEvidence,
    AmazonResearchRun,
    AmazonResearchRunRequest,
    AmazonResearchSnapshot,
)
from mediaforge.rag import amazon_store_translation as translation

SOURCE = "https://www.amazon.com/dp/B000000001"


def make_run(mode):
    now = datetime.now(UTC)
    return AmazonResearchRun(
        id=uuid4(),
        mode=mode,
        source=SOURCE,
        status="running",
        stage="queued",
        canonical_source=SOURCE,
        created_at=now,
        updated_at=now,
        attempt=1,
        snapshot=AmazonResearchSnapshot(),
        inputs=AmazonResearchRunRequest(mode=mode, source=SOURCE),
    )


@pytest.fixture
def configured_model(monkeypatch):
    monkeypatch.setattr(translation, "_translations", OrderedDict())
    monkeypatch.setattr(
        translation, "get_settings", lambda: SimpleNamespace(openrouter_api_key="test-only")
    )


@pytest.mark.parametrize(
    "mode,key",
    [
        ("store", "store-translation"),
        ("bestsellers", "category-translation"),
        ("product", "product-translation"),
    ],
)
async def test_stage_skips_unchanged_and_retries_only_missing_fields(mode, key, monkeypatch):
    monkeypatch.setattr(translation, "_translations", OrderedDict())
    run = make_run(mode)
    product = AmazonProduct(
        asin="B000000001",
        url=SOURCE,
        title="Travel trousers",
        image_brief="Fixture",
        category="Outdoor clothing",
        description="Water-resistant travel trousers.",
        highlights=["Two zip pockets", "已是中文", "42", SOURCE],
    )
    run.snapshot.field_translations[product.category] = "户外服装"
    other_step = AmazonEvidence(
        key="category-analysis",
        kind="analysis",
        title="报告",
        url=SOURCE,
        status="success",
        attempts=1,
    )
    run.snapshot.evidence.append(other_step)
    checkpointed = []

    async def checkpoint(stage):
        row = next(item for item in run.snapshot.evidence if item.key == key)
        checkpointed.append((stage, row.status))

    batches = []

    async def backend(titles, _colors):
        pending = [item.title for item in titles if not item.title_zh]
        batches.append(pending)
        for item in titles:
            if item.title == "Two zip pockets":
                item.title_zh = "两个拉链口袋"
            elif item.title == "Water-resistant travel trousers.":
                item.title_zh = "防泼水旅行长裤"
            elif len(batches) > 1 and item.title == "Travel trousers":
                item.title_zh = "旅行长裤"
        return [product.title] if len(batches) == 1 else []

    def stage():
        return translation.TranslationStage(
            run,
            key,
            "翻译",
            lambda: translation.TranslationInputs(
                titles=[product], details=translation.product_detail_fields([product])
            ),
            checkpoint,
        )

    assert await stage().execute(backend, lambda: None)
    row = run.snapshot.evidence[-1]
    assert row.key == key and row.status == "incomplete" and row.attempts == 1
    assert run.snapshot.translation_missing == [product.title]
    assert not await stage().execute(backend, lambda: None)
    assert len(batches) == 1 and row.attempts == 1
    row.status = "pending"
    assert await stage().execute(backend, lambda: None)
    assert product.title == "Travel trousers" and product.title_zh == "旅行长裤"
    assert product.description == "Water-resistant travel trousers."
    assert run.snapshot.field_translations == {
        "Travel trousers": "旅行长裤",
        "Outdoor clothing": "户外服装",
        "Water-resistant travel trousers.": "防泼水旅行长裤",
        "Two zip pockets": "两个拉链口袋",
    }
    assert run.snapshot.translation_missing == []
    assert batches[1] == [product.title]
    assert checkpointed == [
        (key, "pending"),
        (key, "incomplete"),
        (key, "pending"),
        (key, "success"),
    ]
    assert other_step.status == "success" and other_step.attempts == 1

    fresh_stage = stage()
    product.description = "Same product, newly observed description."
    fresh_stage.refresh()
    assert row.status == "pending" and run.snapshot.translation_status == "pending"


async def test_model_batches_are_bounded_and_validate_typed_results(configured_model, monkeypatch):
    oversized = "Z" * (translation._SOURCE_LIMIT + 1)
    product = AmazonProduct(
        asin="B000000001",
        url=SOURCE,
        title="Travel trousers",
        image_brief="Fixture",
        category="Outdoor clothing",
        description="Water-resistant travel trousers.",
        highlights=["Two zip pockets", "Two zip pockets", oversized, "已是中文", SOURCE],
    )
    fields = translation.product_detail_fields([product])
    fields.extend(translation.DetailField("highlight", f"Feature {index}") for index in range(100))
    payloads = []

    async def chat(_client, messages, **kwargs):
        raw = messages[1]["content"]
        assert len(raw) <= translation._BATCH_CHARS
        batch = json.loads(raw)
        payloads.append(batch)
        return json.dumps(
            {
                key: "户外用品" if value["text"] != "Two zip pockets" else "untranslated"
                for key, value in batch.items()
            }
            | {"unknown": "未提供的字段"}
        )

    monkeypatch.setattr(translation.OpenRouterClient, "chat_completion", chat)
    missing = await translation.localize_research_fields(fields, [])
    assert len(payloads) == 2 and all(len(batch) <= 35 for batch in payloads)
    sent = [value for batch in payloads for value in batch.values()]
    assert sent[:3] == [
        {"field": "category", "text": "Outdoor clothing"},
        {"field": "description", "text": "Water-resistant travel trousers."},
        {"field": "highlight", "text": "Two zip pockets"},
    ]
    assert sum(value["text"] == "Two zip pockets" for value in sent) == 1
    assert {oversized, "Two zip pockets", "Feature 99"} <= set(missing)
    assert "已是中文" not in missing and SOURCE not in missing
    assert fields[0].title_zh == "户外用品" and fields[2].title_zh is None


async def test_stage_fallback_keeps_known_translation_and_does_not_log_exception_text(monkeypatch):
    warnings = []
    monkeypatch.setattr(translation.logger, "warning", lambda *args: warnings.extend(args))
    run = make_run("store")
    known = translation.DetailField("category", "Outdoor clothing", "户外服装")
    unknown = translation.DetailField("highlight", "Two zip pockets")
    stage = translation.TranslationStage(
        run,
        "store-translation",
        "翻译",
        lambda: translation.TranslationInputs(details=[known, unknown]),
        AsyncMock(),
    )
    failing = AsyncMock(side_effect=RuntimeError("sensitive provider content"))
    assert await stage.execute(failing, lambda: None)
    row = run.snapshot.evidence[0]
    assert row.status == "failed" and row.code == "translation_error"
    assert run.snapshot.field_translations == {"Outdoor clothing": "户外服装"}
    assert run.snapshot.translation_missing == ["Two zip pockets"]
    assert not any("sensitive provider content" in str(arg) for arg in warnings)
