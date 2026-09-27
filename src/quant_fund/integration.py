"""Versioned, read-only snapshots for external platform adapters."""

import json

import pandas as pd


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
    snapshot = {
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
    if dataset.disclosures is not None:
        from quant_data_kit.financial.holdings import exposure_summary, look_through

        held = ledger.holdings()
        total = ledger.snapshot()["total_value"]
        weights = {row.fund_id: str(row.value / total) for row in held.itertuples()}
        at = ledger.current_date.tz_localize("Asia/Shanghai") + pd.Timedelta(hours=23, minutes=59)
        leaves = look_through(dataset.disclosures, weights, at)
        snapshot["lookthrough"] = json.loads(json.dumps(exposure_summary(leaves), default=str))
        snapshot["lookthrough"]["leaves"] = records(leaves)
    return snapshot
