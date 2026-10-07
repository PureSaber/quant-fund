"""Opt-in public data collection. Fetching today's history is not a historic PIT archive."""

import re
from decimal import Decimal, localcontext
from pathlib import Path

import pandas as pd

from .data import day, validate_nav


def _nav_curve(raw, value_column):
    if raw.empty or not {"净值日期", value_column}.issubset(raw):
        raise ValueError("AKShare接口字段变化或返回空净值数据")
    raw = raw.copy()
    raw["净值日期"] = pd.to_datetime(raw["净值日期"], errors="raise").dt.normalize()
    if raw["净值日期"].isna().any() or raw["净值日期"].duplicated().any():
        raise ValueError("净值日期缺失或重复")
    raw = raw.sort_values("净值日期").reset_index(drop=True)
    values = [Decimal(str(value)) for value in raw[value_column]]
    if any(not value.is_finite() or value <= 0 for value in values):
        raise ValueError("净值必须为有限正数")
    return raw, values


def _cash_distributions(raw):
    if raw.empty:
        return {}
    formats = {
        "每10份分红": (r"每10份派现金([0-9]+(?:\.[0-9]+)?)元", Decimal(10)),
        "每份分红": (r"每份派现金([0-9]+(?:\.[0-9]+)?)元", Decimal(1)),
    }
    columns = set(formats).intersection(raw.columns)
    if "除息日" not in raw or len(columns) != 1:
        raise ValueError("分红字段或每份单位不明确")
    column = columns.pop()
    pattern, divisor = formats[column]
    dates = pd.to_datetime(raw["除息日"], errors="raise").dt.normalize()
    if dates.isna().any() or dates.duplicated().any():
        raise ValueError("分红除息日缺失或重复")
    amounts = []
    for value in raw[column]:
        match = re.fullmatch(pattern, str(value))
        if match is None or Decimal(match[1]) <= 0:
            raise ValueError("分红金额格式不明确或不是正数")
        amounts.append(Decimal(match[1]) / divisor)
    return dict(zip(dates, amounts))


def _reinvested_nav(unit_dates, units, cumulative, dividends):
    first, last = unit_dates.iloc[0], unit_dates.iloc[-1]
    cash = {date: value for date, value in dividends.items() if first < date <= last}
    if set(cash).difference(unit_dates):
        raise ValueError("分红除息日缺少单位净值，不能插值重建收益")
    cash_total = sum(cash.values(), Decimal(0))
    # Cumulative NAV is only an independent cash-sum check, never the return series.
    cash_change = (cumulative[-1] - units[-1]) - (cumulative[0] - units[0])
    if cash_change != cash_total:
        raise ValueError("分红总额与累计净值的现金分项不一致，拒绝生成复权序列")
    with localcontext() as context:
        context.prec = 40
        wealth = Decimal(1)
        values = [wealth]
        for index in range(1, len(units)):
            wealth *= (units[index] + cash.get(unit_dates.iloc[index], Decimal(0))) / units[
                index - 1
            ]
            values.append(wealth)
    return values


def fetch_akshare(fund_code: str, output, observed_date=None):
    import akshare as ak

    if len(fund_code) != 6 or not fund_code.isdigit():
        raise ValueError("公募代码必须为6位数字")
    target = Path(output)
    raw_paths = {
        "单位净值走势": target.with_suffix(".raw.csv"),
        "累计净值走势": target.with_suffix(".cumulative.raw.csv"),
        "分红送配详情": target.with_suffix(".dividends.raw.csv"),
        "拆分详情": target.with_suffix(".splits.raw.csv"),
    }
    if any(path.exists() for path in [target, *raw_paths.values()]):
        raise ValueError("采集输出已存在；请使用新的快照路径")
    observed = day(observed_date or pd.Timestamp.today().normalize())
    sources = {
        indicator: ak.fund_open_fund_info_em(symbol=fund_code, indicator=indicator)
        for indicator in raw_paths
    }
    if not sources["拆分详情"].empty:
        raise ValueError("公开采集尚不支持份额拆分，拒绝推测复权比例")
    raw, units = _nav_curve(sources["单位净值走势"], "单位净值")
    cumulative_raw, cumulative = _nav_curve(sources["累计净值走势"], "累计净值")
    if not raw["净值日期"].equals(cumulative_raw["净值日期"]):
        raise ValueError("单位净值与累计净值日期不一致，拒绝补值")
    total_returns = _reinvested_nav(
        raw["净值日期"], units, cumulative, _cash_distributions(sources["分红送配详情"])
    )
    frame = pd.DataFrame(
        {
            "fund_id": fund_code,
            "nav_date": pd.to_datetime(raw["净值日期"]),
            "known_at": observed,
            "unit_nav": [str(value) for value in units],
            "total_return_nav": [str(value) for value in total_returns],
            "source": f"AKShare/Eastmoney cash-dividend reinvestment snapshot {observed.date()}",
        }
    )
    frame = validate_nav(frame)
    target.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(target, index=False)
    for indicator, path in raw_paths.items():
        sources[indicator].to_csv(path, index=False)
    return {
        "rows": len(frame),
        "known_at": str(observed.date()),
        "return_basis": "unit_nav_and_cash_distributions",
        "note": "首次采集历史统一按采集日获知，不可用于此前的时点回测。分红账本需另行提供。",
    }
