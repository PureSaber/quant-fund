import pandas as pd
import pytest

from quant_fund.data import Dataset, Fund, validate_nav
from quant_fund.demo import create_demo


@pytest.fixture
def make_dataset():
    def make(*, prices=None, known_lag=0, calendar=None, distributions=None, **fund_changes):
        calendar = calendar if calendar is not None else pd.bdate_range("2024-01-02", periods=20)
        prices = prices if prices is not None else [1.0] * len(calendar)
        fund = Fund(
            **{
                "fund_id": "F",
                "name": "测试基金",
                "manager": "测试管理人",
                "strategy": "equity",
                "kind": "public",
                "inception": "2023-01-01",
                "known_at": "2023-01-01",
                **fund_changes,
            }
        )
        rows = [
            {
                "fund_id": "F",
                "nav_date": d,
                "known_at": calendar[i + known_lag],
                "unit_nav": prices[i],
                "total_return_nav": prices[i],
                "source": "test",
            }
            for i, d in enumerate(calendar)
            if i + known_lag < len(calendar)
        ]
        events = pd.DataFrame(
            distributions or [],
            columns=["fund_id", "ex_date", "pay_date", "known_at", "cash_per_share"],
        )
        for col in ("ex_date", "pay_date", "known_at"):
            events[col] = pd.to_datetime(events[col])
        events["cash_per_share"] = pd.to_numeric(events.cash_per_share)
        return Dataset(
            {"F": fund},
            validate_nav(pd.DataFrame(rows)),
            calendar,
            events,
            {"classification": "synthetic"},
            "test",
        )

    return make


@pytest.fixture(scope="session")
def demo_directory(tmp_path_factory):
    return create_demo(tmp_path_factory.mktemp("demo") / "dataset")
