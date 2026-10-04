import hashlib
import json
from copy import deepcopy
from dataclasses import asdict

import pandas as pd
import pytest

from quant_fund.data import Dataset, FundIdentity
from quant_fund.engine import BacktestConfig, Ledger, Lot, run_backtest, validate_backtest_inputs
from quant_fund.monitor import monitor
from quant_fund.report import export_run, verify_run
from quant_fund.research import allocate, research_table
from quant_fund.terms import TERM_FIELDS, FundTermsTable


def version(data, terms_id, version_id, effective_from, effective_to, known_at, **changes):
    values = asdict(data.funds["F"])
    values.update(changes)
    return {
        "fund_id": "F",
        "terms_id": terms_id,
        "version_id": version_id,
        "effective_from": effective_from,
        "effective_to": effective_to,
        "known_at": known_at,
        **{name: values[name] for name in TERM_FIELDS},
    }


def attach(data, versions):
    data.term_versions = FundTermsTable(data.funds, versions)
    return data


def test_latest_known_revision_is_selected_before_effective_interval(make_dataset):
    data = make_dataset()
    records = [
        version(data, "old", "z-old", "2023-01-01", "2024-01-10", "2023-01-01"),
        version(data, "new", "z-new", "2024-01-10", None, "2023-01-01"),
        version(
            data,
            "old",
            "a-revision",
            "2023-01-01",
            "2024-01-05",
            "2024-01-08",
            manager="修订后管理人",
        ),
    ]
    attach(data, records)

    assert data.fund_at("F", "2024-01-06", "2024-01-07").version_id == "z-old"
    with pytest.raises(ValueError, match="缺失"):
        data.fund_at("F", "2024-01-06", "2024-01-08")
    assert data.fund_at("F", "2024-01-04", "2024-01-08").version_id == "a-revision"


def test_conflicting_and_ambiguous_versions_fail(make_dataset):
    data = make_dataset()
    duplicate = version(data, "one", "v1", "2023-01-01", None, "2023-01-01")
    conflict = {**duplicate, "version_id": "v2", "manager": "另一个管理人"}
    with pytest.raises(ValueError, match="同一terms_id"):
        FundTermsTable(data.funds, [duplicate, conflict])

    overlap = version(data, "two", "v3", "2024-01-01", None, "2023-01-01")
    with pytest.raises(ValueError, match="重叠"):
        FundTermsTable(data.funds, [duplicate, overlap])


def test_future_unsupported_revision_does_not_block_earlier_backtest(make_dataset):
    data = make_dataset()
    records = [
        version(data, "all", "current", "2023-01-01", None, "2023-01-01"),
        version(
            data,
            "all",
            "future-poison",
            "2023-01-01",
            None,
            "2025-01-01",
            currency="USD",
            kind="etf",
        ),
    ]
    attach(data, records)
    dates = validate_backtest_inputs(data, BacktestConfig(start="2024-01-02", end="2024-01-10"))
    assert len(dates) == 7
    assert data.fund_at("F", "2024-01-05", "2024-01-05").version_id == "current"


def test_identity_known_before_inception_does_not_enter_research_or_buy_early(make_dataset):
    calendar = pd.bdate_range("2024-01-15", "2024-02-05")
    data = make_dataset(calendar=calendar)
    record = version(data, "initial", "v1", "2024-02-01", None, "2024-01-01")
    data.funds = {
        "F": FundIdentity(
            fund_id="F",
            name="测试基金",
            inception="2024-02-01",
            known_at="2024-01-01",
        )
    }
    attach(data, [record])

    assert data.returns("2024-01-31").empty
    table, panel = research_table(data, "2024-01-31")
    assert table.empty and panel.empty
    ledger = Ledger(data, 1000)
    ledger.advance(calendar[0])
    with pytest.raises(ValueError, match="不在可申购范围"):
        ledger.submit("F", "BUY", amount=1000)
    for date in calendar[1 : calendar.get_loc("2024-02-01") + 1]:
        ledger.advance(date)
    order = ledger.submit("F", "BUY", amount=1000)
    assert order.deal_date == pd.Timestamp("2024-02-02")


def economic_versions(data):
    common = {
        "kind": "private",
        "manager": "管理人甲",
        "strategy": "equity",
        "max_stale_days": 30,
        "min_buy": 100,
    }
    return [
        version(
            data,
            "old",
            "old-v1",
            "2023-01-01",
            "2024-01-05",
            "2023-01-01",
            open_dates=["2023-12-29"],
            buy_fee=0,
            sell_tiers=[[100000, 0]],
            lock_days=0,
            confirm_lag=0,
            settle_lag=0,
            **common,
        ),
        version(
            data,
            "new",
            "new-v1",
            "2024-01-05",
            None,
            "2023-12-20",
            open_dates=["2024-01-08", "2024-01-19"],
            buy_fee=0.2,
            sell_tiers=[[100000, 0.01]],
            lock_days=10,
            confirm_lag=2,
            settle_lag=4,
            **common,
        ),
        version(
            data,
            "new",
            "new-v2",
            "2024-01-05",
            None,
            "2024-01-09",
            open_dates=["2024-01-08", "2024-01-19"],
            buy_fee=0.5,
            sell_tiers=[[100000, 0.05]],
            lock_days=20,
            confirm_lag=1,
            settle_lag=3,
            **common,
        ),
        version(
            data,
            "new",
            "new-v3",
            "2024-01-05",
            None,
            "2024-01-22",
            open_dates=["2024-01-08", "2024-01-19"],
            buy_fee=0.5,
            sell_tiers=[[100000, 0.2]],
            lock_days=20,
            confirm_lag=1,
            settle_lag=3,
            **common,
        ),
    ]


def test_orders_freeze_application_terms_and_lots_freeze_purchase_lock(make_dataset):
    calendar = pd.bdate_range("2024-01-02", periods=30)
    data = make_dataset(calendar=calendar)
    records = economic_versions(data)
    attach(data, records)
    ledger = Ledger(data, 1200)
    ledger.advance("2024-01-02")
    buy = ledger.submit("F", "BUY", amount=1200)
    assert buy.deal_date == pd.Timestamp("2024-01-08")
    assert buy.confirm_date == pd.Timestamp("2024-01-10")
    assert buy.terms_version_id == "new-v1"

    # Mutating the caller-owned source after submission cannot alter the frozen order.
    records[1]["buy_fee"] = 0.9
    records[1]["sell_tiers"][0][1] = 0.9
    for date in calendar[1 : calendar.get_loc("2024-01-17") + 1]:
        ledger.advance(date)
    assert ledger.trades[0]["fee"] == pytest.approx(200)
    assert ledger.trades[0]["shares"] == pytest.approx(1000)
    lot = ledger.lots[0]
    assert (lot.lock_days, lot.terms_version_id) == (10, "new-v1")

    state = monitor(
        {
            "ledger": ledger,
            "targets": pd.DataFrame(),
            "config": {
                "max_weight": 1.0,
                "frequency": "ME",
                "lookback": 24,
                "min_trade": 1,
            },
        },
        data,
    )
    forecast = state["liquidity"].query("kind == 'indicative_redemption'").iloc[0]
    assert forecast.terms_version_id == "new-v2"
    assert forecast.lot_terms_version_id == "new-v1"
    assert forecast.lot_lock_days == 10

    sell = ledger.submit("F", "SELL", shares=1000)
    assert sell.deal_date == pd.Timestamp("2024-01-19")
    assert sell.terms_version_id == "new-v2"
    records[2]["sell_tiers"][0][1] = 0.9
    for date in calendar[calendar.get_loc("2024-01-18") : calendar.get_loc("2024-01-24") + 1]:
        ledger.advance(date)
    assert ledger.trades[1]["fee"] == pytest.approx(50)
    assert ledger.cash == pytest.approx(950)
    assert ledger.receivables == []


def test_versioned_lot_without_purchase_evidence_fails(make_dataset):
    data = make_dataset()
    attach(data, [version(data, "all", "v1", "2023-01-01", None, "2023-01-01")])
    ledger = Ledger(data)
    ledger.advance(data.calendar[0])
    ledger.lots.append(Lot("F", data.calendar[0], 10))
    with pytest.raises(ValueError, match="缺少申购版本"):
        ledger.submit("F", "SELL", shares=1)


def test_versioned_order_survives_deepcopy_and_daily_rollback(make_dataset, monkeypatch):
    data = make_dataset(confirm_lag=0)
    attach(data, [version(data, "all", "v1", "2023-01-01", None, "2023-01-01")])
    ledger = Ledger(data, 1000)
    ledger.advance(data.calendar[0])
    order = ledger.submit("F", "BUY", amount=1000)
    copied = deepcopy(ledger)
    assert copied.orders[0].terms.version_id == "v1"

    with monkeypatch.context() as patch:
        patch.setattr(ledger, "snapshot", lambda: (_ for _ in ()).throw(RuntimeError("失败")))
        with pytest.raises(RuntimeError, match="失败"):
            ledger.advance(data.calendar[1])
    assert order.status == "pending"
    assert ledger.current_date == data.calendar[0]
    assert not ledger.lots and not ledger.trades
    ledger.advance(data.calendar[1])
    assert order.status == "confirmed"
    assert ledger.lots[0].terms_version_id == "v1"


def test_historical_demo_uses_identity_only_funds_file(tmp_path):
    from quant_fund.demo import create_demo

    root = create_demo(tmp_path / "historical", historical_terms=True)
    data = Dataset.load(root)
    assert data.historical_terms
    assert all(isinstance(item, FundIdentity) for item in data.funds.values())
    assert set(asdict(next(iter(data.funds.values())))) == {
        "fund_id",
        "name",
        "inception",
        "known_at",
    }


def test_balanced_allocation_uses_decision_date_classification(tmp_path):
    from quant_fund.demo import create_demo

    source = create_demo(tmp_path / "classifications", historical_terms=True)
    terms = json.loads((source / "fund_terms.json").read_text(encoding="utf-8"))
    old = next(item for item in terms if item["fund_id"] == "PUB_EQ_A")
    old["effective_to"] = "2025-01-01"
    changed = deepcopy(old)
    changed.update(
        {
            "terms_id": "PUB_EQ_A:reclassified",
            "version_id": "PUB_EQ_A:reclassified:v1",
            "effective_from": "2025-01-01",
            "effective_to": None,
            "known_at": "2024-12-01",
            "strategy": "bond",
        }
    )
    terms.append(changed)
    (source / "fund_terms.json").write_text(
        json.dumps(terms, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    data = Dataset.load(source)
    before, before_info = allocate(data, "2024-12-31", "balanced")
    after, after_info = allocate(data, "2025-01-31", "balanced")
    assert before["PUB_EQ_A"] == pytest.approx(0.2 * 0.95)
    assert after["PUB_EQ_A"] == pytest.approx((0.4 / 3) * 0.95)
    assert before_info["term_versions"]["PUB_EQ_A"].endswith("initial:v1")
    assert after_info["term_versions"]["PUB_EQ_A"] == "PUB_EQ_A:reclassified:v1"


def test_backtest_uses_application_date_minimum_for_announced_reduction(tmp_path):
    from quant_fund.demo import create_demo

    source = create_demo(tmp_path / "minimum", historical_terms=True)
    terms = json.loads((source / "fund_terms.json").read_text(encoding="utf-8"))
    old = next(item for item in terms if item["fund_id"] == "PUB_EQ_A")
    old.update({"effective_to": "2024-01-03", "min_buy": 1_000_000})
    reduced = deepcopy(old)
    reduced.update(
        {
            "terms_id": "PUB_EQ_A:reduced-minimum",
            "version_id": "PUB_EQ_A:reduced-minimum:v1",
            "effective_from": "2024-01-03",
            "effective_to": None,
            "known_at": "2023-12-20",
            "min_buy": 100,
        }
    )
    terms.append(reduced)
    (source / "fund_terms.json").write_text(
        json.dumps(terms, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    data = Dataset.load(source)
    result = run_backtest(
        data, BacktestConfig(start="2024-01-02", end="2024-01-10", strategy="equal")
    )
    order = result["orders"].query("fund_id == 'PUB_EQ_A' and side == 'BUY'").iloc[0]
    assert order.submitted == pd.Timestamp("2024-01-02")
    assert order.deal_date == pd.Timestamp("2024-01-03")
    assert order.terms_version_id == "PUB_EQ_A:reduced-minimum:v1"


def test_versioned_report_preserves_leading_zero_and_rejects_resigned_bad_binding(
    tmp_path, monkeypatch
):
    from quant_fund.demo import create_demo

    source = create_demo(tmp_path / "source", historical_terms=True)
    identities = json.loads((source / "funds.json").read_text(encoding="utf-8"))
    original = identities[0]["fund_id"]
    identities[0]["fund_id"] = "000001"
    (source / "funds.json").write_text(
        json.dumps(identities, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    terms = json.loads((source / "fund_terms.json").read_text(encoding="utf-8"))
    record = next(item for item in terms if item["fund_id"] == original)
    record.update({"fund_id": "000001", "terms_id": "20001", "version_id": "10001"})
    (source / "fund_terms.json").write_text(
        json.dumps(terms, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    nav = pd.read_csv(source / "nav.csv", dtype={"fund_id": str})
    nav.loc[nav.fund_id == original, "fund_id"] = "000001"
    nav.to_csv(source / "nav.csv", index=False)
    dates = pd.read_csv(source / "calendar.csv").date.tolist()
    calendars = [
        {
            "calendar_id": purpose,
            "purpose": purpose,
            "version": "1",
            "available_at": "2021-12-31T00:00:00Z",
            "valid_from": dates[0],
            "valid_to": dates[-1],
            "open_days": dates,
            "source": "synthetic test",
            "evidence_kind": "synthetic",
        }
        for purpose in ("dealing", "confirmation", "banking")
    ]
    (source / "calendars.json").write_text(
        json.dumps(calendars, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    metadata = json.loads((source / "dataset.json").read_text(encoding="utf-8"))
    metadata["calendar_ids"] = {
        purpose: purpose for purpose in ("dealing", "confirmation", "banking")
    }
    (source / "dataset.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    pd.DataFrame(
        [
            {
                "disclosure_id": "synthetic-disclosure",
                "fund_id": "000001",
                "holding_date": "2023-12-29",
                "available_at": "2024-01-01T00:00:00+08:00",
                "instrument_id": "SYNTHETIC-SECURITY",
                "asset_type": "security",
                "currency": "CNY",
                "weight": "0.5",
                "source": "synthetic test",
                "evidence_id": "synthetic-holding",
            }
        ]
    ).to_csv(source / "holdings.csv", index=False)

    data = Dataset.load(source)
    result = run_backtest(
        data, BacktestConfig(start="2024-01-02", end="2024-05-31", strategy="equal")
    )
    monkeypatch.setattr("quant_fund.report.clean_git_commit", lambda root: "a" * 40)
    output = export_run(result, data, source, tmp_path / "report")
    verified = verify_run(output)
    assert verified["terms_mode"] == "historical_pit"
    assert verified["verified_term_bindings"] > 0
    archived = json.loads((output / "inputs/funds.json").read_text(encoding="utf-8"))
    assert archived[0]["fund_id"] == "000001"
    assert {
        "fund_terms.json",
        "calendars.json",
        "holdings.csv",
    }.issubset({path.name for path in (output / "inputs").iterdir()})

    manifest_path = output / "manifest.json"
    original_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    invalid = deepcopy(original_manifest)
    invalid["schema"] = "quant-fund.research-run@unknown"
    manifest_path.write_text(json.dumps(invalid, ensure_ascii=False, indent=2), encoding="utf-8")
    with pytest.raises(ValueError, match="不支持"):
        verify_run(output)

    invalid = deepcopy(original_manifest)
    invalid["historical_terms_pit"] = "true"
    manifest_path.write_text(json.dumps(invalid, ensure_ascii=False, indent=2), encoding="utf-8")
    with pytest.raises(ValueError, match="模式标记无效"):
        verify_run(output)

    from quant_fund.report import _legacy_input_fingerprint

    invalid = deepcopy(original_manifest)
    invalid["schema"] = "quant-fund.research-run@1"
    invalid["input_sha256"] = _legacy_input_fingerprint(output / "inputs")
    manifest_path.write_text(json.dumps(invalid, ensure_ascii=False, indent=2), encoding="utf-8")
    with pytest.raises(ValueError, match="旧版研究产物不能包含"):
        verify_run(output)

    orders = pd.read_csv(output / "orders.csv", dtype=str)
    duplicate_orders = pd.concat([orders, orders.tail(1)], ignore_index=True)
    duplicate_orders.to_csv(output / "orders.csv", index=False)
    invalid = deepcopy(original_manifest)
    invalid["files"]["orders.csv"] = hashlib.sha256(
        (output / "orders.csv").read_bytes()
    ).hexdigest()
    manifest_path.write_text(json.dumps(invalid, ensure_ascii=False, indent=2), encoding="utf-8")
    with pytest.raises(ValueError, match="order_id重复"):
        verify_run(output)

    collision = orders.iloc[[0]].copy()
    collision.loc[:, "order_id"] = f"0{collision.iloc[0].order_id}"
    collision_orders = pd.concat([orders, collision], ignore_index=True)
    collision_orders.to_csv(output / "orders.csv", index=False)
    invalid = deepcopy(original_manifest)
    invalid["files"]["orders.csv"] = hashlib.sha256(
        (output / "orders.csv").read_bytes()
    ).hexdigest()
    manifest_path.write_text(json.dumps(invalid, ensure_ascii=False, indent=2), encoding="utf-8")
    with pytest.raises(ValueError, match="归一后冲突"):
        verify_run(output)

    for invalid_order_id in ("", "1.0", "0", "-1", "01"):
        malformed = orders.copy()
        malformed.loc[0, "order_id"] = invalid_order_id
        malformed.to_csv(output / "orders.csv", index=False)
        invalid = deepcopy(original_manifest)
        invalid["files"]["orders.csv"] = hashlib.sha256(
            (output / "orders.csv").read_bytes()
        ).hexdigest()
        manifest_path.write_text(
            json.dumps(invalid, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        with pytest.raises(ValueError, match="order_id"):
            verify_run(output)

    orders.to_csv(output / "orders.csv", index=False)
    orders.loc[0, "terms_version_id"] = "99999"
    orders.to_csv(output / "orders.csv", index=False)
    manifest = deepcopy(original_manifest)
    manifest["files"]["orders.csv"] = hashlib.sha256(
        (output / "orders.csv").read_bytes()
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    with pytest.raises(ValueError, match="无法从归档输入复核"):
        verify_run(output)
