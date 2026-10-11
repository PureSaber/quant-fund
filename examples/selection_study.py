"""Synthetic end-to-end frozen fund selection and ledger preview."""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from quant_fund.data import Dataset
from quant_fund.demo import create_demo
from quant_fund.engine import Ledger
from quant_fund.selection import persistence, preview_rebalance, screened_allocation, style_oos

root = Path(sys.argv[1])
root.mkdir(parents=True, exist_ok=False)
dataset = Dataset.load(create_demo(root / "synthetic-data"))
table, summary = persistence(dataset, decision="2024-01-02", evaluation="2024-07-31")
table.to_csv(root / "persistence.csv", index=False)
dates = pd.date_range("2022-01-31", "2024-07-31", freq="ME")
factors = pd.DataFrame(
    {
        "period_end": dates,
        "known_at": dates,
        "factor": "synthetic_market",
        "value": np.random.default_rng(7).normal(0, 0.03, len(dates)),
    }
)
path, style = style_oos(
    dataset, "PUB_EQ_A", factors, decision="2024-01-02", evaluation="2024-07-31"
)
path.to_csv(root / "style-oos.csv")
weights, _ = screened_allocation(dataset, "2024-01-02", ["PUB_EQ_A", "PUB_BD_A"], max_weight=0.6)
ledger = Ledger(dataset, 100_000)
ledger.advance("2024-01-02")
orders, preview = preview_rebalance(ledger, weights)
orders.to_csv(root / "preview.csv", index=False)
(root / "summary.json").write_text(
    json.dumps({"persistence": summary, "style": style, "preview": preview}, default=str, indent=2),
    encoding="utf-8",
)
print(root.resolve())
