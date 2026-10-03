import json

import numpy as np
import pandas as pd
import pytest

from quant_fund.data import Dataset
from quant_fund.engine import BacktestConfig, run_backtest
from quant_fund.monitor import monitor
from quant_fund.report import export_run, verify_run
from quant_fund.research import allocate, performance, risk_contributions, style_regression


@pytest.mark.parametrize("strategy", ["equal", "balanced", "optimal_risk", "skfolio_risk", "hrp"])
def test_actual_optimizer_adapters(demo_directory, strategy):
    data = Dataset.load(demo_directory)
    weights, info = allocate(data, "2026-08-31", strategy)
    assert weights.sum() == pytest.approx(0.95)
    assert (weights >= 0).all() and (weights <= 0.400001).all()
    assert info["periods"] >= 12
    rc = risk_contributions(data.returns("2026-08-31").dropna(), weights)
    assert rc.risk_share.sum() == pytest.approx(1)


@pytest.mark.parametrize("max_weight", [0.6, 1.0])
def test_hrp_two_funds_matches_inverse_variance_split_and_budget(demo_directory, max_weight):
    data = Dataset.load(demo_directory)
    codes = list(data.funds)[:2]
    data.funds = {code: data.funds[code] for code in codes}
    weights, info = allocate(data, "2026-08-31", "hrp", max_weight=max_weight)
    panel = data.returns("2026-08-31")[codes].dropna()
    inverse_variance = 1 / panel.var()
    expected = inverse_variance / inverse_variance.sum() * 0.95
    if expected.max() > max_weight:
        high = expected.idxmax()
        expected.loc[high] = max_weight
        expected.loc[expected.index != high] = 0.95 - max_weight
    pd.testing.assert_series_equal(weights, expected.reindex(weights.index), check_names=False)
    assert info["funds"] == sorted(codes)
    assert weights.sum() == pytest.approx(0.95)
    assert weights.max() <= max_weight + 1e-6


def test_infeasible_constraints_are_explicit(demo_directory):
    data = Dataset.load(demo_directory)
    with pytest.raises(ValueError, match="不能满足"):
        allocate(data, "2026-08-31", "optimal_risk", max_weight=0.01)


def test_drawdown_includes_initial_capital():
    stats = performance(pd.Series([-0.1, 0.05]))
    assert stats["max_drawdown"] == pytest.approx(-0.1)
    assert stats["total_return"] == pytest.approx(-0.055)


def test_regression_recovers_known_exposures():
    rng = np.random.default_rng(7)
    x = pd.DataFrame(rng.normal(size=(30, 2)), columns=["equity", "bond"])
    y = 0.002 + 0.7 * x.equity - 0.2 * x.bond
    stats = style_regression(y, x, window=24)
    assert stats["exposures"]["equity"] == pytest.approx(0.7)
    assert stats["exposures"]["bond"] == pytest.approx(-0.2)
    assert stats["r_squared"] == pytest.approx(1)


def test_end_to_end_report_reconciles_and_detects_tampering(demo_directory, tmp_path, monkeypatch):
    from quant_lab.contracts_v2 import load_and_validate_run_v2

    monkeypatch.setattr("quant_fund.report.clean_git_commit", lambda root: "a" * 40)
    data = Dataset.load(demo_directory)
    result = run_backtest(
        data, BacktestConfig(start="2024-01-02", end="2024-05-31", strategy="equal")
    )
    assert len(result["trades"]) > 0
    nav = result["nav"]
    np.testing.assert_allclose(
        nav.total_value, nav.cash + nav.frozen_cash + nav.receivables + nav.holdings_value
    )
    assert (nav.cash >= -1e-8).all()
    state = monitor(result, data)
    assert not state["holdings"].empty
    assert not state["liquidity"].empty
    assert not state["rebalance_review"].empty
    before = result["ledger"].orders_frame()
    monitor(result, data)
    pd.testing.assert_frame_equal(before, result["ledger"].orders_frame())
    root = export_run(result, data, demo_directory, tmp_path / "report")
    published = load_and_validate_run_v2(root)
    assert published.tags["rankable"] == "false"
    assert published.dataset_snapshots["dataset"] == data.fingerprint
    exported_nav = pd.read_parquet(root / "standard/v2/returns.parquet")
    assert exported_nav.nav_units.iloc[0] / 10000 == pytest.approx(nav.total_value.iloc[0])
    assert verify_run(root)["max_reconciliation_error"] < 1e-6
    snapshot = json.loads((root / "platform-snapshot.json").read_text(encoding="utf-8"))
    assert snapshot["schema"] == "quant-fund.portfolio-snapshot@1"
    assert snapshot["mode"] == "research_only"
    assert snapshot["holdings"] and snapshot["rebalance_review"]
    (root / "trades.csv").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="校验失败"):
        verify_run(root)


def test_empty_portfolio_still_reports_confirmed_receivables(make_dataset):
    from quant_fund.engine import Ledger

    data = make_dataset()
    ledger = Ledger(data)
    ledger.advance(data.calendar[0])
    ledger.receivables.append(
        {"fund_id": "F", "amount": 100, "due": data.calendar[2], "kind": "redemption"}
    )
    result = {
        "ledger": ledger,
        "targets": pd.DataFrame(),
        "config": {"max_weight": 0.4, "frequency": "ME", "lookback": 24},
    }
    state = monitor(result, data)
    assert state["liquidity"].estimated_amount.sum() == 100
    assert state["holdings"].empty
