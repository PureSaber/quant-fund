"""Read-only portfolio monitoring and non-binding liquidity projections."""

import numpy as np
import pandas as pd

from .research import risk_contributions, style_regression


def rebalance_review(result, dataset):
    """Target gaps for human review, never executable order instructions."""
    targets = result["targets"]
    if targets.empty:
        return pd.DataFrame()
    target_date = targets.date.max()
    weights = targets.loc[targets.date == target_date].set_index("fund_id").weight
    ledger = result["ledger"]
    holdings = ledger.holdings()
    values = holdings.set_index("fund_id").value if not holdings.empty else pd.Series(dtype=float)
    total = ledger.snapshot()["total_value"]
    pending = {o.fund_id for o in ledger.orders if o.status == "pending"}
    rows = []
    for code in sorted(set(weights.index) | set(values.index)):
        fund = dataset.funds[code]
        actual = float(values.get(code, 0))
        target = float(weights.get(code, 0))
        gap = target * total - actual
        deal = ledger.dealing_date(code, ledger.current_date)
        available = sum(
            lot.shares - lot.reserved
            for lot in ledger.lots
            if lot.fund_id == code
            and deal is not None
            and (deal - lot.bought).days >= fund.lock_days
        )
        quote = dataset.quote(code, ledger.current_date)
        if abs(gap) < result["config"]["min_trade"]:
            status = "差额小于调仓阈值"
        elif code in pending:
            status = "已有待确认指令，先核对在途份额与资金"
        elif quote is None or (ledger.current_date - quote.nav_date).days > fund.max_stale_days:
            status = "估值缺失或过期，暂缓"
        elif deal is None:
            status = "日历内无后续开放日"
        elif gap < 0 and available <= 1e-10:
            status = "份额锁定或已冻结"
        elif gap > 0 and not fund.eligible(ledger.current_date):
            status = "当前不可申购"
        else:
            status = "待人工复核现金、最低金额及实际产品条款"
        rows.append(
            {
                "fund_id": code,
                "target_date": target_date,
                "actual_weight": actual / total,
                "target_weight": target,
                "gap_amount": gap,
                "direction": "增配" if gap > 0 else "减配" if gap < 0 else "持平",
                "next_open_date": deal,
                "unlocked_shares_at_next_open": available,
                "status": status,
            }
        )
    return pd.DataFrame(rows)


def monitor(result, dataset):
    ledger = result["ledger"]
    holdings = ledger.holdings()
    snapshot = ledger.snapshot()
    date = ledger.current_date
    alerts, liquidity = [], []
    risk = pd.DataFrame()
    styles = []
    if holdings.empty:
        holdings = pd.DataFrame(columns=["fund_id", "manager", "value"])
        alerts.append({"severity": "info", "fund_id": "", "message": "当前没有已确认基金持仓"})
    holdings["weight"] = holdings.value / snapshot["total_value"]
    for item in holdings.itertuples():
        fund = dataset.funds[item.fund_id]
        if item.stale_days > fund.max_stale_days:
            alerts.append(
                {
                    "severity": "warning",
                    "fund_id": item.fund_id,
                    "message": f"估值已滞后{item.stale_days}天，暂停新增风险",
                }
            )
        if item.weight > result["config"]["max_weight"] + 1e-6:
            alerts.append(
                {
                    "severity": "warning",
                    "fund_id": item.fund_id,
                    "message": "实际持仓超过单基金目标上限，可能由价格漂移或锁定造成",
                }
            )
    concentration = holdings.groupby("manager", as_index=False).agg(
        value=("value", "sum"), weight=("weight", "sum")
    )
    for item in concentration.itertuples():
        if item.weight > 0.4:
            alerts.append(
                {
                    "severity": "warning",
                    "fund_id": "",
                    "message": f"{item.manager}占比超过40%监控线",
                }
            )
    for lot in ledger.lots:
        available = lot.shares - lot.reserved
        if available <= 1e-10:
            continue
        fund = dataset.funds[lot.fund_id]
        deal = ledger.dealing_date(
            lot.fund_id,
            date,
            not_before=lot.bought + pd.Timedelta(days=fund.lock_days),
        )
        due = ledger.offset(deal, fund.settle_lag, "banking") if deal is not None else None
        mark = holdings.set_index("fund_id").loc[lot.fund_id, "mark"]
        liquidity.append(
            {
                "fund_id": lot.fund_id,
                "kind": "indicative_redemption",
                "deal_date": deal,
                "arrival_date": due,
                "estimated_amount": available * mark,
                "basis": "按当前估值、未扣未来赎回费；不包含未提交指令的成交保证",
            }
        )
    for item in ledger.receivables:
        liquidity.append(
            {
                "fund_id": item["fund_id"],
                "kind": item["kind"],
                "deal_date": None,
                "arrival_date": item["due"],
                "estimated_amount": item["amount"],
                "basis": "已确认应收款",
            }
        )
    for order in ledger.orders:
        if order.status == "pending" and order.side == "SELL":
            mark = holdings.set_index("fund_id").loc[order.fund_id, "mark"]
            liquidity.append(
                {
                    "fund_id": order.fund_id,
                    "kind": "pending_redemption",
                    "deal_date": order.deal_date,
                    "arrival_date": order.settle_date,
                    "estimated_amount": order.shares * mark,
                    "basis": "待确认赎回，按当前估值未扣费；披露延迟可能推迟到账",
                }
            )
    panel = dataset.returns(date, result["config"]["frequency"], result["config"]["lookback"])
    periods = 12 if result["config"]["frequency"] == "ME" else 52
    try:
        if not holdings.empty:
            risk = risk_contributions(panel, holdings.set_index("fund_id").weight, periods)
    except ValueError as error:
        alerts.append({"severity": "warning", "fund_id": "", "message": str(error)})
    # Equal-weight public equity/bond peer baskets are explicit proxies, not official factors.
    factors = {}
    for group in ("equity", "bond"):
        codes = [
            c
            for c in panel
            if dataset.funds[c].strategy == group and dataset.funds[c].kind == "public"
        ]
        if codes:
            factors[group] = panel[codes].mean(axis=1, skipna=False)
    factor_frame = pd.DataFrame(factors)
    for code in panel:
        if dataset.funds[code].kind != "private" or len(factors) < 2:
            continue
        try:
            current = style_regression(panel[code], factor_frame, window=12)
            previous = style_regression(panel[code].iloc[:-3], factor_frame.iloc[:-3], window=12)
            drift = float(
                np.linalg.norm(
                    np.array(list(current["exposures"].values()))
                    - np.array(list(previous["exposures"].values()))
                )
            )
            styles.append(
                {
                    "fund_id": code,
                    "equity_beta": current["exposures"]["equity"],
                    "bond_beta": current["exposures"]["bond"],
                    "r_squared": current["r_squared"],
                    "drift_l2": drift,
                    "observations": current["observations"],
                }
            )
            if drift > 0.5:
                alerts.append(
                    {
                        "severity": "info",
                        "fund_id": code,
                        "message": "滚动代理因子暴露变化超过0.5，建议复核策略；不代表真实底仓变化",
                    }
                )
        except ValueError as error:
            alerts.append({"severity": "info", "fund_id": code, "message": str(error)})
    if not alerts:
        alerts.append(
            {"severity": "info", "fund_id": "", "message": "当前已实现监控规则未发现异常"}
        )
    return {
        "holdings": holdings,
        "concentration": concentration,
        "risk": risk,
        "liquidity": pd.DataFrame(liquidity),
        "alerts": pd.DataFrame(alerts),
        "styles": pd.DataFrame(styles),
        "rebalance_review": rebalance_review(result, dataset),
    }
