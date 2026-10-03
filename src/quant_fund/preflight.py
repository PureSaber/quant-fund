"""Read-only dataset and static account prerequisites; no allocation or ledger replay."""

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from .data import Dataset
from .engine import BacktestConfig, validate_backtest_inputs


def _sources(root, config):
    files = [path for path in root.rglob("*") if path.is_file()]
    return {
        str(path.resolve()): (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_mtime_ns,
        )
        for path in [*files, config]
    }


def preflight(dataset_path, config_path):
    root, config_path = Path(dataset_path).resolve(), Path(config_path).resolve()
    before = _sources(root, config_path)
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    config = BacktestConfig(**saved.get("config", saved))
    dataset = Dataset.load(root)
    dates = validate_backtest_inputs(dataset, config)
    if before != _sources(root, config_path):
        raise ValueError("输入在预检期间改变，请重新检查")
    return {
        "schema_version": "quant-fund.preflight/v1",
        "software_preflight": "pass",
        "read_only": True,
        "investable": False,
        "classification": dataset.metadata["classification"],
        "symbols": len(dataset.funds),
        "rows": len(dataset.nav),
        "account_dates": len(dates),
        "interval": {"start": str(dates[0].date()), "end": str(dates[-1].date())},
        "input_sha256": dataset.fingerprint,
        "config_sha256": before[str(config_path)][0],
        "config": asdict(config),
        "checks": ["dataset schema and known-at rules", "calendars", "static account inputs"],
        "limitations": [
            "No strategy allocation, historical return sufficiency or optimizer feasibility check",
            "No order replay, actual confirmations or complete real-business certification",
            "A later run reloads inputs; preflight does not freeze them",
        ],
    }
