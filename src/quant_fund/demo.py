"""Deterministic fixtures, explicitly classified as synthetic, never mixed with real data."""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .data import Fund, write_funds


def create_demo(directory, seed=20260926):
    root = Path(directory)
    if root.exists() and any(root.iterdir()):
        raise ValueError("样本输出目录非空，请选择新目录")
    root.mkdir(parents=True, exist_ok=True)
    calendar = pd.bdate_range("2022-01-03", "2026-10-30")
    rng = np.random.default_rng(seed)
    n = len(calendar)
    equity = rng.normal(0.00022, 0.010, n)
    bond = rng.normal(0.00012, 0.0018, n)
    trend = rng.normal(0.00017, 0.0055, n)
    # A known synthetic stress regime tests correlations and drawdowns without real-world claims.
    stress = (calendar >= "2024-04-01") & (calendar <= "2024-05-17")
    equity[stress] -= 0.0025
    month_ends = pd.Series(calendar, index=calendar).resample("ME").last().tolist()
    monthly_dates = [str(d.date()) for d in month_ends]
    specs = [
        ("PUB_EQ_A", "合成·宽基权益A", "示例管理人甲", "equity", "public", 0.95, 0.05, 0),
        ("PUB_EQ_B", "合成·价值权益B", "示例管理人乙", "equity", "public", 0.70, 0.20, 0.05),
        ("PUB_BD_A", "合成·中短债A", "示例管理人丙", "bond", "public", 0, 1.0, 0),
        ("PUB_BD_B", "合成·稳健债券B", "示例管理人丁", "bond", "public", 0.05, 0.85, 0),
        ("PRI_CTA", "合成·趋势CTA", "示例管理人戊", "cta", "private", -0.05, 0.10, 0.95),
        ("PRI_NEUTRAL", "合成·市场中性", "示例管理人己", "neutral", "private", 0.03, 0.20, 0.15),
        ("PRI_EQ", "合成·私募股票多头", "示例管理人庚", "equity", "private", 0.80, 0.05, 0.10),
    ]
    funds, rows = [], []
    for code, name, manager, strategy, kind, e, b, t in specs:
        private = kind == "private"
        funds.append(
            Fund(
                code,
                name,
                manager,
                strategy,
                kind,
                "2022-01-03",
                "2022-01-03",
                confirm_lag=3 if private else 1,
                settle_lag=6 if private else 3,
                notice_days=5 if private else 0,
                lock_days=90 if private else 0,
                buy_fee=0.001 if not private else 0,
                sell_tiers=[[7, 0.015], [30, 0.005], [100000, 0]],
                open_dates=monthly_dates if private else [],
                min_buy=10_000 if private else 100,
                max_stale_days=45 if private else 8,
            )
        )
        ret = e * equity + b * bond + t * trend + rng.normal(0.00004, 0.001, n)
        prices = pd.Series(np.cumprod(1 + ret), index=calendar)
        observations = prices.reindex(month_ends) if private else prices
        for date, nav in observations.items():
            lag = 3 if private else 1
            index = calendar.get_loc(date) + lag
            if index >= len(calendar):
                continue
            rows.append(
                {
                    "fund_id": code,
                    "nav_date": str(date.date()),
                    "known_at": str(calendar[index].date()),
                    "unit_nav": nav,
                    "total_return_nav": nav,
                    "source": f"synthetic_seed_{seed}",
                }
            )
    write_funds(funds, root / "funds.json")
    frame = pd.DataFrame(rows)
    frame.to_csv(root / "nav.csv", index=False)
    # A small import example, not an additional source for the research dataset.
    frame.groupby("fund_id", sort=False).head(3).to_excel(
        root / "nav-import-example.xlsx", index=False
    )
    pd.DataFrame({"date": calendar.strftime("%Y-%m-%d")}).to_csv(root / "calendar.csv", index=False)
    pd.DataFrame(columns=["fund_id", "ex_date", "pay_date", "known_at", "cash_per_share"]).to_csv(
        root / "distributions.csv", index=False
    )
    metadata = {
        "classification": "synthetic",
        "label": "合成数据演示",
        "seed": seed,
        "default_as_of": "2026-08-31",
        "default_start": "2024-01-02",
        "calendar_source": "合成工作日日历，不代表中国交易所节假日",
        "known_at_policy": "模拟公募T+1、私募T+3获知；各产品条款为合成设定",
        "notes": "不包含真实基金、实际收益或投资建议；示例净值已扣基金层费用。",
    }
    (root / "dataset.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return root
