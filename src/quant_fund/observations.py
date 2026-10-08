"""Read-only comparison of modeled trades with separately supplied observations."""

import csv
import hashlib
import json
from decimal import Decimal, InvalidOperation
from io import StringIO
from pathlib import Path

from .data import Dataset, day
from .report import verify_run

CONFIRMATION_COLUMNS = (
    "evidence_id",
    "order_id",
    "fund_id",
    "side",
    "deal_date",
    "confirmed",
    "unit_nav",
    "shares",
    "gross",
    "fee",
    "currency",
    "source_ref",
)
RECEIPT_COLUMNS = (
    "evidence_id",
    "order_id",
    "fund_id",
    "received",
    "amount",
    "currency",
    "source_ref",
)
TOLERANCES = {
    "unit_nav": Decimal("0.00000001"),
    "shares": Decimal("0.00000001"),
    "gross": Decimal("0.000001"),
    "fee": Decimal("0.000001"),
    "amount": Decimal("0.000001"),
}


def _number(value, field):
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError) as exc:
        raise ValueError(f"Invalid observation number: {field}") from exc
    if not number.is_finite() or number < 0 or (field in {"shares", "unit_nav"} and number == 0):
        raise ValueError(f"Invalid observation number: {field}")
    return number


def _order_id(value):
    if not value.isascii() or not value.isdigit() or int(value) < 1:
        raise ValueError("Observation order_id must be a positive integer")
    return str(int(value))


def _read(path, columns, *, batched=False):
    raw = Path(path).read_bytes()
    reader = csv.DictReader(StringIO(raw.decode("utf-8-sig")))
    if reader.fieldnames is None or len(set(reader.fieldnames)) != len(reader.fieldnames):
        raise ValueError("Observation CSV must have unique headers")
    if set(reader.fieldnames) != set(columns):
        raise ValueError(f"Observation CSV requires exactly these columns: {','.join(columns)}")
    rows, ids, evidence = {}, set(), set()
    for row in reader:
        if None in row or any(not isinstance(v, str) or not v.strip() for v in row.values()):
            raise ValueError("Observation CSV contains empty or malformed fields")
        row = {key: value.strip() for key, value in row.items()}
        order_key = _order_id(row["order_id"])
        key = row["evidence_id"] if batched else order_key
        if key in ids or row["evidence_id"] in evidence:
            raise ValueError("Duplicate order or evidence identity in observations")
        ids.add(key)
        evidence.add(row["evidence_id"])
        row["order_id"] = order_key
        if row["currency"] != "CNY":
            raise ValueError("Only explicit CNY observations are supported")
        for field in set(row).intersection(TOLERANCES):
            _number(row[field], field)
        for field in set(row).intersection({"deal_date", "confirmed", "received"}):
            row[field] = str(day(row[field]).date())
        if "side" in row:
            if row["side"] not in {"BUY", "SELL"}:
                raise ValueError("Observation side must be BUY or SELL")
            if row["confirmed"] < row["deal_date"]:
                raise ValueError("Confirmation cannot precede dealing date")
        rows[key] = row
    return rows, hashlib.sha256(raw).hexdigest()


def _compare(expected, observed, fields, kind):
    differences = []
    for key in sorted(set(expected) | set(observed), key=int):
        left, right = expected.get(key), observed.get(key)
        if left is None or right is None:
            differences.append(
                {
                    "kind": kind,
                    "order_id": key,
                    "issue": "unexpected" if left is None else "missing",
                }
            )
            continue
        for field in fields:
            a, b = left[field], right[field]
            if field in TOLERANCES:
                delta = _number(b, field) - _number(a, field)
                equal = abs(delta) <= TOLERANCES[field]
            else:
                equal = a == b
            if not equal:
                differences.append(
                    {
                        "kind": kind,
                        "order_id": key,
                        "issue": "mismatch",
                        "field": field,
                        "expected": a,
                        "observed": b,
                    }
                )
    return differences


def _redemption_date(dataset, trade, as_of):
    if not trade["settle_date"]:
        return None
    earliest = max(day(trade["settle_date"]), day(trade["confirmed"]))
    for date in dataset.account_calendar:
        if date < earliest or date > day(as_of):
            continue
        if dataset.purpose_calendars is not None:
            calendar, at = dataset.purpose_calendar("banking", date, date)
            if not calendar.is_open(date, at=at, purpose="banking"):
                continue
        return str(date.date())
    return None


def reconcile_observations(directory, confirmations, receipts):
    """Compare supplied records; never book cash, alter a run, or certify authenticity."""
    root = Path(directory)
    paths = [
        root / "manifest.json",
        root / "trades.csv",
        root / "nav.csv",
        Path(confirmations),
        Path(receipts),
    ]
    before = {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    verified = verify_run(root)
    if verified.get("ledger_replay") != "pass":
        raise ValueError("Observation reconciliation requires a replay-verifiable run")
    dataset = Dataset.load(root / "inputs")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    actual, confirmation_hash = _read(confirmations, CONFIRMATION_COLUMNS)
    cash, receipt_hash = _read(receipts, RECEIPT_COLUMNS)
    trades = list(csv.DictReader(StringIO((root / "trades.csv").read_text(encoding="utf-8"))))
    nav = list(csv.DictReader(StringIO((root / "nav.csv").read_text(encoding="utf-8"))))
    # Use the actual verified run interval, not a requested end outside the calendar.
    as_of = str(day(nav[-1]["date"]).date())
    expected, arrivals = {}, {}
    pending = []
    for trade in trades:
        key = _order_id(trade["order_id"])
        expected[key] = {
            field: trade[field]
            for field in (
                "fund_id",
                "side",
                "deal_date",
                "confirmed",
                "unit_nav",
                "shares",
                "gross",
                "fee",
            )
        }
        for field in ("deal_date", "confirmed"):
            expected[key][field] = str(day(expected[key][field]).date())
        if trade["side"] != "SELL":
            continue
        planned = _redemption_date(dataset, trade, as_of)
        if planned is None:
            pending.append(key)
            continue
        arrivals[key] = {
            "fund_id": trade["fund_id"],
            "received": planned,
            "amount": str(_number(trade["gross"], "gross") - _number(trade["fee"], "fee")),
        }
    fields = ("fund_id", "side", "deal_date", "confirmed", "unit_nav", "shares", "gross", "fee")
    differences = _compare(expected, actual, fields, "confirmation")
    differences += _compare(arrivals, cash, ("fund_id", "received", "amount"), "redemption_receipt")
    if before != {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}:
        raise ValueError("Inputs changed during observation reconciliation")
    return {
        "schema": "quant-fund.observation-reconciliation/v1",
        "status": "matched"
        if not differences and expected
        else "differences"
        if differences
        else "no_trades",
        "read_only": True,
        "real_business_certified": False,
        "classification": manifest["classification"],
        "as_of": as_of,
        "verified_ledger_orders": verified["verified_ledger_orders"],
        "expected_confirmations": len(expected),
        "supplied_confirmations": len(actual),
        "expected_redemption_receipts": len(arrivals),
        "supplied_redemption_receipts": len(cash),
        "pending_redemption_order_ids": pending,
        "differences": differences,
        "run_manifest_sha256": before[str((root / "manifest.json").resolve())],
        "confirmation_sha256": confirmation_hash,
        "receipt_sha256": receipt_hash,
        "absolute_tolerances": {k: str(v) for k, v in TOLERANCES.items()},
        "limitations": [
            "Supplied observations and source references are not independently authenticated",
            "Only confirmed modeled trades and calendar-based redemption arrivals are compared",
            "No certification of bank debits, dividend receipts, bank balances or pending orders",
            "No rounding policy inferred; discrepancies require source and model review",
            "No account mutation or extension of the verified run interval",
        ],
    }
