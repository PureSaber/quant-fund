import hashlib
import json
from dataclasses import replace

import pandas as pd
import pytest

from quant_fund.data import Dataset
from quant_fund.economics import FOFEconomics, FOFPolicy
from quant_fund.engine import BacktestConfig, Ledger, run_backtest
from quant_fund.report import export_run, verify_run


def test_interest_and_fees_independent_act365_calendar_gap(make_dataset):
    data = make_dataset()
    policy = {
        "known_at": "2024-01-02",
        "management_rate": 0.0365,
        "custody_rate": 0.0073,
        "cash_interest_rate": 0.073,
    }
    account = Ledger(data, 1000, fof_policy=policy)
    expected = 1000.0
    previous = None
    for date in data.calendar[:6]:
        # Same cash-only baseline: interest 20 cents/day, expenses 12 cents/day
        # initially, then compound only from previous net cash at observed closes.
        days = 0 if previous is None else (date - previous).days
        expected *= 1 + (0.073 - 0.0365 - 0.0073) * days / 365
        account.advance(date)
        assert account.cash == pytest.approx(expected)
        assert account.snapshot()["total_value"] == pytest.approx(expected)
        previous = date
    assert account.economics.records[-1]["elapsed_calendar_days"] == 1
    assert any(row["elapsed_calendar_days"] == 3 for row in account.economics.records)


def test_insufficient_cash_payable_reserves_and_redemption_payment(make_dataset):
    data = make_dataset(confirm_lag=0, settle_lag=2)
    account = Ledger(data, 1000, fof_policy={"known_at": "2024-01-02", "management_rate": 0.0365})
    account.advance(data.calendar[0])
    account.submit("F", "BUY", amount=1000)
    account.advance(data.calendar[1])
    assert account.cash == 0
    assert account.economics.fixed_payable == pytest.approx(0.1)
    assert account.snapshot()["total_value"] == pytest.approx(999.9)
    account.submit("F", "SELL", shares=1000)
    account.advance(data.calendar[2])
    assert account.snapshot()["receivables"] == 1000
    assert account.cash == 0
    with pytest.raises(ValueError, match="可用资金"):
        account.submit("F", "BUY", amount=1000)
    for date in data.calendar[3:5]:
        account.advance(date)
    assert account.economics.fixed_payable == 0
    assert account.cash < 1000
    assert account.economics.records[-1]["cash_paid"] > 0


def test_performance_loss_reversal_crystallization_and_hwm():
    policy = FOFPolicy(
        known_at="2024-01-01",
        performance_rate=0.2,
        crystallization_dates=("2024-01-04", "2024-01-06"),
    )
    economics = FOFEconomics(policy, 100)
    previous = None
    # Explicit gross account values, fixed investor capital. No investor flows.
    for token, gross in [("01", 100), ("02", 110), ("03", 95), ("04", 120)]:
        date = pd.Timestamp("2024-01-" + token)
        cash = economics.close(date, previous, gross, gross)
        row = economics.records[-1]
        previous = {
            "date": date,
            "total_value": row["net_assets"],
            "cash": cash,
            "fof_fee_liability": economics.liability,
        }
    assert economics.records[1]["performance_reserve"] == 2
    assert economics.records[2]["performance_reserve_change"] == -2
    assert economics.records[3]["performance_crystallized"] == 4
    assert cash == 116
    assert economics.high_water_mark == 116
    economics.close(pd.Timestamp("2024-01-05"), previous, 110, 110)
    assert economics.performance_reserve == 0
    previous.update(date=pd.Timestamp("2024-01-05"), total_value=110, cash=110)
    cash = economics.close(pd.Timestamp("2024-01-06"), previous, 121, 121)
    assert cash == 120  # Only 20% of the 5 above post-fee HWM 116.
    assert economics.high_water_mark == 120


def test_explicit_payments_do_not_change_net_assets():
    policy = FOFPolicy(
        known_at="2024-01-01",
        management_rate=0.0365,
        payment_frequency="explicit",
        payment_dates=("2024-01-03",),
    )
    economics = FOFEconomics(policy, 1000)
    previous = {
        "date": pd.Timestamp("2024-01-01"),
        "total_value": 1000,
        "cash": 1000,
        "fof_fee_liability": 0,
    }
    cash = economics.close(pd.Timestamp("2024-01-02"), previous, 1000, 1000)
    assert cash == 1000 and economics.liability == pytest.approx(0.1)
    previous.update(
        date=pd.Timestamp("2024-01-02"), total_value=999.9, fof_fee_liability=economics.liability
    )
    cash = economics.close(pd.Timestamp("2024-01-03"), previous, 1000, cash)
    assert economics.liability == 0
    assert cash == pytest.approx(999.80001)
    assert economics.records[-1]["net_assets"] == pytest.approx(cash)


def test_policy_rejections_and_transaction_rollback(make_dataset):
    for policy in (
        {"management_rate": 0.01},
        {"management_rate": float("nan")},
        {"day_count": "ACT/360"},
        {"cash_interest_rate": True},
    ):
        with pytest.raises(ValueError):
            FOFPolicy(**policy)
    data = make_dataset()
    account = Ledger(data, 1000, fof_policy={"known_at": "2024-01-03", "management_rate": 0.01})
    with pytest.raises(ValueError, match="unavailable"):
        account.advance(data.calendar[0])
    assert account.current_date is None and account.cash == 1000
    assert not account.history and not account.economics.records
    with pytest.raises(ValueError, match="calendar"):
        FOFPolicy(crystallization_dates=("2024-01-06",)).validate_calendar(data.calendar)


def test_zero_policy_identical_and_report_replay_tamper_detection(demo_directory, tmp_path):
    dataset = Dataset.load(demo_directory)
    config = BacktestConfig(strategy="equal", end="2024-05-31")
    original = run_backtest(dataset, config)
    zero = run_backtest(dataset, replace(config, fof_policy={"management_rate": 0}))
    pd.testing.assert_frame_equal(original["nav"], zero["nav"])
    result = run_backtest(
        dataset,
        replace(
            config,
            fof_policy={
                "known_at": "2024-01-02",
                "management_rate": 0.01,
                "custody_rate": 0.002,
                "cash_interest_rate": 0.015,
                "performance_rate": 0.2,
                "crystallization_dates": ["2024-05-31"],
            },
        ),
    )
    output = tmp_path / "run"
    export_run(result, dataset, demo_directory, output)
    assert verify_run(output)["ledger_replay"] == "pass"
    assert "FOF自身费用" in (output / "report.html").read_text(encoding="utf-8")
    assert json.loads((output / "platform-snapshot.json").read_text(encoding="utf-8"))[
        "fof_economics"
    ]
    table = pd.read_csv(output / "fof_economics.csv")
    table.loc[1, "cash_interest"] += 5
    table.to_csv(output / "fof_economics.csv", index=False)
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"]["fof_economics.csv"] = hashlib.sha256(
        (output / "fof_economics.csv").read_bytes()
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="fof_economics"):
        verify_run(output)


def test_unpaid_crystallized_fee_is_not_charged_twice():
    policy = FOFPolicy(
        known_at="2024-01-01", performance_rate=0.2, crystallization_dates=("2024-01-02",)
    )
    economics = FOFEconomics(policy, 100)
    date = pd.Timestamp("2024-01-02")
    cash = economics.close(date, None, 120, 0)
    assert cash == 0
    assert economics.fixed_payable == 4 and economics.high_water_mark == 116
    previous = {"date": date, "total_value": 116, "cash": 0, "fof_fee_liability": 4}
    economics.close(pd.Timestamp("2024-01-03"), previous, 120, 0)
    assert economics.performance_reserve == 0
    assert economics.liability == 4
    assert economics.records[-1]["net_assets"] == 116


def test_exhausted_assets_roll_back_accrual_and_date(make_dataset):
    calendar = pd.DatetimeIndex(["2024-01-02", "2026-01-02"])
    data = make_dataset(calendar=calendar)
    account = Ledger(data, 1000, fof_policy={"known_at": "2024-01-02", "management_rate": 0.9})
    account.advance(calendar[0])
    with pytest.raises(ValueError, match="exhaust"):
        account.advance(calendar[1])
    assert account.cash == 1000 and account.current_date == calendar[0]
    assert account.economics.liability == 0
    assert len(account.economics.records) == len(account.history) == 1
