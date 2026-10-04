"""Native-frequency research and explicit third-party optimizer adapters."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
from sklearn.covariance import LedoitWolf

from .data import Dataset, day

STRATEGIES = {
    "equal": "等权基线",
    "balanced": "股债60/40基线",
    "optimal_risk": "风险预算·OptimalPortfolios",
    "skfolio_risk": "风险平价·skfolio",
    "hrp": "层次风险平价·skfolio",
}


def latest_complete_interval(frame):
    """Last contiguous block with all observations; never join across missing periods."""
    valid = frame.notna() if isinstance(frame, pd.Series) else frame.notna().all(axis=1)
    positions = np.flatnonzero(valid.to_numpy())
    if not len(positions):
        return frame.iloc[:0]
    end = positions[-1] + 1
    gaps = np.flatnonzero(~valid.iloc[:end].to_numpy())
    start = gaps[-1] + 1 if len(gaps) else 0
    return frame.iloc[start:end]


def performance(returns: pd.Series, periods=12, risk_free=0.0) -> dict:
    r = returns.dropna().astype(float)
    if len(r) < 2:
        return {
            "observations": len(r),
            "annual_return": None,
            "volatility": None,
            "sharpe": None,
            "max_drawdown": None,
            "total_return": None,
        }
    if not np.isfinite(r).all() or (r <= -1).any():
        raise ValueError("收益率包含非法值")
    wealth = np.r_[1.0, (1 + r).cumprod().to_numpy()]
    vol = float(r.std(ddof=1) * np.sqrt(periods))
    excess = r.mean() * periods - risk_free
    return {
        "observations": len(r),
        "annual_return": float(wealth[-1] ** (periods / len(r)) - 1),
        "volatility": vol,
        "sharpe": float(excess / vol) if vol > 1e-12 else None,
        "max_drawdown": float(np.min(wealth / np.maximum.accumulate(wealth) - 1)),
        "total_return": float(wealth[-1] - 1),
    }


def research_table(dataset: Dataset, date, frequency="ME", window=36):
    date = day(date)
    panel = dataset.returns(date, frequency, window)
    rows = []
    for code, identity in dataset.funds.items():
        if day(identity.known_at) > date:
            continue
        fund = dataset.fund_at(code, date, date)
        quote = dataset.quote(code, date)
        values = latest_complete_interval(panel[code]) if code in panel else pd.Series(dtype=float)
        stats = performance(values, 12 if frequency == "ME" else 52)
        historical_versions = sorted(
            {
                dataset.fund_at(code, endpoint, date).version_id
                for endpoint in panel.index
                if endpoint <= date
            }
        )
        rows.append(
            {
                "fund_id": code,
                "name": fund.name,
                "manager": fund.manager,
                "strategy": fund.strategy,
                "kind": fund.kind,
                "nav_date": str(quote.nav_date.date()) if quote is not None else None,
                "stale_days": (day(date) - quote.nav_date).days if quote is not None else None,
                "eligible": fund.eligible(date),
                "terms_version_id": fund.version_id,
                "terms_known_at": fund.terms_known_at,
                "historical_terms_pit": fund.historical_pit,
                "classification_basis": "decision_date",
                "history_terms_version_ids": json.dumps(historical_versions, ensure_ascii=False),
                "history_start": str(values.index[0].date()) if len(values) else None,
                "history_end": str(values.index[-1].date()) if len(values) else None,
                **stats,
            }
        )
    result = pd.DataFrame(rows)
    if not result.empty:
        # Different sample windows and fund types are not comparable ranking universes.
        peers = ["kind", "strategy", "history_start", "history_end"]
        result["peer_percentile"] = result.groupby(peers).annual_return.rank(pct=True)
        result["peer_count"] = result.groupby(peers).annual_return.transform("count").fillna(0)
        result.loc[result.peer_count < 2, "peer_percentile"] = np.nan
    return result, panel


def covariance(returns: pd.DataFrame, periods=12) -> pd.DataFrame:
    if len(returns) < 3 or returns.empty or returns.isna().any().any():
        raise ValueError("协方差估计至少需要3个完整共同观测")
    matrix = LedoitWolf().fit(returns.to_numpy()).covariance_ * periods
    return pd.DataFrame(matrix, index=returns.columns, columns=returns.columns)


def allocate(
    dataset,
    date,
    strategy,
    *,
    frequency="ME",
    window=24,
    min_periods=12,
    max_weight=0.4,
    cash_buffer=0.05,
):
    if strategy not in STRATEGIES:
        raise ValueError(f"未知配置方法：{strategy}")
    if not 0 < max_weight <= 1 or not 0 <= cash_buffer < 1:
        raise ValueError("权重上限或现金比例无效")
    panel = dataset.returns(date, frequency, window)
    columns = [c for c in panel if panel[c].count() >= min_periods]
    # Avoid treating A/C share classes as independent diversification.
    selected, seen = [], set()
    for code in sorted(columns):
        fund = dataset.fund_at(code, date, date)
        group = fund.share_group or code
        if group not in seen:
            selected.append(code)
            seen.add(group)
    panel = panel[selected].dropna()
    if len(panel) < min_periods or len(panel.columns) < 2:
        raise ValueError(f"共同历史不足：{len(panel)}期、{len(panel.columns)}只基金")
    if (panel.std() <= 1e-10).any():
        raise ValueError("存在零波动基金，请核对净值或明确从研究池剔除")
    n = len(panel.columns)
    budget = 1 - cash_buffer
    # max_weight is an absolute portfolio limit, optimizer weights sum to one before cash.
    relative_cap = min(1.0, max_weight / budget)
    if n * max_weight < budget - 1e-10:
        raise ValueError("基金数量与单基金上限不能满足投资预算")
    periods = 12 if frequency == "ME" else 52
    cov = covariance(panel, periods)
    if strategy == "equal":
        weights = pd.Series(1 / n, index=panel.columns)
    elif strategy == "balanced":
        groups = {
            g: [c for c in panel if dataset.fund_at(c, date, date).strategy == g]
            for g in ("equity", "bond")
        }
        if not all(groups.values()):
            raise ValueError("60/40基线需要股票和债券两类基金")
        weights = pd.Series(0.0, index=panel.columns)
        for group, fraction in (("equity", 0.6), ("bond", 0.4)):
            weights.loc[groups[group]] = fraction / len(groups[group])
    elif strategy == "optimal_risk":
        from optimalportfolios.optimization.constraints import Constraints
        from optimalportfolios.optimization.risk_allocation.risk_budgeting import (
            wrapper_risk_budgeting,
        )

        constraints = Constraints(
            min_weights=pd.Series(0.0, index=panel.columns),
            max_weights=pd.Series(relative_cap, index=panel.columns),
        )
        weights = wrapper_risk_budgeting(pd_covar=cov, constraints=constraints)
    else:
        from skfolio.cluster import HierarchicalClustering
        from skfolio.optimization import HierarchicalRiskParity, RiskBudgeting

        if strategy == "hrp":
            # HRP bisects the full linkage tree; it does not use flat cluster labels.
            # The default gap-statistic selection needs at least three assets.
            model = HierarchicalRiskParity(
                max_weights=relative_cap,
                hierarchical_clustering_estimator=HierarchicalClustering(max_clusters=n),
            )
        else:
            model = RiskBudgeting(max_weights=relative_cap)
        model.fit(panel)
        weights = pd.Series(model.weights_, index=panel.columns)
    weights = weights.reindex(panel.columns) * budget
    if (
        not np.isfinite(weights).all()
        or (weights < -1e-8).any()
        or (weights > max_weight + 1e-6).any()
        or abs(weights.sum() - budget) > 1e-6
    ):
        raise ValueError("配置结果未通过预算、非负性或单基金上限校验；停止本次调仓")
    term_versions = {code: dataset.fund_at(code, date, date).version_id for code in panel.columns}
    term_evidence = [
        dataset.term_evidence(code, date, date, use="allocation_classification")
        for code in panel.columns
    ]
    term_evidence.extend(
        dataset.term_evidence(code, endpoint, date, use="research_return")
        for code in panel.columns
        for endpoint in panel.index
    )
    return weights.clip(lower=0), {
        "periods": len(panel),
        "history_end": str(panel.index[-1].date()),
        "funds": list(panel.columns),
        "method": strategy,
        "term_versions": term_versions,
        "classification_basis": "decision_date",
        "_term_evidence": term_evidence,
    }


def risk_contributions(returns, weights, periods=12):
    from qis.portfolio.risk.contributions import compute_portfolio_risk_contributions

    held = weights[weights > 1e-10]
    missing = set(held.index) - set(returns.columns)
    if missing:
        raise ValueError(f"持仓风险数据缺失：{sorted(missing)}")
    panel = returns[held.index].dropna()
    cov = covariance(panel, periods)
    rc = compute_portfolio_risk_contributions(w=held, covar=cov)
    total = float(np.sqrt(held @ cov @ held))
    if not np.isclose(rc.sum(), total, atol=1e-10):
        raise ValueError("qis风险贡献无法与总波动对账")
    return pd.DataFrame(
        {"weight": held, "vol_contribution": rc, "risk_share": rc / total if total > 0 else 0}
    )


def style_regression(fund_returns, factor_returns, window=12):
    """Unconstrained OLS exposure estimates; not claims about actual holdings."""
    frame = pd.concat([fund_returns.rename("fund"), factor_returns], axis=1).dropna().tail(window)
    if len(frame) < max(8, 3 * (factor_returns.shape[1] + 1)):
        raise ValueError("风格回归共同样本不足")
    x = np.column_stack([np.ones(len(frame)), frame.drop(columns="fund").to_numpy()])
    if np.linalg.matrix_rank(x) < x.shape[1]:
        raise ValueError("因子共线，不能识别风格暴露")
    y = frame.fund.to_numpy()
    coef, *_ = np.linalg.lstsq(x, y, rcond=None)
    residual = y - x @ coef
    denominator = ((y - y.mean()) ** 2).sum()
    return {
        "intercept": float(coef[0]),
        "exposures": dict(zip(factor_returns.columns, coef[1:].tolist(), strict=True)),
        "r_squared": float(1 - (residual**2).sum() / denominator) if denominator > 0 else None,
        "observations": len(frame),
    }
