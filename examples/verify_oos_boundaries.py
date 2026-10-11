"""Independent raw-NAV controls for coordinator finding C-FUND-01 (synthetic)."""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from quant_fund.data import Dataset, Fund, validate_nav
from quant_fund.selection import persistence, style_oos

root = Path(sys.argv[1])
root.mkdir(parents=True, exist_ok=False)
calendar = pd.bdate_range("2022-01-03", "2024-07-31")
levels = np.where(calendar >= "2024-01-15", 2.0, 1.0)
fund = Fund("F", "Synthetic", "Synthetic", "equity", "public", "2021-01-01", "2021-01-01")
nav = validate_nav(
    pd.DataFrame(
        {
            "fund_id": "F",
            "nav_date": calendar,
            "known_at": calendar,
            "unit_nav": levels,
            "total_return_nav": levels,
            "source": "synthetic-boundary-control",
        }
    )
)
dataset = Dataset(
    {"F": fund},
    nav,
    calendar,
    pd.DataFrame(columns=["fund_id", "ex_date", "pay_date", "known_at", "cash_per_share"]),
    {"classification": "synthetic"},
    "C-FUND-01-control",
)
cases = [
    ("ME", "2024-01-16", "2024-07-31", "2024-02-29", 1),
    ("ME", "2024-01-31", "2024-07-31", "2024-02-29", 0),
    ("W-FRI", "2024-01-17", "2024-03-01", "2024-01-26", 1),
    ("W-FRI", "2024-01-19", "2024-03-01", "2024-01-26", 0),
]
results = []
for frequency, decision, evaluation, first_endpoint, excluded in cases:
    ends = pd.date_range(calendar[0], calendar[-1], freq=frequency)
    factors = pd.DataFrame(
        {
            "period_end": ends,
            "known_at": ends,
            "factor": "market",
            "value": np.sin(np.arange(len(ends))) * 0.02,
        }
    )
    path, style = style_oos(
        dataset, "F", factors, decision=decision, evaluation=evaluation, frequency=frequency
    )
    ranks, info = persistence(
        dataset, decision=decision, evaluation=evaluation, frequency=frequency
    )
    # This control is independent of the interval helper and both return aggregators.
    raw = nav.loc[(nav.nav_date >= decision) & (nav.nav_date <= evaluation)]
    expected = float(raw.total_return_nav.iloc[-1] / raw.total_return_nav.iloc[0] - 1)
    assert expected == 0.0 and (path.fund_return == expected).all()
    assert ranks.test_return.iloc[0] == expected
    assert str(path.index[0].date()) == first_endpoint
    assert style["expected_periods"] == info["expected_periods"] == len(path) == 6
    assert style["excluded_periods"] == info["excluded_periods"] == excluded
    assert style["missing_periods"] == ranks.missing_test_periods.iloc[0] == 0
    results.append(
        {
            "frequency": frequency,
            "decision": decision,
            "evaluation": evaluation,
            "true_postdecision_raw_nav_return": expected,
            "first_style_period": str(path.index[0].date()),
            "first_style_return": float(path.fund_return.iloc[0]),
            "persistence_test_return": float(ranks.test_return.iloc[0]),
            "periods": len(path),
            "excluded": style["excluded_intervals"],
            "status": "pass",
        }
    )
    key = f"{frequency}-{decision}"
    path.to_csv(root / f"{key}-style.csv")
    ranks.to_csv(root / f"{key}-persistence.csv", index=False)
report = {"finding": "C-FUND-01", "evidence_kind": "synthetic", "cases": results}
(root / "independent-controls.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report, indent=2))
