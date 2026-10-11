import numpy as np
import pandas as pd
import pytest

from quant_fund.data import Dataset
from quant_fund.engine import Ledger
from quant_fund.selection import (
    factor_asof,
    persistence,
    preview_rebalance,
    screened_allocation,
    style_oos,
)


def test_factor_versions_are_not_backdated():
    factors = pd.DataFrame(
        {
            "period_end": ["2023-01-31"] * 2,
            "known_at": ["2023-02-01", "2024-01-01"],
            "factor": ["equity"] * 2,
            "value": [0.01, 9.0],
        }
    )
    assert factor_asof(factors, "2023-01-31").empty
    assert factor_asof(factors, "2023-02-01").equity.iloc[0] == 0.01


def test_native_frequency_style_and_frozen_training(demo_directory):
    data = Dataset.load(demo_directory)
    ends = pd.date_range("2022-01-31", "2025-12-31", freq="ME")
    rng = np.random.default_rng(17)
    factors = pd.DataFrame(
        {
            "period_end": ends,
            "known_at": ends,
            "factor": "market",
            "value": rng.normal(0, 0.03, len(ends)),
        }
    )
    path, info = style_oos(
        data, "PUB_EQ_A", factors, decision="2024-01-02", evaluation="2024-07-31"
    )
    assert len(path) == 7
    train = data.returns("2024-01-02", "ME", 24)["PUB_EQ_A"].dropna()
    factor = factor_asof(factors, "2024-01-02").reindex(train.index).market
    # Independent single-factor covariance/variance formula.
    slope = np.cov(train, factor, ddof=1)[0, 1] / np.var(factor, ddof=1)
    assert info["frozen_model"]["exposures"]["market"] == pytest.approx(slope)
    revised = data.nav.copy()
    revised.loc[revised.known_at > pd.Timestamp("2024-01-02"), "total_return_nav"] *= 2
    data.nav = revised
    _, changed = style_oos(
        data, "PUB_EQ_A", factors, decision="2024-01-02", evaluation="2024-07-31"
    )
    assert changed["frozen_model"] == info["frozen_model"]


def test_persistence_keeps_missing_funds(demo_directory):
    data = Dataset.load(demo_directory)
    data.nav = data.nav.loc[
        ~((data.nav.fund_id == "PUB_EQ_A") & (data.nav.nav_date > "2024-02-01"))
    ]
    table, info = persistence(data, decision="2024-01-02", evaluation="2024-07-31")
    assert "PUB_EQ_A" in set(table.fund_id)
    assert info["incomplete_funds"] > 0
    equities = table.loc[(table.kind == "public") & (table.strategy == "equity")]
    assert equities.test_rank.isna().all()


def test_existing_optimizer_and_nonmutating_cash_preview(demo_directory):
    data = Dataset.load(demo_directory)
    weights, _ = screened_allocation(
        data, "2024-01-02", ["PUB_EQ_A", "PUB_BD_A"], max_weight=0.6, min_periods=12
    )
    assert weights.sum() == pytest.approx(0.95)
    ledger = Ledger(data, 100_000)
    ledger.advance("2024-01-02")
    before = ledger.snapshot()
    orders, report = preview_rebalance(ledger, weights)
    assert len(orders) == 2 and (orders.status == "preview_accepted").all()
    assert report["after"]["cash"] == pytest.approx(5000)
    assert ledger.snapshot() == before and len(ledger.orders) == 0
    ledger.submit("PUB_EQ_A", "BUY", amount=90_000)
    blocked, _ = preview_rebalance(ledger, {"PUB_EQ_A": 0.5, "PUB_BD_A": 0.5})
    assert (blocked.status == "blocked").all()
    assert len(ledger.orders) == 1
