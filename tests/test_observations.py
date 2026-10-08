import csv
import hashlib
import json
import sys
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd
import pytest

from quant_fund.cli import main
from quant_fund.data import Dataset
from quant_fund.engine import BacktestConfig, run_backtest
from quant_fund.observations import (
    CONFIRMATION_COLUMNS,
    RECEIPT_COLUMNS,
    _read,
    _redemption_date,
    reconcile_observations,
)
from quant_fund.report import export_run


def write_csv(path, columns, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return path


@pytest.fixture(scope="module")
def modeled_run(demo_directory, tmp_path_factory):
    dataset = Dataset.load(demo_directory)
    result = run_backtest(
        dataset, BacktestConfig(start="2024-01-02", end="2024-05-31", strategy="equal")
    )
    root = export_run(result, dataset, demo_directory, tmp_path_factory.mktemp("run") / "report")
    return root


@pytest.fixture
def observations(modeled_run, tmp_path):
    # These are deliberately model-derived synthetic records, never real evidence.
    with (modeled_run / "trades.csv").open(encoding="utf-8") as stream:
        trades = list(csv.DictReader(stream))
    confirmations, receipts = [], []
    for trade in trades:
        confirmations.append(
            {
                **{k: trade[k] for k in CONFIRMATION_COLUMNS if k in trade},
                "evidence_id": f"synthetic-confirm-{trade['order_id']}",
                "currency": "CNY",
                "source_ref": "synthetic:test-confirmation",
            }
        )
        if trade["side"] == "SELL" and trade["settle_date"] <= "2024-05-31":
            receipts.append(
                {
                    "evidence_id": f"synthetic-receipt-{trade['order_id']}",
                    "order_id": trade["order_id"],
                    "fund_id": trade["fund_id"],
                    "received": trade["settle_date"],
                    "amount": str(Decimal(trade["gross"]) - Decimal(trade["fee"])),
                    "currency": "CNY",
                    "source_ref": "synthetic:test-receipt",
                }
            )
    assert receipts, "Fixture must exercise redemption as well as subscription"
    return confirmations, receipts, tmp_path / "confirm.csv", tmp_path / "receipts.csv"


def compare(root, observations):
    confirmations, receipts, cpath, rpath = observations
    write_csv(cpath, CONFIRMATION_COLUMNS, confirmations)
    write_csv(rpath, RECEIPT_COLUMNS, receipts)
    return reconcile_observations(root, cpath, rpath)


def test_matched_records_never_certify_real_business_or_mutate_run(modeled_run, observations):
    before = {
        p: hashlib.sha256(p.read_bytes()).hexdigest() for p in modeled_run.rglob("*") if p.is_file()
    }
    result = compare(modeled_run, observations)
    assert result["status"] == "matched"
    assert result["expected_redemption_receipts"] > 0
    assert result["read_only"] and not result["real_business_certified"]
    assert result["classification"] == "synthetic"
    assert before == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before}


@pytest.mark.parametrize(
    "change,kind,issue,field",
    [
        ("fee", "confirmation", "mismatch", "fee"),
        ("shares", "confirmation", "mismatch", "shares"),
        ("cash", "redemption_receipt", "mismatch", "amount"),
        ("late", "redemption_receipt", "mismatch", "received"),
        ("missing", "confirmation", "missing", None),
        ("extra", "redemption_receipt", "unexpected", None),
    ],
)
def test_independent_discrepancies(modeled_run, observations, change, kind, issue, field):
    confirmations, receipts, _, _ = observations
    if change in {"fee", "shares"}:
        confirmations[0][change] = str(Decimal(confirmations[0][change]) + 1)
    elif change == "cash":
        receipts[0]["amount"] = str(Decimal(receipts[0]["amount"]) - 1)
    elif change == "late":
        receipts[0]["received"] = "2024-06-03"
    elif change == "missing":
        confirmations.pop()
    else:
        receipts.append({**receipts[0], "order_id": "99999", "evidence_id": "extra"})
    result = compare(modeled_run, observations)
    assert result["status"] == "differences"
    assert any(
        d["kind"] == kind and d["issue"] == issue and d.get("field") == field
        for d in result["differences"]
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("shares", "NaN"),
        ("fee", "Infinity"),
        ("gross", "-1"),
        ("unit_nav", "0"),
        ("currency", "USD"),
        ("source_ref", " "),
        ("confirmed", "2024-01-01"),
        ("order_id", "1.5"),
    ],
)
def test_malformed_evidence_is_rejected(observations, field, value):
    confirmations, _, cpath, _ = observations
    confirmations[0][field] = value
    write_csv(cpath, CONFIRMATION_COLUMNS, confirmations)
    with pytest.raises(ValueError):
        _read(cpath, CONFIRMATION_COLUMNS)


def test_duplicate_identity_and_extra_column_rejected(observations):
    confirmations, _, cpath, _ = observations
    write_csv(cpath, CONFIRMATION_COLUMNS, confirmations + confirmations[:1])
    with pytest.raises(ValueError, match="Duplicate"):
        _read(cpath, CONFIRMATION_COLUMNS)
    write_csv(cpath, (*CONFIRMATION_COLUMNS, "extra"), confirmations)
    with pytest.raises(ValueError, match="exactly"):
        _read(cpath, CONFIRMATION_COLUMNS)


def test_arrival_respects_confirmation_bank_calendar_and_run_cutoff():
    closed = pd.Timestamp("2024-01-05")
    bank = SimpleNamespace(is_open=lambda date, **kwargs: date != closed)
    dataset = SimpleNamespace(
        account_calendar=pd.bdate_range("2024-01-02", "2024-01-10"),
        purpose_calendars=True,
        purpose_calendar=lambda *args: (bank, args[1]),
    )
    trade = {"settle_date": "2024-01-04", "confirmed": "2024-01-05"}
    assert _redemption_date(dataset, trade, "2024-01-08") == "2024-01-08"
    assert _redemption_date(dataset, trade, "2024-01-05") is None
    assert _redemption_date(dataset, {**trade, "settle_date": ""}, "2024-01-10") is None


def test_cli_exit_code_reports_difference(modeled_run, observations, monkeypatch, capsys):
    observations[1].pop()
    compare(modeled_run, observations)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "quant-fund",
            "reconcile-observations",
            "--run",
            str(modeled_run),
            "--confirmations",
            str(observations[2]),
            "--receipts",
            str(observations[3]),
        ],
    )
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    assert json.loads(capsys.readouterr().out)["status"] == "differences"
