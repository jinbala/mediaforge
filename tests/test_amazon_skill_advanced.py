import httpx
import pytest
import respx

from mediaforge.gateway.main import app
from mediaforge.gateway.middleware.rate_limit import limiter
from mediaforge.models.amazon_skills import SkillRequest
from mediaforge.rag.amazon_script_samples import HTS_SEARCH
from mediaforge.workers.amazon_skill_advanced_definitions import ADVANCED
from mediaforge.workers.amazon_skills import ADVANCED_HANDLERS, run_skill

SOURCE = "固定样本 · US · 2026-11-28"
ASIN = "B000000001"
PARENT = "B000000002"

# 每个编号验证一项有业务含义的真实产物，不只验证接口成功。
CASES = [
    (61, {"data": "US|4|2|20|标签待核对"}, "9.00"),
    (
        62,
        {
            "market": "JP · ja",
            "currency": "JPY",
            "price": "20",
            "fx": "150",
            "adjustment": "100",
            "content": "Shirt|シャツ|核对尺码",
        },
        "3100.00 JPY",
    ),
    (63, {"data": "S1|4|10|3|5|2|190"}, "交期内可能缺货"),
    (64, {"seed": "linen shirt", "data": "linen shirt|100|50|25", "modifiers": "men"}, "4.00"),
    (65, {"keyword": "linen", "data": "2026-01-01|12\n2026-01-02|5"}, "+7"),
    (66, {"audience": "通勤", "claims": "实测快干", "dimensions": "30 cm"}, "7 · 使用说明"),
    (
        67,
        {"title": "Shirt", "bullets": "cotton", "keywords": "shirt\nlinen", "evidence": "实测棉"},
        "linen",
    ),
    (68, {"min_clicks": "10", "data": "parts|10|12|0|不相关"}, "精准否定候选"),
    (69, {"data": "服装|4|2|20|-10"}, "11.25"),
    (70, {"price": "20", "cost": "12", "cvr": "10", "keywords": "cotton", "asins": ASIN}, "0.80"),
    (
        71,
        {"threshold": "10", "data": "2026-01-01|20|未见|无\n2026-01-02|15|14|促销"},
        "复核价格变动",
    ),
    (
        72,
        {"audience": "通勤", "features": "棉", "data": "供应商A|10|7|5|已核实", "budget": "100"},
        "预算内可送样",
    ),
    (73, {"data": "两件装|20|9|18|2"}, "10.00%"),
    (74, {"type": "儿童用品", "use": "3–6 岁塑料制品"}, "CPSC/CPSIA"),
    (75, {"audience": "通勤", "features": "面料", "dimensions": "40 cm"}, "侧光"),
    (76, {"data": "服装|5|1|50|1|可见证据"}, "90.00"),
    (
        77,
        {"price": "20", "cost": "5", "units": "10", "fees": "广告|2\n退款|1", "fixed": "20"},
        "100.00",
    ),
    (78, {"keyword": "linen", "asin": ASIN, "data": "2026-01-01|7\n2026-01-02|未见"}, "不可比"),
    (
        79,
        {
            "price": "20",
            "cost": "10",
            "floor_margin": "2",
            "ceiling": "24",
            "competitor": "14",
            "step": "2",
        },
        "18.00 USD",
    ),
    (80, {"data": "尺码|5|3|退货报表"}, "15.00"),
    (81, {"data": "1|尺码偏小\n5|材质柔软"}, "尺寸与合身"),
    (82, {"delivered": "10", "requested": "2", "issues": "需补尺寸图"}, "20.00%"),
    (
        83,
        {"rank": "100", "category": "服装", "data": "100|1|2|3\n200|0|1|2", "season": "淡季"},
        "1.00 / 2.00 / 3.00",
    ),
    (84, {"data": "linen|高|是|5|100|20"}, "20.00%"),
    (85, {"lead": "30", "data": "12|100|20|10|冬季素材"}, "至少提前 30 天"),
    (86, {"data": f"首页|{ASIN}|2|3|20|成熟\n新品|{ASIN}|2|3|20|成熟"}, "全店去重"),
    (
        87,
        {
            "dims": "12|12|12",
            "weight": "1",
            "divisor": "144",
            "months": "2",
            "storage": "1",
            "fulfillment": "3",
            "removal": "5",
            "fbm": "8",
        },
        "10.00",
    ),
    (
        88,
        {"brand": "品牌", "audience": "通勤", "story": "故事", "data": f"主栏目|{ASIN}|通勤图"},
        ASIN,
    ),
    (
        89,
        {
            "price": "20",
            "cost": "10",
            "discount": "10",
            "retained": "8",
            "subscribers": "10",
            "interval": "30",
        },
        "64.00",
    ),
    (
        90,
        {"reason": "记录不足", "evidence": "留存记录", "fixes": "遗漏|复核|签字"},
        "申诉行动计划草案",
    ),
    (
        91,
        {"launch": "2026-01-01", "data": "2026-01-15|100|1\n2026-01-31|50|3", "season": "淡季"},
        "50.00%",
    ),
    (92, {"theme": "颜色与尺码", "data": f"{PARENT}|{ASIN}|红|S", "same": "待核对"}, ASIN),
    (93, {"eligible": "未确认", "units": "10", "fee": "30", "cost": "5", "ship": "2"}, "100.00"),
    (94, {"price": "20", "fees": "3", "budget": "100", "data": "供应商A|10|5|30|已核实"}, "可送样"),
]


def payload(req, values):
    definition = ADVANCED[req]
    defaults = {"product": "测试商品", "source": SOURCE}
    return SkillRequest(
        values={
            f.key: values[f.key] if f.key in values else defaults[f.key]
            for f in definition.fields
            if f.key in values or f.key in defaults
        }
    )


@pytest.mark.parametrize(("index", "values", "expected"), CASES)
def test_outputs(index, values, expected):
    req = f"REQ-{index:03}"
    report = run_skill(req, payload(req, values))
    assert report.req == req and report.sections
    assert expected in "\n".join(sec.title for sec in report.sections) + "\n" + "\n".join(
        text for sec in report.sections for row in sec.rows for text in row
    )
    assert all(len(row) == len(sec.columns) for sec in report.sections for row in sec.rows)


def test_catalog_and_scope():
    assert set(ADVANCED) == {f"REQ-{i:03}" for i in range(61, 96)}
    assert set(ADVANCED_HANDLERS) == set(ADVANCED) - {"REQ-095"}


@pytest.mark.parametrize(
    ("index", "values"),
    [
        (63, {"data": "S|1|0|0|2|1|0\nS|1|0|0|2|1|0"}),
        (68, {"min_clicks": "10", "data": "term|2|4|3|相关"}),
        (
            79,
            {
                "price": "20",
                "cost": "16",
                "floor_margin": "4",
                "ceiling": "19",
                "competitor": "18",
                "step": "1",
            },
        ),
        (82, {"delivered": "8", "requested": "9", "issues": "尺寸"}),
        (86, {"data": f"首页|{ASIN}|2|3|20|成熟\n新品|{ASIN}|3|4|20|成熟"}),
        (92, {"theme": "颜色", "same": "待核对", "data": f"{ASIN}|{ASIN}|蓝|S"}),
    ],
)
def test_inconsistent_input_fails_without_report(index, values):
    req = f"REQ-{index:03}"
    with pytest.raises(ValueError):
        run_skill(req, payload(req, values))


@respx.mock
async def test_req095_uses_same_landed_and_hs_outputs():
    limiter.reset()
    respx.get(HTS_SEARCH).respond(
        200,
        json=[
            {
                "htsno": "8518.30.20.00",
                "description": "Headphones, other",
                "general": "Free",
            }
        ],
    )
    auth = {"X-Api-Key": "test-api-key-starter"}
    landed = {
        "tool": "landed",
        "origin": "CN",
        "hs_code": "8518.30.20.00",
        "as_of": "2026-11-28",
        "goods_value": "100",
        "freight": "20",
        "quantity": 3,
    }
    hs = {"tool": "hs", "query": "8518.30.20"}
    skill_url = "/api/v1/research/amazon/skills/REQ-095"
    tools_url = "/api/v1/research/amazon/tools"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test.local"
    ) as client:
        assert (await client.post(skill_url, json={"hs": hs})).status_code == 401
        first = await client.post(tools_url, headers=auth, json=landed)
        second = await client.post(tools_url, headers=auth, json=hs)
        response = await client.post(skill_url, headers=auth, json={"landed": landed, "hs": hs})
        assert response.status_code == first.status_code == second.status_code == 200, response.text
        report = response.json()
        volatile = {"report_id", "collected_at"}
        for field, direct in [("landed", first.json()), ("hs", second.json())]:
            assert {k: v for k, v in report[field].items() if k not in volatile} == {
                k: v for k, v in direct.items() if k not in volatile
            }
        assert report["landed"]["total_usd"] is None
        assert report["hs"]["observed_count"] == 1
        lookup = (await client.post(skill_url, headers=auth, json={"hs": hs})).json()
        assert lookup["landed"] is None and lookup["hs"]["candidates"]
        assert (await client.post(skill_url, headers=auth, json={})).status_code == 422
        other = await client.post(skill_url.replace("095", "061"), headers=auth, json={"hs": hs})
        assert other.status_code == 422
