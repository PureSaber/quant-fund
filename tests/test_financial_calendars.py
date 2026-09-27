import pandas as pd
import pytest
from quant_data_kit.financial.calendars import CalendarBook, PurposeCalendar

from quant_fund.engine import Ledger


def attach(data, *, omit_bank_day=None, banking_end=None):
    records = []
    for purpose in ("dealing", "confirmation", "banking"):
        days = [str(x.date()) for x in data.calendar]
        if purpose == "banking" and omit_bank_day:
            days.remove(omit_bank_day)
        if purpose == "banking" and banking_end:
            days = [x for x in days if x <= banking_end]
        records.append(
            PurposeCalendar(
                purpose,
                purpose,
                "1",
                "2023-12-01T00:00:00Z",
                "2024-01-01",
                "2024-02-28",
                tuple(days),
                "synthetic",
                "synthetic",
            )
        )
    data.purpose_calendars = CalendarBook(records)
    data.metadata["calendar_ids"] = {x: x for x in ("dealing", "confirmation", "banking")}
    return data


def test_fund_banking_holiday_is_not_a_trading_holiday(make_dataset):
    data = attach(make_dataset(confirm_lag=0, settle_lag=1), omit_bank_day="2024-01-05")
    ledger = Ledger(data, 1000)
    ledger.advance("2024-01-02")
    ledger.submit("F", "BUY", amount=1000)
    ledger.advance("2024-01-03")
    order = ledger.submit("F", "SELL", shares=1000)
    assert order.deal_date == pd.Timestamp("2024-01-04")
    assert order.settle_date == pd.Timestamp("2024-01-08")
    ledger.advance("2024-01-04")
    ledger.advance("2024-01-05")
    assert ledger.cash == 0 and ledger.snapshot()["receivables"] == 1000
    ledger.advance("2024-01-08")
    assert ledger.cash == 1000


def test_calendar_failure_does_not_freeze_cash_or_shares(make_dataset):
    data = attach(make_dataset(confirm_lag=0, settle_lag=1), banking_end="2024-01-03")
    ledger = Ledger(data, 1000)
    ledger.advance("2024-01-02")
    with pytest.raises(ValueError, match="coverage"):
        ledger.submit("F", "BUY", amount=1000)
    assert ledger.cash == 1000 and ledger.frozen == 0 and not ledger.orders
