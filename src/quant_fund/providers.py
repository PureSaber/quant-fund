"""Opt-in public data collection. Fetching today's history is not a historic PIT archive."""

from pathlib import Path

import numpy as np
import pandas as pd

from .data import day, validate_nav


def fetch_akshare(fund_code: str, output, observed_date=None):
    import akshare as ak

    if len(fund_code) != 6 or not fund_code.isdigit():
        raise ValueError("公募代码必须为6位数字")
    target = Path(output)
    if target.exists() or target.with_suffix(".raw.csv").exists():
        raise ValueError("采集输出已存在；请使用新的快照路径")
    observed = day(observed_date or pd.Timestamp.today().normalize())
    raw = ak.fund_open_fund_info_em(symbol=fund_code, indicator="单位净值走势")
    required = {"净值日期", "单位净值", "日增长率"}
    if raw.empty or not required.issubset(raw):
        raise ValueError("AKShare接口字段变化或返回空数据")
    raw = raw.sort_values("净值日期").reset_index(drop=True)
    growth = pd.to_numeric(raw["日增长率"], errors="raise") / 100
    if not np.isfinite(growth.iloc[1:]).all() or (growth.iloc[1:] <= -1).any():
        raise ValueError("日收益率不完整，无法重建分红再投资收益；拒绝使用累计净值替代")
    growth.iloc[0] = 0  # Initial NAV is a normalization anchor, not an imputed return.
    frame = pd.DataFrame(
        {
            "fund_id": fund_code,
            "nav_date": pd.to_datetime(raw["净值日期"]),
            "known_at": observed,
            "unit_nav": pd.to_numeric(raw["单位净值"]),
            "total_return_nav": (1 + growth).cumprod(),
            "source": f"AKShare/Eastmoney snapshot {observed.date()}",
        }
    )
    frame = validate_nav(frame)
    target.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(target, index=False)
    raw.to_csv(target.with_suffix(".raw.csv"), index=False)
    return {
        "rows": len(frame),
        "known_at": str(observed.date()),
        "note": "首次采集历史统一按采集日获知，不可用于此前的时点回测。分红账本需另行提供。",
    }
