import pandas as pd
import pytest

from quant_fund.engine import Ledger


def test_buy_reserves_cash_uses_dealing_nav_and_external_fee(make_dataset):
    data = make_dataset(prices=[1, 2, 4] + [4] * 17, buy_fee=0.01)
    ledger = Ledger(data, 1010)
    ledger.advance(data.calendar[0])
    order = ledger.submit("F", "BUY", amount=1010)
    assert ledger.cash == 0
    assert ledger.frozen == 1010
    ledger.advance(data.calendar[1])
    assert not ledger.lots
    ledger.advance(data.calendar[2])
    assert order.status == "confirmed"
    assert ledger.lots[0].shares == pytest.approx(500)
    assert ledger.trades[0]["fee"] == pytest.approx(10)
    assert ledger.trades[0]["unit_nav"] == 2
    assert ledger.snapshot()["total_value"] == pytest.approx(2000)


def test_unpublished_exact_nav_keeps_subscription_pending(make_dataset):
    data = make_dataset(known_lag=4, confirm_lag=1)
    ledger = Ledger(data, 1000)
    ledger.advance(data.calendar[4])
    order = ledger.submit("F", "BUY", amount=1000)
    for date in data.calendar[5:9]:
        ledger.advance(date)
    assert order.status == "pending"
    assert ledger.frozen == 1000
    ledger.advance(data.calendar[9])
    assert order.status == "confirmed"


def test_redemption_cash_cannot_be_reused_before_arrival(make_dataset):
    data = make_dataset(confirm_lag=0, settle_lag=3)
    ledger = Ledger(data, 1000)
    ledger.advance(data.calendar[0])
    ledger.submit("F", "BUY", amount=1000)
    ledger.advance(data.calendar[1])
    ledger.submit("F", "SELL", shares=1000)
    ledger.advance(data.calendar[2])
    assert ledger.snapshot()["receivables"] == pytest.approx(1000)
    assert ledger.cash == 0
    with pytest.raises(ValueError, match="可用资金"):
        ledger.submit("F", "BUY", amount=1000)
    for date in data.calendar[3:6]:
        ledger.advance(date)
    assert ledger.cash == pytest.approx(1000)
    assert not ledger.receivables


def test_lock_period_and_double_sell_reservation(make_dataset):
    data = make_dataset(confirm_lag=0, lock_days=7)
    ledger = Ledger(data, 1000)
    ledger.advance(data.calendar[0])
    ledger.submit("F", "BUY", amount=1000)
    ledger.advance(data.calendar[1])
    with pytest.raises(ValueError, match="已解锁"):
        ledger.submit("F", "SELL", shares=10)
    for date in data.calendar[2:7]:
        ledger.advance(date)
    ledger.submit("F", "SELL", shares=700)
    with pytest.raises(ValueError, match="份额不足"):
        ledger.submit("F", "SELL", shares=400)


def test_calendar_end_does_not_accelerate_confirmation(make_dataset):
    data = make_dataset(
        calendar=pd.bdate_range("2024-01-02", periods=3), confirm_lag=4, settle_lag=5
    )
    ledger = Ledger(data)
    ledger.advance(data.calendar[0])
    order = ledger.submit("F", "BUY", amount=1000)
    for date in data.calendar[1:]:
        ledger.advance(date)
    assert order.confirm_date is None and order.status == "pending"
    assert ledger.frozen == 1000


def test_private_notice_period_and_explicit_open_day(make_dataset):
    data = make_dataset(kind="private", open_dates=["2024-01-05", "2024-01-19"], notice_days=7)
    ledger = Ledger(data)
    ledger.advance(data.calendar[0])
    assert ledger.submit("F", "BUY", amount=1000).deal_date == pd.Timestamp("2024-01-19")


def test_dividend_does_not_double_count_stale_cum_dividend_nav(make_dataset):
    events = [
        {
            "fund_id": "F",
            "ex_date": "2024-01-05",
            "pay_date": "2024-01-08",
            "known_at": "2024-01-02",
            "cash_per_share": 0.1,
        }
    ]
    data = make_dataset(
        prices=[1, 1, 1] + [0.9] * 17, known_lag=1, confirm_lag=0, distributions=events
    )
    ledger = Ledger(data, 1000)
    ledger.advance(data.calendar[1])
    ledger.submit("F", "BUY", amount=1000)
    ledger.advance(data.calendar[2])
    ledger.advance(data.calendar[3])
    assert ledger.snapshot()["holdings_value"] == pytest.approx(900)
    assert ledger.snapshot()["receivables"] == pytest.approx(100)
    assert ledger.snapshot()["total_value"] == pytest.approx(1000)
    ledger.advance(data.calendar[4])
    assert ledger.cash == pytest.approx(100)
    assert ledger.snapshot()["total_value"] == pytest.approx(1000)


def test_dividend_with_unresolved_record_entitlement_is_rejected(make_dataset):
    events = [
        {
            "fund_id": "F",
            "ex_date": "2024-01-05",
            "pay_date": "2024-01-08",
            "known_at": "2024-01-02",
            "cash_per_share": 0.1,
        }
    ]
    data = make_dataset(confirm_lag=4, settle_lag=5, distributions=events)
    ledger = Ledger(data)
    ledger.advance(data.calendar[0])
    ledger.submit("F", "BUY", amount=1000)
    for date in data.calendar[1:3]:
        ledger.advance(date)
    with pytest.raises(ValueError, match="登记日权益"):
        ledger.advance(data.calendar[3])


def test_fifo_redemption_tiers(make_dataset):
    data = make_dataset(confirm_lag=0, sell_tiers=[[7, 0.015], [100000, 0]])
    ledger = Ledger(data, 2000)
    ledger.advance(data.calendar[0])
    ledger.submit("F", "BUY", amount=1000)
    for date in data.calendar[1:8]:
        ledger.advance(date)
    ledger.submit("F", "BUY", amount=1000)
    ledger.advance(data.calendar[8])
    ledger.submit("F", "SELL", shares=1500)
    ledger.advance(data.calendar[9])
    assert ledger.trades[-1]["fee"] == pytest.approx(500 * 0.015)


def test_same_day_ex_dividend_redemption_keeps_entitlement(make_dataset):
    events = [
        {
            "fund_id": "F",
            "ex_date": "2024-01-05",
            "pay_date": "2024-01-08",
            "known_at": "2024-01-02",
            "cash_per_share": 0.1,
        }
    ]
    data = make_dataset(
        prices=[1, 1, 1] + [0.9] * 17, confirm_lag=0, settle_lag=0, distributions=events
    )
    ledger = Ledger(data, 1000)
    ledger.advance(data.calendar[0])
    ledger.submit("F", "BUY", amount=1000)
    ledger.advance(data.calendar[1])
    ledger.advance(data.calendar[2])
    ledger.submit("F", "SELL", shares=1000)
    ledger.advance(data.calendar[3])
    assert ledger.cash == pytest.approx(900)
    assert ledger.snapshot()["receivables"] == pytest.approx(100)
    ledger.advance(data.calendar[4])
    assert ledger.cash == pytest.approx(1000)


@pytest.mark.parametrize("change", [{"currency": "USD"}, {"kind": "etf"}])
def test_unsupported_trade_models_are_rejected(make_dataset, change):
    with pytest.raises(ValueError):
        Ledger(make_dataset(**change))
