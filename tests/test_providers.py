import sys
from types import SimpleNamespace

import pandas as pd
import pytest

from quant_fund.providers import fetch_akshare


def test_public_snapshot_uses_collection_date_and_total_returns(monkeypatch, tmp_path):
    raw = pd.DataFrame(
        {"净值日期": ["2024-01-02", "2024-01-03"], "单位净值": [1.0, 0.9], "日增长率": [0.0, 0.0]}
    )
    monkeypatch.setitem(
        sys.modules, "akshare", SimpleNamespace(fund_open_fund_info_em=lambda **kwargs: raw)
    )
    output = tmp_path / "snapshot.csv"
    fetch_akshare("000001", output, observed_date="2026-09-26")
    frame = pd.read_csv(output, dtype={"fund_id": str})
    assert frame.fund_id.tolist() == ["000001", "000001"]
    assert frame.total_return_nav.tolist() == [1.0, 1.0]
    assert set(frame.known_at) == {"2026-09-26"}
    with pytest.raises(ValueError, match="已存在"):
        fetch_akshare("000001", output)


def test_public_snapshot_refuses_incomplete_return_data(monkeypatch, tmp_path):
    raw = pd.DataFrame(
        {
            "净值日期": ["2024-01-02", "2024-01-03"],
            "单位净值": [1.0, 0.9],
            "日增长率": [0.0, float("nan")],
        }
    )
    monkeypatch.setitem(
        sys.modules, "akshare", SimpleNamespace(fund_open_fund_info_em=lambda **kwargs: raw)
    )
    with pytest.raises(ValueError, match="日收益率不完整"):
        fetch_akshare("000001", tmp_path / "snapshot.csv")
