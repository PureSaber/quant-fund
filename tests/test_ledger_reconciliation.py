import hashlib
import json
from copy import deepcopy

import pandas as pd
import pytest

from quant_fund.data import Dataset
from quant_fund.engine import BacktestConfig, Ledger, run_backtest
from quant_fund.report import export_run, verify_run


def test_request_retry_does_not_reserve_twice_even_after_confirmation(make_dataset):
    ledger = Ledger(make_dataset(confirm_lag=0), 2000)
    ledger.advance(ledger.dataset.calendar[0])
    first = ledger.submit("F", "BUY", amount=1000, request_id="buy-1")
    assert ledger.submit("F", "BUY", amount=1000, request_id="buy-1") is first
    assert ledger.cash == ledger.frozen == 1000
    ledger.advance(ledger.dataset.calendar[1])
    assert ledger.submit("F", "BUY", amount=1000, request_id="buy-1") is first
    assert len(ledger.orders) == len(ledger.trades) == 1
    with pytest.raises(ValueError, match="同一请求"):
        ledger.submit("F", "BUY", amount=500, request_id="buy-1")
    sale = ledger.submit("F", "SELL", shares=500, request_id="sell-1")
    assert ledger.submit("F", "SELL", shares=500, request_id="sell-1") is sale
    assert ledger.lots[0].reserved == 500


@pytest.mark.parametrize("request_id", ["", " ", 1, True])
def test_invalid_request_identity_does_not_change_account(make_dataset, request_id):
    ledger = Ledger(make_dataset(), 2000)
    ledger.advance(ledger.dataset.calendar[0])
    with pytest.raises(ValueError, match="请求标识"):
        ledger.submit("F", "BUY", amount=1000, request_id=request_id)
    assert ledger.cash == 2000 and ledger.frozen == 0 and not ledger.orders


def test_reserved_shares_and_receivables_are_reconciled(make_dataset):
    ledger = Ledger(make_dataset(confirm_lag=0), 2000)
    ledger.advance(ledger.dataset.calendar[0])
    ledger.submit("F", "BUY", amount=1000)
    ledger.advance(ledger.dataset.calendar[1])
    ledger.submit("F", "SELL", shares=500)
    ledger.lots[0].reserved -= 1
    with pytest.raises(ValueError, match="冻结份额"):
        ledger.check()
    ledger.lots[0].reserved += 1
    ledger.receivables.append({"amount": float("nan"), "due": None})
    with pytest.raises(ValueError, match="应收资金"):
        ledger.check()


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_submission_failure_restores_balances_and_allows_same_request_retry(
    make_dataset, monkeypatch, side
):
    ledger = Ledger(make_dataset(confirm_lag=0), 2000)
    ledger.advance(ledger.dataset.calendar[0])
    ledger.submit("F", "BUY", amount=1000)
    ledger.advance(ledger.dataset.calendar[1])
    before = deepcopy(ledger)

    def interrupted():
        raise KeyboardInterrupt("injected submission interruption")

    parameters = {"amount": 500} if side == "BUY" else {"shares": 500}
    with monkeypatch.context() as patch:
        patch.setattr(ledger, "check", interrupted)
        with pytest.raises(KeyboardInterrupt):
            ledger.submit("F", side, request_id="retry", **parameters)
    assert ledger.cash == before.cash and ledger.frozen == before.frozen
    pd.testing.assert_frame_equal(ledger.lots_frame(), before.lots_frame())
    pd.testing.assert_frame_equal(ledger.orders_frame(), before.orders_frame())
    ledger.submit("F", side, request_id="retry", **parameters)
    assert len(ledger.orders) == 2
    ledger.check()


@pytest.mark.parametrize("corruption", ["cash", "lot", "nan", "missing_day", "duplicate_day"])
def test_resigned_economic_corruption_is_rejected_by_replay(demo_directory, tmp_path, corruption):
    dataset = Dataset.load(demo_directory)
    result = run_backtest(
        dataset, BacktestConfig(start="2024-01-02", end="2024-05-31", strategy="equal")
    )
    root = export_run(result, dataset, demo_directory, tmp_path / "report")
    report = verify_run(root)
    assert report["verified_ledger_days"] == len(result["nav"])
    assert report["verified_ledger_orders"] == len(result["orders"])
    path = root / ("lots.csv" if corruption == "lot" else "nav.csv")
    table = pd.read_csv(path)
    if corruption == "lot":
        table.loc[0, "shares"] += 1
    elif corruption == "cash":
        table.loc[len(table) - 1, "cash"] += 100
        table.loc[len(table) - 1, "total_value"] += 100
        table.loc[len(table) - 1, "nav"] = (
            table.loc[len(table) - 1, "total_value"] / result["config"]["initial_cash"]
        )
    elif corruption == "nan":
        table.loc[0, "cash"] = float("nan")
    elif corruption == "missing_day":
        table = table.drop(index=1)
    else:
        table = pd.concat([table.iloc[:1], table], ignore_index=True)
    table.to_csv(path, index=False)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="账本重放"):
        verify_run(root)
