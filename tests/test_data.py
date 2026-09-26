import sqlite3
from dataclasses import replace

import pandas as pd
import pytest

from quant_fund.data import Dataset, import_observations, validate_nav
from quant_fund.research import allocate, latest_complete_interval


def test_revision_only_visible_when_known(make_dataset):
    data = make_dataset()
    revision = data.nav.iloc[[0]].copy()
    revision["known_at"] = data.calendar[5]
    revision["unit_nav"] = 2.0
    data.nav = pd.concat([data.nav, revision], ignore_index=True)
    assert data.quote("F", data.calendar[4], exact_date=data.calendar[0]).unit_nav == 1
    assert data.quote("F", data.calendar[5], exact_date=data.calendar[0]).unit_nav == 2


@pytest.mark.parametrize(
    "field,value", [("unit_nav", 0), ("total_return_nav", float("inf")), ("known_at", "2020-01-01")]
)
def test_reject_invalid_observations(make_dataset, field, value):
    frame = make_dataset().nav.copy()
    frame.loc[0, field] = value
    with pytest.raises(ValueError):
        validate_nav(frame)


def test_csv_xlsx_import_idempotent_and_transactional(make_dataset, tmp_path):
    frame = make_dataset().nav
    csv, xlsx, database = tmp_path / "nav.csv", tmp_path / "nav.xlsx", tmp_path / "nav.sqlite"
    frame.to_csv(csv, index=False)
    frame.to_excel(xlsx, index=False)
    assert import_observations(csv, database)["inserted"] == len(frame)
    assert import_observations(xlsx, database)["inserted"] == 0
    newer = frame.iloc[[0]].copy()
    newer["fund_id"] = "A_NEW"
    conflict = frame.iloc[[0]].copy()
    conflict["unit_nav"] = 20
    pd.concat([newer, conflict]).to_csv(csv, index=False)
    with pytest.raises(ValueError, match="冲突"):
        import_observations(csv, database)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM nav").fetchone()[0] == len(frame)


def test_future_data_does_not_change_allocation(demo_directory):
    data = Dataset.load(demo_directory)
    altered = data.nav.copy()
    future = altered.known_at > pd.Timestamp("2024-07-01")
    altered.loc[future, ["unit_nav", "total_return_nav"]] *= 10
    changed = replace(data, nav=altered)
    for strategy in ("equal", "optimal_risk", "hrp"):
        left, info = allocate(data, "2024-07-01", strategy)
        right, other = allocate(changed, "2024-07-01", strategy)
        pd.testing.assert_series_equal(left, right)
        assert info == other


def test_low_frequency_missing_month_not_interpolated(make_dataset):
    data = make_dataset(calendar=pd.bdate_range("2024-01-01", "2024-06-28"), max_stale_days=60)
    data.nav = data.nav.loc[data.nav.nav_date.dt.month != 3]
    panel = data.returns("2024-06-28")
    assert pd.isna(panel.loc["2024-03-31", "F"])
    assert pd.isna(panel.loc["2024-04-30", "F"])


def test_undisclosed_month_end_not_treated_as_complete_month(make_dataset):
    data = make_dataset(calendar=pd.bdate_range("2024-01-01", "2024-04-05"), known_lag=1)
    panel = data.returns("2024-02-29")
    assert pd.isna(panel.loc["2024-02-29", "F"])
    assert data.returns("2024-03-01").loc["2024-02-29", "F"] == 0


def test_wealth_metrics_do_not_join_across_missing_months():
    values = pd.Series([0.1, None, 0.02, -0.01, None])
    assert latest_complete_interval(values).index.tolist() == [2, 3]
