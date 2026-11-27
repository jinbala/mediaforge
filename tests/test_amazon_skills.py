import httpx
import pytest

from mediaforge.gateway.main import app
from mediaforge.gateway.middleware.observability import _should_log_body
from mediaforge.gateway.middleware.rate_limit import limiter
from mediaforge.models.amazon_skills import SkillRequest
from mediaforge.workers.amazon_skills import run_skill

BASE = "/api/v1/research/amazon/skills"
AUTH = {"X-Api-Key": "test-api-key-starter"}
FBA = {
    "tool": "fba",
    "dimensions_in": ["6", "4", "0.5"],
    "weight_lb": "0.125",
    "category": "home_kitchen",
    "price_usd": "10",
    "ship_date": "2026-10-15",
    "storage_start": "2026-09-30",
    "storage_days": 2,
    "selling_plan": "individual",
}


@pytest.mark.parametrize(
    ("req", "values", "expected"),
    [
        (
            "REQ-045",
            {"product": "上衣", "audience": "通勤", "benefits": "棉质", "evidence": "面料单"},
            "棉质",
        ),
        ("REQ-046", {"product": "上衣", "goal": "首单", "budget": "100", "phase": "新品"}, "70.00"),
        ("REQ-047", {"keywords": "COTTON cotton linen shirt", "excluded": "linen"}, "cotton shirt"),
        (
            "REQ-048",
            {
                "source": "授权报表 2026-01",
                "report_type": "搜索查询表现",
                "data": "shirt|1|40|10|20|4",
            },
            "25.00%",
        ),
        (
            "REQ-048",
            {"source": "授权报表 2026-01", "report_type": "购物篮", "data": "shirt|20|100"},
            "20.00%",
        ),
        ("REQ-049", {"product": "上衣", "mark": "文字", "status": "申请中"}, "brandregistry"),
        (
            "REQ-050",
            {
                "audience": "新客",
                "goal": "转化",
                "price": "20",
                "cost": "11",
                "discount": "10",
                "quantity": "10",
                "inventory": "8",
            },
            "56.00",
        ),
        (
            "REQ-051",
            {"product": "上衣", "price": "21", "comp": "20", "inventory": "0", "delivery": "FBA"},
            "1.00",
        ),
        ("REQ-052", {"product": "上衣", "restriction": "被拒绝"}, "保存提交参考号"),
        (
            "REQ-053",
            {"product": "上衣", "source": "授权样本", "data": "B000000001|20|4.5|5|棉质|两色"},
            "https://www.amazon.com/dp/B000000001",
        ),
        (
            "REQ-054",
            {
                "price": "20",
                "cost": "10",
                "discount": "10",
                "fee": "1",
                "fixed": "5",
                "stack": "10",
                "quantity": "10",
            },
            "47.00",
        ),
        (
            "REQ-055",
            {
                "source": "授权报表",
                "zone": "America/Los_Angeles",
                "data": "08:00|10|50|6",
                "acos": "30",
            },
            "20.00%",
        ),
        (
            "REQ-056",
            {
                "price": "20",
                "cost": "10",
                "inventory": "5",
                "data": "秒杀|10|5|1|10\n折扣|10|0|0|10",
            },
            "40.00",
        ),
        (
            "REQ-057",
            {"product": "上衣", "audience": "回访", "goal": "转化", "budget": "100"},
            "60.00",
        ),
        (
            "REQ-058",
            {
                "product": "上衣",
                "audience": "通勤",
                "benefits": "棉质",
                "story": "从工艺开始",
                "evidence": "面料单",
            },
            "从工艺开始",
        ),
        (
            "REQ-060",
            {
                "product": "上衣",
                "quantity": "9",
                "boxes": "2",
                "handling": "易碎",
                "label_type": "FNSKU",
            },
            "1 箱 × 4 件",
        ),
    ],
)
def test_skill_products(req, values, expected):
    report = run_skill(req, SkillRequest(values=values))
    assert report.req == req and report.site == "US"
    assert expected in "\n".join(
        cell for section in report.sections for row in section.rows for cell in row
    )
    assert all(
        len(row) == len(section.columns) for section in report.sections for row in section.rows
    )


async def test_authorization_catalog_and_shared_fba():
    limiter.reset()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test.local"
    ) as client:
        assert (await client.get(BASE)).status_code == 401
        assert (await client.post(BASE + "/REQ-059", json={})).status_code == 401
        assert not _should_log_body(BASE + "/REQ-048")
        catalog = (await client.get(BASE, headers=AUTH)).json()
        assert [row["req"] for row in catalog] == [f"REQ-{i:03}" for i in range(45, 61)]
        calculator = await client.post("/api/v1/research/amazon/tools", headers=AUTH, json=FBA)
        skill = await client.post(
            BASE + "/REQ-059",
            headers=AUTH,
            json={"values": {"cost": "2", "quantity": "3"}, "fba": FBA},
        )
        assert skill.status_code == calculator.status_code == 200, skill.text
        result = skill.json()
        assert result["fba"]["lines"] == calculator.json()["lines"]
        assert result["sections"][0]["rows"][-1] == ["订单贡献 USD", "5.64"]
        no_rate = await client.post(
            BASE + "/REQ-059",
            headers=AUTH,
            json={
                "values": {"cost": "2", "quantity": "3"},
                "fba": {**FBA, "ship_date": "2050-01-01"},
            },
        )
        assert no_rate.json()["sections"][0]["rows"][-1][-1] == "待确认"
        invalid = await client.post(
            BASE + "/REQ-056",
            headers=AUTH,
            json={
                "values": {"cost": "2", "inventory": "2", "price": "5", "data": "秒杀|105|3|0|2"}
            },
        )
        assert invalid.status_code == 422
