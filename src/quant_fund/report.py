"""Immutable local research runs with full inputs, hashes and machine-readable outputs."""

import hashlib
import html
import importlib.metadata
import json
import platform
import shutil
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from quant_lab.research_v2 import clean_git_commit, write_exploratory_run_v2

from .integration import platform_snapshot
from .monitor import monitor
from .research import performance


def export_run(result, dataset, source_directory, output):
    root = Path(output)
    root.mkdir(parents=True, exist_ok=False)
    tables = {
        key: result[key] for key in ("nav", "trades", "orders", "decisions", "targets", "events")
    }
    monitoring = monitor(result, dataset)
    tables.update(monitoring)
    (root / "platform-snapshot.json").write_text(
        json.dumps(
            platform_snapshot(result, dataset, monitoring),
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ),
        encoding="utf-8",
    )
    for name, table in tables.items():
        table.to_csv(root / f"{name}.csv", index=name in {"nav", "risk"})
    nav = result["nav"].nav
    monthly = nav.resample("ME").last().pct_change(fill_method=None).dropna()
    monthly = monthly.loc[monthly.index <= nav.index[-1]]
    metrics = performance(monthly, 12)
    metrics["full_period_return"] = float(nav.iloc[-1] / nav.iloc[0] - 1)
    metrics["full_period_max_drawdown"] = float((nav / nav.cummax() - 1).min())
    (root / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    (root / "inputs").mkdir()
    for name in ("funds.json", "nav.csv", "calendar.csv", "distributions.csv", "dataset.json"):
        shutil.copyfile(Path(source_directory) / name, root / "inputs" / name)
    shutil.copytree(
        Path(__file__).parent,
        root / "code" / "src" / "quant_fund",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    project = Path(__file__).resolve().parents[2]
    for name in ("pyproject.toml", "requirements.lock", "app.py", "README.md", "LICENSE"):
        if (project / name).exists():
            shutil.copyfile(project / name, root / "code" / name)
    manifest = {
        "schema": "quant-fund.research-run@1",
        "classification": dataset.metadata["classification"],
        "input_sha256": dataset.fingerprint,
        "config": result["config"],
        "python": platform.python_version(),
        "packages": {
            p: importlib.metadata.version(p)
            for p in ("quant-fund", "numpy", "pandas", "skfolio", "optimalportfolios", "qis")
        },
        "valuation": "当日已获知的单位净值；陈旧估值沿用并显示滞后天数",
        "execution": "收盘决策、下一开放日申请价、确切交易日净值获知后确认；到账后可用",
    }
    fig = go.Figure(go.Scatter(x=nav.index, y=nav, name="组合净值", line={"color": "#087f8c"}))
    fig.update_layout(
        template="plotly_white", height=420, margin={"l": 35, "r": 25, "t": 30, "b": 35}
    )
    label = html.escape(dataset.metadata.get("label", dataset.metadata["classification"]))
    notice = "合成数据仅验证软件。" if dataset.metadata["classification"] == "synthetic" else ""
    tables_html = "".join(
        f"<h2>{html.escape(title)}</h2>{tables[key].to_html(index=False, escape=True)}"
        for key, title in [
            ("holdings", "期末持仓"),
            ("alerts", "监控提示"),
            ("liquidity", "流动性日历"),
            ("rebalance_review", "调仓差额复核清单（非下单指令）"),
            ("decisions", "决策记录"),
        ]
    )
    document = f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8">
    <title>基金FOF研究报告</title><style>body{{max-width:1200px;margin:40px auto;padding:0 24px;
    font:15px/1.6 'Microsoft YaHei',sans-serif;color:#213547;background:#f7f9fb}}
    table{{border-collapse:collapse;background:white;width:100%;font-size:12px}}td,th{{padding:8px;
    border:1px solid #dfe7ec;text-align:left}}h2{{margin-top:36px}}.tag{{color:#95610b}}
    </style><h1>基金FOF研究报告</h1><p class="tag">{label}
    · {html.escape(result["config"]["strategy"])}
    · {result["config"]["start"]}—{result["config"]["end"]}</p>
    <p>{notice}低频净值下的组合曲线使用已知估值，不能表示实际日内风险。</p>
    {fig.to_html(full_html=False, include_plotlyjs=True)}{tables_html}
    <h2>复现信息</h2>
    <pre>{html.escape(json.dumps(manifest, ensure_ascii=False, indent=2))}</pre></html>"""
    (root / "report.html").write_text(document, encoding="utf-8")
    commit = clean_git_commit(Path(__file__).resolve().parents[2])
    if commit is not None:
        levels = result["nav"][["total_value"]].rename_axis("date").reset_index()
        levels = levels.rename(columns={"total_value": "nav"})
        write_exploratory_run_v2(
            root,
            project="quant-fund",
            run_id=root.name,
            strategy_id=str(result["config"]["strategy"]),
            currency="CNY",
            code_version=commit,
            dataset_snapshots={"dataset": dataset.fingerprint},
            nav=levels[["date", "nav"]],
            comparability="not_historical_universe",
            metrics={
                **metrics,
                "evidence_kind": dataset.metadata["classification"],
                "measurement_basis": {
                    "period_start": str(nav.index[0].date()),
                    "period_end": str(nav.index[-1].date()),
                    "currency": "CNY",
                    "monthly_annualization_periods": 12,
                    "sharpe_risk_free_rate": 0,
                    "valuation": manifest["valuation"],
                    "monthly_sample": "相邻完整月末已知估值的收益；不含首个非完整月区间",
                    "first_return_month_end": str(monthly.index[0].date())
                    if len(monthly)
                    else None,
                    "last_return_month_end": str(monthly.index[-1].date())
                    if len(monthly)
                    else None,
                    "benchmark": None,
                },
                "backtest_stats": [
                    {
                        "portfolio": "完整估值区间（已知估值）",
                        "total_return": metrics["full_period_return"],
                        "ann_return": None,
                        "sharpe": None,
                        "max_drawdown": metrics["full_period_max_drawdown"],
                    },
                    {
                        "portfolio": "完整月末收益样本（年化12期）",
                        "total_return": metrics["total_return"],
                        "ann_return": metrics["annual_return"],
                        "sharpe": metrics["sharpe"],
                        "max_drawdown": metrics["max_drawdown"],
                        "observations": metrics["observations"],
                    },
                ],
            },
            config={
                "currency": "CNY",
                "calendar": "synthetic_or_supplied",
                "study_config": result["config"],
            },
        )
    manifest["files"] = {
        str(p.relative_to(root)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return root


def verify_run(directory):
    root = Path(directory)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    for name, digest in manifest["files"].items():
        path = (root / name).resolve()
        if (
            not path.is_relative_to(root.resolve())
            or hashlib.sha256(path.read_bytes()).hexdigest() != digest
        ):
            raise ValueError(f"研究产物校验失败：{name}")
    from .data import Dataset

    if Dataset.load(root / "inputs").fingerprint != manifest["input_sha256"]:
        raise ValueError("输入数据摘要不匹配")
    nav = pd.read_csv(root / "nav.csv")
    difference = nav.total_value - nav[
        ["cash", "frozen_cash", "receivables", "holdings_value"]
    ].sum(axis=1)
    if difference.abs().max() > 1e-6:
        raise ValueError("资产分项不能与总资产对账")
    return {
        "verified_files": len(manifest["files"]),
        "max_reconciliation_error": float(difference.abs().max()),
    }
