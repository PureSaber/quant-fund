"""Immutable local research runs with full inputs, hashes and machine-readable outputs."""

import hashlib
import html
import importlib.metadata
import json
import platform
import shutil
from pathlib import Path

import numpy as np
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
        key: result[key]
        for key in (
            "nav",
            "trades",
            "orders",
            "lots",
            "order_lots",
            "decisions",
            "targets",
            "events",
            "term_versions",
        )
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
    input_files = dataset.input_files or tuple(
        name
        for name in (
            "funds.json",
            "nav.csv",
            "calendar.csv",
            "distributions.csv",
            "dataset.json",
            "calendars.json",
            "holdings.csv",
            "fund_terms.json",
        )
        if (Path(source_directory) / name).exists()
    )
    for name in input_files:
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
        "schema": "quant-fund.research-run@2",
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
        "terms_mode": dataset.terms_mode,
        "historical_terms_pit": dataset.historical_terms,
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

    dataset = Dataset.load(root / "inputs")
    schema = manifest.get("schema")
    fingerprint = (
        _legacy_input_fingerprint(root / "inputs")
        if schema == "quant-fund.research-run@1"
        else dataset.fingerprint
    )
    if fingerprint != manifest["input_sha256"]:
        raise ValueError("输入数据摘要不匹配")
    if schema == "quant-fund.research-run@1":
        if dataset.historical_terms:
            raise ValueError("旧版研究产物不能包含历史条款版本输入")
        verified_bindings = 0
    elif schema == "quant-fund.research-run@2":
        terms_mode = manifest.get("terms_mode")
        historical_pit = manifest.get("historical_terms_pit")
        if (
            terms_mode not in {"legacy_static", "historical_pit"}
            or type(historical_pit) is not bool
        ):
            raise ValueError("新版研究产物的条款模式标记无效")
        if terms_mode != dataset.terms_mode or historical_pit != dataset.historical_terms:
            raise ValueError("研究产物条款模式与归档输入不一致")
        required = {"lots.csv", "order_lots.csv", "term_versions.csv"}
        if not required.issubset(manifest["files"]):
            raise ValueError("新版研究产物缺少条款和份额批次证据")
        _verify_term_bindings(root, dataset)
        verified_bindings = _csv(root / "orders.csv").shape[0]
    else:
        raise ValueError(f"不支持的研究产物schema：{schema}")
    nav = pd.read_csv(root / "nav.csv")
    difference = nav.total_value - nav[
        ["cash", "frozen_cash", "receivables", "holdings_value"]
    ].sum(axis=1)
    if difference.abs().max() > 1e-6:
        raise ValueError("资产分项不能与总资产对账")
    return {
        "verified_files": len(manifest["files"]),
        "max_reconciliation_error": float(difference.abs().max()),
        "terms_mode": dataset.terms_mode,
        "verified_term_bindings": verified_bindings,
    }


def _csv(path):
    try:
        return pd.read_csv(path, dtype=str)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _legacy_input_fingerprint(root):
    digest = hashlib.sha256()
    for name in ("funds.json", "nav.csv", "calendar.csv", "distributions.csv", "dataset.json"):
        digest.update(name.encode())
        digest.update((root / name).read_bytes())
    for name in ("calendars.json", "holdings.csv"):
        if (root / name).exists():
            digest.update((root / name).read_bytes())
    return digest.hexdigest()


def _timestamp(value):
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).normalize()


def _same_timestamp(actual, expected):
    return _timestamp(actual) == _timestamp(expected)


def _boolean(value):
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized not in {"true", "false"}:
        raise ValueError(f"布尔证据字段无效：{value}")
    return normalized == "true"


def _parsed_order_id(value, source):
    if value is None or pd.isna(value):
        raise ValueError(f"{source}的order_id不能为空")
    text = str(value)
    if not text.isascii() or not text.isdecimal():
        raise ValueError(f"{source}的order_id必须为十进制正整数")
    normalized = int(text)
    if normalized <= 0:
        raise ValueError(f"{source}的order_id必须为十进制正整数")
    return text, normalized


def _canonical_order_id(value, source):
    text, normalized = _parsed_order_id(value, source)
    if text != str(normalized):
        raise ValueError(f"{source}的order_id必须使用规范十进制形式")
    return normalized


def _unique_order_ids(values):
    seen = {}
    normalized_ids = []
    for value in values:
        text, normalized = _parsed_order_id(value, "订单")
        if normalized in seen:
            raise ValueError(f"订单order_id重复或归一后冲突：{seen[normalized]}与{text}")
        if text != str(normalized):
            raise ValueError("订单的order_id必须使用规范十进制形式")
        seen[normalized] = text
        normalized_ids.append(normalized)
    return normalized_ids


def _verify_term_bindings(root, dataset):
    """Re-select archived order terms and recompute fees from archived inputs."""
    from .engine import Ledger

    orders = _csv(root / "orders.csv")
    trades = _csv(root / "trades.csv")
    lots = _csv(root / "lots.csv")
    order_lots = _csv(root / "order_lots.csv")
    evidence = _csv(root / "term_versions.csv")
    evidence_fields = {
        "fund_id",
        "terms_id",
        "version_id",
        "effective_from",
        "effective_to",
        "terms_known_at",
        "effective_on",
        "known_on",
        "use",
        "historical_pit",
    }
    if not evidence.empty and not evidence_fields.issubset(evidence):
        raise ValueError("研究产物缺少实际使用条款版本证据字段")
    for item in evidence.itertuples(index=False):
        terms = dataset.fund_at(item.fund_id, item.effective_on, item.known_on)
        if (
            item.terms_id != terms.terms_id
            or item.version_id != terms.version_id
            or not _same_timestamp(item.terms_known_at, terms.terms_known_at)
            or not _same_timestamp(item.effective_from, terms.effective_from)
            or not _same_timestamp(item.effective_to, terms.effective_to)
            or _boolean(item.historical_pit) != bool(terms.historical_pit)
        ):
            raise ValueError("实际使用条款版本证据无法从归档输入复核")
    if orders.empty:
        if not trades.empty or not lots.empty or not order_lots.empty:
            raise ValueError("无订单的产物不能包含成交、份额批次或赎回批次绑定")
        return
    required = {
        "order_id",
        "fund_id",
        "side",
        "submitted",
        "deal_date",
        "confirm_date",
        "settle_date",
        "amount",
        "shares",
        "status",
        "terms_id",
        "terms_version_id",
        "terms_known_at",
        "terms_effective_from",
        "terms_effective_to",
        "historical_terms_pit",
    }
    if not required.issubset(orders):
        raise ValueError("订单缺少条款冻结证据")
    order_ids = _unique_order_ids(orders.order_id)
    if (
        not orders.side.isin(["BUY", "SELL"]).all()
        or not orders.status.isin(["pending", "confirmed"]).all()
    ):
        raise ValueError("订单方向或状态无效")
    order_lot_fields = {
        "order_id",
        "fund_id",
        "lot_bought",
        "shares",
        "lot_terms_version_id",
        "lot_lock_days",
        "historical_terms_pit",
    }
    if not order_lots.empty and not order_lot_fields.issubset(order_lots):
        raise ValueError("赎回批次绑定缺少申购条款证据")
    if not order_lots.empty:
        for value in order_lots.order_id:
            _canonical_order_id(value, "赎回批次绑定")
    if not evidence_fields.issubset(evidence):
        raise ValueError("研究产物缺少实际使用条款版本证据字段")
    calendar = Ledger(dataset)
    resolved_orders = {}
    for order, order_id in zip(orders.itertuples(index=False), order_ids, strict=True):
        expected_deal, terms = calendar.dealing_terms(order.fund_id, order.submitted)
        if not _same_timestamp(order.deal_date, expected_deal):
            raise ValueError(f"订单{order.order_id}申请日不符合提交时可见的开放与预约条款")
        fields_match = (
            order.terms_id == terms.terms_id
            and order.terms_version_id == terms.version_id
            and _same_timestamp(order.terms_known_at, terms.terms_known_at)
            and _same_timestamp(order.terms_effective_from, terms.effective_from)
            and _same_timestamp(order.terms_effective_to, terms.effective_to)
            and _boolean(order.historical_terms_pit) == bool(terms.historical_pit)
        )
        if not fields_match:
            raise ValueError(f"订单{order.order_id}冻结的条款版本无法从归档输入复核")
        confirmation = calendar.offset(
            order.deal_date, terms.confirm_lag, "confirmation", known_on=order.submitted
        )
        settlement = calendar.offset(
            order.deal_date, terms.settle_lag, "banking", known_on=order.submitted
        )
        if not _same_timestamp(order.confirm_date, confirmation) or not _same_timestamp(
            order.settle_date, settlement
        ):
            raise ValueError(f"订单{order.order_id}确认或到账日期与冻结条款不一致")
        resolved_orders[order_id] = (order, terms)
        matching_evidence = evidence.loc[
            (evidence.fund_id == order.fund_id)
            & (evidence.version_id == order.terms_version_id)
            & (evidence.use == f"order_{order.side.lower()}")
            & (pd.to_datetime(evidence.effective_on) == _timestamp(order.deal_date))
            & (pd.to_datetime(evidence.known_on) == _timestamp(order.submitted))
        ]
        if matching_evidence.empty:
            raise ValueError(f"订单{order.order_id}缺少实际使用版本证据")
    trade_counts = {}
    trade_fields = {
        "order_id",
        "fund_id",
        "side",
        "submitted",
        "deal_date",
        "confirmed",
        "settle_date",
        "unit_nav",
        "shares",
        "gross",
        "fee",
        "terms_id",
        "terms_version_id",
        "terms_known_at",
        "terms_effective_from",
        "terms_effective_to",
        "historical_terms_pit",
    }
    if not trades.empty and not trade_fields.issubset(trades):
        raise ValueError("成交记录缺少冻结条款或逐笔经济证据")
    for trade in trades.itertuples(index=False):
        key = _canonical_order_id(trade.order_id, "成交")
        trade_counts[key] = trade_counts.get(key, 0) + 1
        if key not in resolved_orders:
            raise ValueError(f"成交{key}没有对应订单")
        order, terms = resolved_orders[key]
        if (
            trade.fund_id != order.fund_id
            or trade.side != order.side
            or not _same_timestamp(trade.submitted, order.submitted)
            or not _same_timestamp(trade.deal_date, order.deal_date)
            or not _same_timestamp(trade.settle_date, order.settle_date)
            or _timestamp(trade.confirmed) < _timestamp(order.confirm_date)
            or trade.terms_id != terms.terms_id
            or trade.terms_version_id != terms.version_id
            or not _same_timestamp(trade.terms_known_at, terms.terms_known_at)
            or not _same_timestamp(trade.terms_effective_from, terms.effective_from)
            or not _same_timestamp(trade.terms_effective_to, terms.effective_to)
            or _boolean(trade.historical_terms_pit) != bool(terms.historical_pit)
        ):
            raise ValueError(f"成交{key}条款版本与订单不一致")
        quote = dataset.quote(order.fund_id, trade.confirmed, exact_date=order.deal_date)
        if quote is None or not np.isclose(
            float(trade.unit_nav), float(quote.unit_nav), atol=1e-12, rtol=0
        ):
            raise ValueError(f"成交{key}未使用确认时已知的确切申请日净值")
        if order.side == "BUY":
            net = float(order.amount) / (1 + terms.buy_fee)
            expected_fee = float(order.amount) - net
            expected_shares = net / float(trade.unit_nav)
            expected_gross = float(order.amount)
        else:
            if order_lots.empty:
                raise ValueError(f"赎回订单{key}缺少份额批次绑定")
            allocations = order_lots.loc[order_lots.order_id == str(key)]
            if allocations.empty:
                raise ValueError(f"赎回订单{key}缺少份额批次绑定")
            if not np.isclose(
                pd.to_numeric(allocations.shares).sum(), float(order.shares), atol=1e-8, rtol=0
            ):
                raise ValueError(f"赎回订单{key}的批次份额不能与订单对账")
            for item in allocations.itertuples(index=False):
                if item.fund_id != order.fund_id:
                    raise ValueError(f"赎回订单{key}引用了其他基金的份额批次")
                buys = orders.loc[
                    (orders.side == "BUY")
                    & (orders.fund_id == order.fund_id)
                    & (pd.to_datetime(orders.deal_date) == _timestamp(item.lot_bought))
                    & (orders.terms_version_id == item.lot_terms_version_id)
                ]
                if buys.empty:
                    raise ValueError(f"赎回订单{key}引用的申购批次条款不存在")
                buy_terms = resolved_orders[_canonical_order_id(buys.iloc[0].order_id, "订单")][1]
                if (
                    int(item.lot_lock_days) != buy_terms.lock_days
                    or _boolean(item.historical_terms_pit) != bool(buy_terms.historical_pit)
                    or (_timestamp(order.deal_date) - _timestamp(item.lot_bought)).days
                    < buy_terms.lock_days
                ):
                    raise ValueError(f"赎回订单{key}违反申购批次冻结的锁定期")
            expected_fee = sum(
                float(item.shares)
                * float(trade.unit_nav)
                * terms.sell_rate((_timestamp(order.deal_date) - _timestamp(item.lot_bought)).days)
                for item in allocations.itertuples(index=False)
            )
            expected_shares = float(order.shares)
            expected_gross = expected_shares * float(trade.unit_nav)
        if (
            not np.isclose(float(trade.fee), expected_fee, atol=1e-6, rtol=0)
            or not np.isclose(float(trade.shares), expected_shares, atol=1e-8, rtol=0)
            or not np.isclose(float(trade.gross), expected_gross, atol=1e-6, rtol=0)
        ):
            raise ValueError(f"成交{key}费用或份额未按冻结条款计算")
    for key, (order, _) in resolved_orders.items():
        expected_count = 1 if order.status == "confirmed" else 0
        if trade_counts.get(key, 0) != expected_count:
            raise ValueError(f"订单{key}状态与成交记录数量不一致")
    if not lots.empty:
        lot_fields = {
            "fund_id",
            "bought",
            "shares",
            "reserved",
            "lock_days",
            "terms_version_id",
            "historical_terms_pit",
        }
        if not lot_fields.issubset(lots):
            raise ValueError("份额批次缺少申购条款证据")
        buys = orders.loc[orders.side == "BUY"]
        for lot in lots.itertuples(index=False):
            matches = buys.loc[
                (buys.fund_id == lot.fund_id)
                & (pd.to_datetime(buys.deal_date) == _timestamp(lot.bought))
                & (buys.terms_version_id == lot.terms_version_id)
            ]
            if matches.empty:
                raise ValueError("份额批次缺少对应的申购条款版本")
            terms = resolved_orders[_canonical_order_id(matches.iloc[0].order_id, "订单")][1]
            if int(lot.lock_days) != terms.lock_days or _boolean(lot.historical_terms_pit) != bool(
                terms.historical_pit
            ):
                raise ValueError("份额批次锁定期与申购条款版本不一致")
            if (
                float(lot.shares) < -1e-8
                or not 0 <= float(lot.reserved) <= float(lot.shares) + 1e-8
            ):
                raise ValueError("份额批次数量无效")
