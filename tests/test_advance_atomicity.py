from copy import deepcopy
from dataclasses import asdict

import pytest

from quant_fund.engine import Ledger


def state(ledger):
    return deepcopy(
        {
            "date": ledger.current_date,
            "cash": ledger.cash,
            "frozen": ledger.frozen,
            "receivables": ledger.receivables,
            "lots": [asdict(lot) for lot in ledger.lots],
            "orders": [asdict(order) for order in ledger.orders],
            "trades": ledger.trades,
            "history": ledger.history,
            "dividends": ledger.dividends_processed,
        }
    )


def dividend():
    return {
        "fund_id": "F",
        "ex_date": "2024-01-05",
        "pay_date": "2024-01-08",
        "known_at": "2024-01-02",
        "cash_per_share": 0.1,
    }


def test_unresolved_dividend_does_not_consume_the_failed_day(make_dataset):
    data = make_dataset(confirm_lag=4, settle_lag=5, distributions=[dividend()])
    ledger = Ledger(data, 1000)
    ledger.advance(data.calendar[0])
    ledger.submit("F", "BUY", amount=1000)
    for date in data.calendar[1:3]:
        ledger.advance(date)
    before = state(ledger)

    for _ in range(2):
        with pytest.raises(ValueError, match="登记日权益"):
            ledger.advance(data.calendar[3])
        assert state(ledger) == before
    with pytest.raises(ValueError, match="不能跳过事件"):
        ledger.advance(data.calendar[4])
    assert state(ledger) == before


@pytest.mark.parametrize(
    "method,fail_on_call",
    [("_settle", 1), ("_settle", 2), ("_dividends", 1), ("check", 1), ("snapshot", 1)],
)
def test_daily_mutations_rollback_and_retry_exactly_once(
    make_dataset, monkeypatch, method, fail_on_call
):
    data = make_dataset(
        confirm_lag=0,
        settle_lag=1,
        prices=[1, 1, 1] + [0.9] * 17,
        distributions=[dividend()],
    )
    ledger = Ledger(data, 2000)
    ledger.advance(data.calendar[0])
    ledger.submit("F", "BUY", amount=1000)
    ledger.advance(data.calendar[1])
    ledger.submit("F", "SELL", shares=500)
    ledger.advance(data.calendar[2])
    buy = ledger.submit("F", "BUY", amount=500)
    sell = ledger.submit("F", "SELL", shares=250)
    lot = ledger.lots[0]
    before = state(ledger)
    expected = deepcopy(ledger)
    expected.advance(data.calendar[3])
    original = getattr(ledger, method)
    calls = 0

    def fail_after_mutation(*args, **kwargs):
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if calls == fail_on_call:
            raise RuntimeError("injected daily failure")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(ledger, method, fail_after_mutation)
        with pytest.raises(RuntimeError, match="injected daily failure"):
            ledger.advance(data.calendar[3])

    assert state(ledger) == before
    assert ledger.dataset is data
    assert ledger.orders[-2] is buy and ledger.orders[-1] is sell
    assert ledger.lots[0] is lot and sell.lots[0][0] is lot
    assert buy.status == sell.status == "pending"
    assert lot.shares == 500 and lot.reserved == 250

    ledger.advance(data.calendar[3])
    assert state(ledger) == state(expected)
    assert buy.status == sell.status == "confirmed"
    assert ledger.snapshot()["total_value"] == pytest.approx(2000)
    ledger.advance(data.calendar[4])
    expected.advance(data.calendar[4])
    assert state(ledger) == state(expected)


def test_initial_day_failure_restores_unstarted_ledger(make_dataset, monkeypatch):
    ledger = Ledger(make_dataset(), 1000)
    before = state(ledger)
    with monkeypatch.context() as patch:

        def interrupt():
            raise KeyboardInterrupt("injected interruption")

        patch.setattr(ledger, "snapshot", interrupt)
        with pytest.raises(KeyboardInterrupt):
            ledger.advance(ledger.dataset.calendar[0])
    assert state(ledger) == before
    ledger.advance(ledger.dataset.calendar[0])
    assert len(ledger.history) == 1
