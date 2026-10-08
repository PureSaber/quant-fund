"""Read-only partial confirmations and cash receipts linked to their actual confirmation."""

import csv
import hashlib
import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from .data import Dataset, day
from .observations import (
    CONFIRMATION_COLUMNS,
    RECEIPT_COLUMNS,
    TOLERANCES,
    _compare,
    _number,
    _read,
    _redemption_date,
)
from .report import verify_run

BATCH_CONFIRMATION_COLUMNS = (*CONFIRMATION_COLUMNS, "final")
BATCH_RECEIPT_COLUMNS = (*RECEIPT_COLUMNS, "confirmation_id")


def reconcile_batches(directory, confirmations, receipts):
    root = Path(directory)
    tracked = [root / name for name in ("manifest.json", "trades.csv", "nav.csv")]
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tracked}
    if verify_run(root).get("ledger_replay") != "pass":
        raise ValueError("Batch reconciliation requires a replay-verifiable run")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    dataset = Dataset.load(root / "inputs")
    with (root / "nav.csv").open(encoding="utf-8") as stream:
        as_of = list(csv.DictReader(stream))[-1]["date"]
    with (root / "trades.csv").open(encoding="utf-8") as stream:
        expected = {row["order_id"]: row for row in csv.DictReader(stream)}
    actual, confirmation_hash = _read(confirmations, BATCH_CONFIRMATION_COLUMNS, batched=True)
    cash, receipt_hash = _read(receipts, BATCH_RECEIPT_COLUMNS, batched=True)
    grouped, paid = defaultdict(list), defaultdict(list)
    for key, row in actual.items():
        if row["final"] not in {"true", "false"}:
            raise ValueError("Batch final flag must be true or false")
        if day(row["confirmed"]) > day(as_of):
            raise ValueError("Batch confirmation exceeds the verified run cutoff")
        if _number(row["fee"], "fee") > _number(row["gross"], "gross"):
            raise ValueError("Confirmation fee exceeds gross amount")
        grouped[row["order_id"]].append(row)
    for row in cash.values():
        parent = actual.get(row["confirmation_id"])
        if parent is None or parent["side"] != "SELL":
            raise ValueError("Cash receipt must reference an existing redemption confirmation")
        if parent["fund_id"] != row["fund_id"] or parent["order_id"] != row["order_id"]:
            raise ValueError("Cash receipt identity differs from confirmation")
        if not day(parent["confirmed"]) <= day(row["received"]) <= day(as_of):
            raise ValueError("Cash receipt is before confirmation or beyond run cutoff")
        if _number(row["amount"], "amount") <= 0:
            raise ValueError("Cash receipt must be positive")
        paid[row["confirmation_id"]].append(row)
    aggregate, open_orders = {}, []
    differences, cash_status = [], []
    for key, parts in grouped.items():
        first = parts[0]
        if any((row["fund_id"], row["side"]) != (first["fund_id"], first["side"]) for row in parts):
            raise ValueError("One order cannot mix funds or sides")
        final = [row for row in parts if row["final"] == "true"]
        latest = max(row["confirmed"] for row in parts)
        if len(final) > 1 or (final and final[0]["confirmed"] != latest):
            raise ValueError("Final confirmation must be unique and cannot precede another batch")
        if not final:
            open_orders.append(key)
        aggregate[key] = {
            "fund_id": first["fund_id"],
            "side": first["side"],
            "confirmed": latest,
            "deal_date": first["deal_date"],
        }
        for field in ("shares", "gross", "fee"):
            aggregate[key][field] = str(
                sum((_number(row[field], field) for row in parts), Decimal(0))
            )
        model = expected.get(key)
        if model is not None:
            # Do not average away different dealing dates or NAVs across partial confirmations.
            for part in parts:
                for field in ("deal_date", "unit_nav"):
                    if _compare({key: model}, {key: part}, (field,), "confirmation"):
                        differences.append(
                            {
                                "kind": "confirmation",
                                "order_id": key,
                                "evidence_id": part["evidence_id"],
                                "issue": "mismatch",
                                "field": field,
                                "expected": model[field],
                                "observed": part[field],
                            }
                        )
        for part in parts:
            if part["side"] != "SELL":
                continue
            net = _number(part["gross"], "gross") - _number(part["fee"], "fee")
            payments = paid[part["evidence_id"]]
            received = sum((_number(row["amount"], "amount") for row in payments), Decimal(0))
            outstanding = net - received
            due = _redemption_date(dataset, model, as_of) if model is not None else None
            state = (
                "overpaid"
                if outstanding < -TOLERANCES["amount"]
                else "unpaid"
                if outstanding > TOLERANCES["amount"]
                else "paid"
            )
            if state == "overpaid" or (state == "unpaid" and due is not None):
                differences.append(
                    {
                        "kind": "cash",
                        "order_id": key,
                        "evidence_id": part["evidence_id"],
                        "issue": state,
                    }
                )
            if model is not None:
                for payment in payments:
                    if due is None or payment["received"] != due:
                        differences.append(
                            {
                                "kind": "cash",
                                "order_id": key,
                                "evidence_id": payment["evidence_id"],
                                "issue": "arrival_differs_from_model",
                                "expected": due,
                                "observed": payment["received"],
                            }
                        )
            cash_status.append(
                {
                    "order_id": key,
                    "confirmation_id": part["evidence_id"],
                    "confirmed_net": str(net),
                    "received": str(received),
                    "outstanding": str(outstanding),
                    "status": state,
                    "payments": [
                        {k: row[k] for k in ("evidence_id", "received", "amount")}
                        for row in payments
                    ],
                }
            )
    differences += _compare(
        expected,
        aggregate,
        ("fund_id", "side", "confirmed", "shares", "gross", "fee"),
        "confirmation",
    )
    pending = bool(open_orders) or any(row["status"] == "unpaid" for row in cash_status)
    status = (
        "differences"
        if differences
        else "pending"
        if pending
        else "matched"
        if expected
        else "no_trades"
    )
    if (
        hashlib.sha256(Path(confirmations).read_bytes()).hexdigest() != confirmation_hash
        or hashlib.sha256(Path(receipts).read_bytes()).hexdigest() != receipt_hash
    ):
        raise ValueError("Batch observations changed during reconciliation")
    if before != {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tracked}:
        raise ValueError("Run changed during batch reconciliation")
    return {
        "schema": "quant-fund.batch-reconciliation/v1",
        "status": status,
        "read_only": True,
        "real_business_certified": False,
        "classification": manifest["classification"],
        "as_of": as_of,
        "confirmation_batches": len(actual),
        "cash_receipts": len(cash),
        "orders_awaiting_final_confirmation": sorted(open_orders, key=int),
        "differences": differences,
        "cash_by_confirmation": cash_status,
        "confirmation_sha256": confirmation_hash,
        "receipt_sha256": receipt_hash,
        "run_manifest_sha256": before["manifest.json"],
        "limitations": [
            "Read-only observations do not change model orders, holdings or available cash",
            "Partial execution scheduling, bank debits and authenticity are not certified",
            "A modeled settlement date does not establish a contractual payment deadline",
        ],
    }
