"""Versioned, read-only snapshots for external platform adapters."""

import json


def records(frame):
    # pandas serializes timestamps and missing values without non-standard NaN tokens.
    return json.loads(frame.to_json(orient="records", date_format="iso"))


def platform_snapshot(result, dataset, monitoring=None):
    if monitoring is None:
        from .monitor import monitor

        monitoring = monitor(result, dataset)
    ledger = result["ledger"]
    account = ledger.snapshot()
    account.pop("date")
    return {
        "schema": "quant-fund.portfolio-snapshot@1",
        "mode": "research_only",
        "as_of": ledger.current_date.strftime("%Y-%m-%d"),
        "currency": "CNY",
        "dataset": {
            "classification": dataset.metadata["classification"],
            "sha256": dataset.fingerprint,
        },
        "account": account,
        "config": result["config"],
        "holdings": records(monitoring["holdings"]),
        "orders": records(result["orders"]),
        "targets": records(result["targets"]),
        "risk": records(monitoring["risk"].rename_axis("fund_id").reset_index()),
        "alerts": records(monitoring["alerts"]),
        "liquidity": records(monitoring["liquidity"]),
        "rebalance_review": records(monitoring["rebalance_review"]),
    }
