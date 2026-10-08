"""Rebuild account evidence from archived instructions and PIT input data."""

from io import StringIO

import numpy as np
import pandas as pd

from .engine import BacktestConfig, Ledger, validate_backtest_inputs

NUMERIC_COLUMNS = {
    "nav": {
        "cash",
        "frozen_cash",
        "receivables",
        "holdings_value",
        "total_value",
        "nav",
        "pending_orders",
    },
    "orders": {"order_id", "amount", "shares"},
    "trades": {
        "order_id",
        "unit_nav",
        "shares",
        "gross",
        "fee",
        "lot_lock_days",
        "rounding_residual",
    },
    "lots": {"shares", "reserved", "lock_days"},
    "order_lots": {"order_id", "shares", "lot_lock_days"},
}


def read_table(source):
    try:
        return pd.read_csv(source, dtype=str, keep_default_na=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _compare(root, name, expected):
    actual = read_table(root / f"{name}.csv")
    expected = read_table(StringIO(expected.to_csv(index=False)))
    # Schema@2 runs created before request identities have unkeyed instructions.
    if name == "orders" and "request_id" not in actual and "request_id" in expected:
        actual["request_id"] = ""
    if len(actual) != len(expected) or set(actual) != set(expected):
        raise ValueError(f"账本重放与{name}的行数或字段不一致")
    for column in expected:
        left, right = actual[column], expected[column]
        if column in NUMERIC_COLUMNS[name]:
            missing = right.eq("")
            if not left.eq("").equals(missing):
                raise ValueError(f"账本重放与{name}.{column}的空值不一致")
            try:
                observed = pd.to_numeric(left[~missing]).to_numpy(dtype=float)
                rebuilt = pd.to_numeric(right[~missing]).to_numpy(dtype=float)
            except ValueError as exc:
                raise ValueError(f"账本重放发现{name}.{column}的数值无效") from exc
            tolerance = 1e-6
            if column in {"order_id", "pending_orders", "lot_lock_days", "lock_days"}:
                tolerance = 0
            elif column in {"shares", "reserved"}:
                tolerance = 1e-8
            elif column == "nav":
                tolerance = 1e-12
            if not np.isfinite(observed).all() or not np.allclose(
                observed, rebuilt, rtol=0, atol=tolerance
            ):
                raise ValueError(f"账本重放与{name}.{column}不一致")
        elif not left.equals(right):
            raise ValueError(f"账本重放与{name}.{column}不一致")


def verify_ledger_replay(root, dataset, config):
    """Do not rerun the optimizer: replay saved requests against archived business facts."""
    config = BacktestConfig(**config)
    dates = validate_backtest_inputs(dataset, config)
    orders = read_table(root / "orders.csv")
    ledger = Ledger(dataset, config.initial_cash)
    submitted = set()
    for date in dates:
        ledger.advance(date)
        if not orders.empty:
            for index, row in orders.loc[orders.submitted == str(date.date())].iterrows():
                request_id = row.get("request_id") or None
                ledger.submit(
                    row.fund_id,
                    row.side,
                    amount=float(row.amount),
                    shares=float(row.shares),
                    request_id=request_id,
                )
                submitted.add(index)
        ledger.history[-1] = ledger.snapshot()
    if len(submitted) != len(orders):
        raise ValueError("账本重放发现区间外或非法提交日期的订单")
    tables = {
        "nav": pd.DataFrame(ledger.history),
        "orders": ledger.orders_frame(),
        "trades": pd.DataFrame(ledger.trades),
        "lots": ledger.lots_frame(),
        "order_lots": ledger.order_lots_frame(),
    }
    for name, table in tables.items():
        _compare(root, name, table)
    return {"verified_ledger_days": len(dates), "verified_ledger_orders": len(ledger.orders)}
