import json
from dataclasses import asdict

import pytest

from quant_fund.data import Dataset
from quant_fund.engine import BacktestConfig, Ledger, run_backtest
from quant_fund.preflight import preflight


def test_preflight_is_read_only_and_does_not_replay(demo_directory, tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    config.write_text(json.dumps(asdict(BacktestConfig(strategy="equal"))))
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in demo_directory.iterdir()}

    def forbidden(*args, **kwargs):
        pytest.fail("preflight must not create a ledger")

    monkeypatch.setattr(Ledger, "__init__", forbidden)
    result = preflight(demo_directory, config)
    assert result["software_preflight"] == "pass" and result["read_only"]
    assert result["classification"] == "synthetic" and not result["investable"]
    assert result["symbols"] == 7 and result["account_dates"] > 100
    assert before == {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in demo_directory.iterdir()}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.json"]


@pytest.mark.parametrize(
    "change",
    [
        {"strategy": "wrong"},
        {"initial_cash": float("nan")},
        {"cash_buffer": 1},
        {"max_weight": 0},
        {"frequency": "invalid"},
    ],
)
def test_invalid_static_config_is_rejected_before_cash_only_run(change):
    with pytest.raises(ValueError):
        BacktestConfig(**change)


def test_short_window_rejected_by_preflight_and_run(demo_directory, tmp_path):
    config = BacktestConfig(start="2030-01-01", end="2030-01-02")
    path = tmp_path / "config.json"
    path.write_text(json.dumps(asdict(config)))
    with pytest.raises(ValueError, match="两个"):
        preflight(demo_directory, path)
    with pytest.raises(ValueError, match="两个"):
        run_backtest(Dataset.load(demo_directory), config)


def test_input_change_during_load_is_rejected(demo_directory, tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(asdict(BacktestConfig())))
    original = Dataset.load

    def changed(source):
        data = original(source)
        path.write_text(json.dumps(asdict(BacktestConfig(initial_cash=200000))))
        return data

    monkeypatch.setattr(Dataset, "load", changed)
    with pytest.raises(ValueError, match="期间改变"):
        preflight(demo_directory, path)
