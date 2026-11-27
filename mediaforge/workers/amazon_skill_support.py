import re
from datetime import date
from decimal import Decimal, InvalidOperation

from mediaforge.models.amazon_skills import SkillSection
from mediaforge.workers.amazon_fba_fees import currency_round

D = Decimal
UNKNOWN = "未知／待确认"


def number(value, *, percentage=False, count=False):
    try:
        result = D(value)
        if not result.is_finite() or not 0 <= result <= (100 if percentage else 100_000_000):
            raise ValueError
        if count and result != result.to_integral_value():
            raise ValueError
        return result
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("数值必须为有效的非负数；比例最高 100%，数量须为整数。") from exc


def positive(value, *, count=False):
    result = number(value, count=count)
    if not result:
        raise ValueError("此项数值必须大于 0。")
    return result


def money(value):
    return str(currency_round(value)) if value is not None else UNKNOWN


def percent(value):
    return money(value * 100) + "%" if value is not None else "未知（分母为零）"


def rate(value):
    return number(value, percentage=True) / 100


def pipe_rows(value, count):
    for line_no, line in enumerate(value.splitlines(), 1):
        if not line.strip():
            continue
        fields = [cell.strip() for cell in line.split("|")]
        if len(fields) != count or any(not cell for cell in fields):
            raise ValueError(f"第 {line_no} 行应有 {count} 个非空字段，以 | 分隔。")
        yield fields


def section(title, *rows, columns=None):
    return SkillSection(
        title=title,
        rows=[[str(value) for value in row] for row in rows],
        columns=columns or ["项目", "建议／结果"],
    )


def lines(value):
    return list(dict.fromkeys(line.strip() for line in value.splitlines() if line.strip()))


def day(value):
    if len(value) != 10 or value[4:5] != "-" or value[7:8] != "-":
        raise ValueError("日期须为 YYYY-MM-DD。")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("请输入有效日期（YYYY-MM-DD）。") from exc


def series(data, parse_value):
    samples = {}
    for raw_date, raw_value in pipe_rows(data, 2):
        timestamp = day(raw_date)
        if timestamp in samples:
            raise ValueError("同一指标每个采样日只允许一个值。")
        samples[timestamp] = parse_value(raw_value)
    return sorted(samples.items())


def ratio(part, total):
    if part > total:
        raise ValueError("分项数量不得大于对应总数。")
    return part / total if total else None


def ceil_count(value):
    return int(value.to_integral_value(rounding="ROUND_CEILING"))


def score_out_of_five(value):
    score = positive(value, count=True)
    if score > 5:
        raise ValueError("评分必须为 1–5 的整数。")
    return score


def asin(value):
    value = value.upper()
    if not re.fullmatch(r"[A-Z0-9]{10}", value):
        raise ValueError("ASIN 必须为 10 位字母或数字。")
    return value


def rank_series(v, label="自然排名"):
    samples = series(
        v["data"], lambda value: None if value == "未见" else positive(value, count=True)
    )
    rows = []
    prev = None
    for timestamp, value in samples:
        delta = prev - value if prev is not None and value is not None else None
        rows.append(
            (
                timestamp,
                int(value) if value is not None else "未进入本次样本",
                f"{int(delta):+d}" if delta is not None else "不可比",
            )
        )
        prev = value
    return section(
        label + "时间序列", *rows, columns=["采样日", "名次", "改善位数（正数表示上升）"]
    )
