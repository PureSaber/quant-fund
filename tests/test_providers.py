import sys
from types import SimpleNamespace

import pandas as pd
import pytest

from quant_fund.providers import fetch_akshare


def test_public_snapshot_uses_collection_date_and_total_returns(monkeypatch, tmp_path):
    raw = pd.DataFrame(
        {
            "净值日期": ["2024-01-02", "2024-01-03", "2024-01-04"],
            "单位净值": [1.0, 0.9, 0.99],
            "日增长率": [0.0, -10.0, 10.0],
        }
    )
    sources = {
        "单位净值走势": raw,
        "累计净值走势": pd.DataFrame({"净值日期": raw["净值日期"], "累计净值": [1.0, 1.0, 1.09]}),
        "分红送配详情": pd.DataFrame(
            {
                "除息日": ["2024-01-03"],
                "每10份分红": ["每10份派现金1.0000元"],
            }
        ),
        "拆分详情": pd.DataFrame(),
    }
    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(fund_open_fund_info_em=lambda **kwargs: sources[kwargs["indicator"]]),
    )
    output = tmp_path / "snapshot.csv"
    fetch_akshare("000001", output, observed_date="2026-09-26")
    frame = pd.read_csv(output, dtype={"fund_id": str})
    assert frame.fund_id.tolist() == ["000001", "000001", "000001"]
    assert frame.total_return_nav.tolist() == pytest.approx([1.0, 1.0, 1.1])
    assert set(frame.known_at) == {"2026-09-26"}
    assert output.with_suffix(".dividends.raw.csv").is_file()
    assert output.with_suffix(".cumulative.raw.csv").is_file()
    with pytest.raises(ValueError, match="已存在"):
        fetch_akshare("000001", output)


def test_public_snapshot_refuses_missing_dividends(monkeypatch, tmp_path):
    raw = pd.DataFrame(
        {
            "净值日期": ["2024-01-02", "2024-01-03"],
            "单位净值": [1.0, 0.9],
            "日增长率": [0.0, -10.0],
        }
    )
    sources = {
        "单位净值走势": raw,
        "累计净值走势": pd.DataFrame({"净值日期": raw["净值日期"], "累计净值": [1.0, 1.0]}),
        "分红送配详情": pd.DataFrame(),
        "拆分详情": pd.DataFrame(),
    }
    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(fund_open_fund_info_em=lambda **kwargs: sources[kwargs["indicator"]]),
    )
    with pytest.raises(ValueError, match="分红.*不一致"):
        fetch_akshare("000001", tmp_path / "snapshot.csv", observed_date="2026-09-26")
    assert not list(tmp_path.iterdir())


@pytest.fixture
def public_sources(monkeypatch):
    dates = ["2024-01-02", "2024-01-03", "2024-01-04"]
    sources = {
        "单位净值走势": pd.DataFrame(
            {"净值日期": dates, "单位净值": [1.0, 0.9, 0.99], "日增长率": [0, -10, 10]}
        ),
        "累计净值走势": pd.DataFrame({"净值日期": dates, "累计净值": [1.0, 1.0, 1.09]}),
        "分红送配详情": pd.DataFrame(
            {"除息日": ["2024-01-03"], "每10份分红": ["每10份派现金1.0000元"]}
        ),
        "拆分详情": pd.DataFrame(),
    }
    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(fund_open_fund_info_em=lambda **kwargs: sources[kwargs["indicator"]]),
    )
    return sources


def test_one_share_dividend_unit_is_explicit_and_equivalent(public_sources, tmp_path):
    public_sources["分红送配详情"] = pd.DataFrame(
        {"除息日": ["2024-01-03"], "每份分红": ["每份派现金0.1000元"]}
    )
    output = tmp_path / "snapshot.csv"
    fetch_akshare("000001", output, observed_date="2026-09-26")
    assert pd.read_csv(output).total_return_nav.tolist() == pytest.approx([1, 1, 1.1])


def test_no_dividend_returns_use_nav_prices_without_rounded_growth(public_sources, tmp_path):
    public_sources["分红送配详情"] = pd.DataFrame()
    public_sources["累计净值走势"]["累计净值"] = [1.0, 0.9, 0.99]
    public_sources["单位净值走势"]["日增长率"] = [float("nan")] * 3
    output = tmp_path / "snapshot.csv"
    fetch_akshare("000001", output, observed_date="2026-09-26")
    assert pd.read_csv(output).total_return_nav.tolist() == pytest.approx([1, 0.9, 0.99])


@pytest.mark.parametrize(
    "issue,match",
    [
        ("missing_cumulative_date", "日期不一致"),
        ("duplicate_nav", "重复"),
        ("invalid_nav", "有限正数"),
        ("missing_ex_date_nav", "除息日缺少单位净值"),
        ("duplicate_dividend", "重复"),
        ("wrong_cash_unit", "金额格式"),
        ("unknown_cash_unit", "单位不明确"),
        ("incorrect_cash_total", "分红.*不一致"),
        ("share_split", "拆分"),
    ],
)
def test_public_snapshot_rejects_ambiguous_sources(public_sources, tmp_path, issue, match):
    if issue == "missing_cumulative_date":
        public_sources["累计净值走势"] = public_sources["累计净值走势"].iloc[:-1]
    elif issue == "duplicate_nav":
        public_sources["单位净值走势"] = pd.concat([public_sources["单位净值走势"]] * 2)
    elif issue == "invalid_nav":
        public_sources["单位净值走势"].loc[1, "单位净值"] = float("inf")
    elif issue == "missing_ex_date_nav":
        for indicator in ("单位净值走势", "累计净值走势"):
            public_sources[indicator] = public_sources[indicator].drop(index=1)
    elif issue == "duplicate_dividend":
        public_sources["分红送配详情"] = pd.concat([public_sources["分红送配详情"]] * 2)
    elif issue == "wrong_cash_unit":
        public_sources["分红送配详情"].loc[0, "每10份分红"] = "每份派现金1.0000元"
    elif issue == "unknown_cash_unit":
        public_sources["分红送配详情"] = public_sources["分红送配详情"].rename(
            columns={"每10份分红": "分红"}
        )
    elif issue == "incorrect_cash_total":
        public_sources["累计净值走势"].loc[2, "累计净值"] = 1.08
    elif issue == "share_split":
        public_sources["拆分详情"] = pd.DataFrame({"拆分折算日": ["2024-01-03"]})
    with pytest.raises(ValueError, match=match):
        fetch_akshare("000001", tmp_path / "snapshot.csv", observed_date="2026-09-26")
    assert not list(tmp_path.iterdir())


def test_source_sidecar_cannot_be_overwritten(public_sources, tmp_path):
    sidecar = tmp_path / "snapshot.dividends.raw.csv"
    sidecar.write_bytes(b"prior evidence")
    with pytest.raises(ValueError, match="已存在"):
        fetch_akshare("000001", tmp_path / "snapshot.csv")
    assert sidecar.read_bytes() == b"prior evidence"
    assert not (tmp_path / "snapshot.csv").exists()
