import hashlib
import json
from dataclasses import asdict, replace

import pandas as pd
import pytest

from quant_fund.data import Dataset, Fund
from quant_fund.dealing import ExecutionPolicy, redemption, subscription
from quant_fund.engine import BacktestConfig, Ledger, run_backtest
from quant_fund.report import export_run, verify_run
from quant_fund.terms import TERM_FIELDS, FundTermsTable


def policy(**changes):
    return ExecutionPolicy(
        **{
            "subscription_tiers": [
                [0, "rate", "0.008"],
                [1000000, "rate", "0.005"],
                [5000000, "fixed", "1000"],
            ],
            "money_decimals": 2,
            "share_decimals": 2,
            "money_rounding": "half_up",
            "share_rounding": "half_up",
            "redemption_fee_rounding": "per_lot",
            "residual_destination": "fund",
            "source_ref": "synthetic:reviewed-fee-table",
            "source_sha256": "a" * 64,
            **changes,
        }
    )


def test_fee_thresholds_fixed_fee_and_independent_rounding_examples(make_dataset):
    fund = replace(make_dataset().funds["F"], execution_policy=policy())
    # Hand calculated: 1008 / 1.008 = 1000 net; 1000 / 1.2345 = 810.04455... shares.
    result = subscription(fund, 1008, 1.2345)
    assert result == {
        "gross": 1008.0,
        "fee": 8.0,
        "shares": 810.04,
        "rounding_residual": pytest.approx(0.00562),
    }
    assert subscription(fund, 1000000, 1)["fee"] == 4975.12
    assert subscription(fund, 5000000, 1)["fee"] == 1000
    assert subscription(fund, 5000000, 1)["shares"] == 4999000
    fund = replace(fund, sell_tiers=[[7, 0.015], [100000, 0]])
    sale = redemption(fund, 810.04, 1.2345, [(810.04, 3)])
    assert sale["gross"] == 999.99 and sale["fee"] == 15.0
    assert sale["rounding_residual"] == pytest.approx(0.00438)


def test_per_lot_and_aggregate_are_explicit_different_policies(make_dataset):
    fund = replace(make_dataset(sell_tiers=[[100000, 0.01]]).funds["F"], execution_policy=policy())
    assert redemption(fund, 1, 1, [(0.5, 1), (0.5, 2)])["fee"] == 0.02
    aggregate = replace(fund, execution_policy=policy(redemption_fee_rounding="aggregate"))
    assert redemption(aggregate, 1, 1, [(0.5, 1), (0.5, 2)])["fee"] == 0.01


@pytest.mark.parametrize(
    "changes",
    [
        {"money_decimals": True},
        {"share_decimals": 9},
        {"money_rounding": "guess"},
        {"subscription_tiers": [[1, "rate", 0]]},
        {"subscription_tiers": [[0, "rate", 1]]},
        {"subscription_tiers": [[0, "fixed", "NaN"]]},
        {"source_ref": ""},
        {"source_sha256": "missing"},
        {"residual_destination": "refund"},
        {"redemption_fee_rounding": "guess"},
    ],
)
def test_invalid_policy_is_rejected(changes):
    with pytest.raises(ValueError):
        policy(**changes)


def test_request_precision_failure_is_atomic_and_policy_is_frozen(make_dataset):
    dataset = make_dataset(confirm_lag=0, prices=[1.2345] * 20, execution_policy=policy())
    base = asdict(dataset.funds["F"])
    versions = []
    for identity, at, configured in [
        ("v1", "2023-01-01", policy()),
        ("v2", "2024-01-03", policy(subscription_tiers=[[0, "rate", 0]])),
    ]:
        values = {**base, "execution_policy": asdict(configured)}
        versions.append(
            {
                "fund_id": "F",
                "terms_id": "all",
                "version_id": identity,
                "effective_from": "2023-01-01",
                "effective_to": None,
                "known_at": at,
                **{k: values[k] for k in TERM_FIELDS},
            }
        )
    dataset.term_versions = FundTermsTable(dataset.funds, versions)
    ledger = Ledger(dataset, 2000)
    ledger.advance(dataset.calendar[0])
    with pytest.raises(ValueError, match="precision"):
        ledger.submit("F", "BUY", amount=1008.001)
    assert ledger.cash == 2000 and ledger.frozen == 0 and not ledger.orders
    ledger.submit("F", "BUY", amount=1008)
    ledger.advance(dataset.calendar[1])
    assert ledger.trades[0]["fee"] == 8
    assert ledger.trades[0]["terms_version_id"] == "v1"
    assert ledger.lots[0].shares == 810.04
    with pytest.raises(ValueError, match="precision"):
        ledger.submit("F", "SELL", shares=1.001)
    assert ledger.lots[0].reserved == 0


def test_policy_roundtrips_and_legacy_defaults_remain_valid(make_dataset):
    fund = replace(make_dataset().funds["F"], execution_policy=policy())
    assert Fund(**json.loads(json.dumps(asdict(fund)))) == fund
    with pytest.raises(ValueError, match="buy_fee"):
        replace(fund, buy_fee=0.01)
    legacy = make_dataset(buy_fee=0.008).funds["F"]
    assert subscription(legacy, 1008, 1)["shares"] == 1000


def test_rounded_backtest_exports_and_replays(demo_directory, tmp_path):
    import shutil

    source = tmp_path / "source"
    shutil.copytree(demo_directory, source)
    path = source / "funds.json"
    funds = json.loads(path.read_text(encoding="utf-8"))
    for fund in funds:
        fund.update(buy_fee=0, execution_policy=asdict(policy()))
    path.write_text(json.dumps(funds), encoding="utf-8")
    dataset = Dataset.load(source)
    result = run_backtest(
        dataset, BacktestConfig(start="2024-01-02", end="2024-05-31", strategy="equal")
    )
    assert not result["trades"].empty and "rounding_residual" in result["trades"]
    assert set(result["trades"].side) == {"BUY", "SELL"}
    output = export_run(result, dataset, source, tmp_path / "report")
    assert verify_run(output)["verified_ledger_orders"] > 0
    trades = pd.read_csv(output / "trades.csv")
    trades.loc[0, "rounding_residual"] += 1
    trades.to_csv(output / "trades.csv", index=False)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"]["trades.csv"] = hashlib.sha256(
        (output / "trades.csv").read_bytes()
    ).hexdigest()
    (output / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="尾差"):
        verify_run(output)
