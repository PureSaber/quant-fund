"""Native-frequency, frozen fund research and non-mutating ledger previews."""

from copy import deepcopy
from dataclasses import replace

import numpy as np
import pandas as pd

from .data import day
from .research import allocate, latest_complete_interval, style_regression


def factor_asof(observations, at):
    required = {"period_end", "known_at", "factor", "value"}
    if not required.issubset(observations) or observations.empty:
        raise ValueError("factor return periods, known_at, names and values required")
    frame = observations.copy()
    for key in ("period_end", "known_at"):
        frame[key] = frame[key].map(day)
    if frame[["period_end", "known_at", "factor"]].isna().any().any():
        raise ValueError("missing factor identity or time")
    if (frame.known_at < frame.period_end).any():
        raise ValueError("factor return cannot be known before period end")
    if frame.duplicated(["period_end", "known_at", "factor"]).any():
        raise ValueError("duplicate factor versions")
    if not np.isfinite(frame.value.to_numpy(dtype=float)).all():
        raise ValueError("finite factor returns required")
    frame = frame.loc[frame.known_at <= day(at)].sort_values("known_at")
    frame = frame.drop_duplicates(["period_end", "factor"], keep="last")
    return frame.pivot(index="period_end", columns="factor", values="value").sort_index()


def style_oos(dataset, fund_id, factors, *, decision, evaluation, frequency="ME", window=24):
    """Freeze existing OLS at decision; expose every missing OOS fund/factor return."""
    decision, evaluation = day(decision), day(evaluation)
    if decision >= evaluation:
        raise ValueError("evaluation must follow decision")
    training = dataset.returns(decision, frequency, window)
    if fund_id not in training:
        raise ValueError("fund has no known training history")
    known_factors = factor_asof(factors, decision)
    joined = pd.concat([training[fund_id].rename("fund"), known_factors], axis=1).reindex(
        training.index
    )
    joined = latest_complete_interval(joined)
    if joined.empty:
        raise ValueError("no contiguous fund/factor training interval")
    model = style_regression(joined.fund, joined.drop(columns="fund"), window=window)
    index = pd.date_range(decision, evaluation, freq=frequency)
    index = index[index > decision]
    if not len(index):
        raise ValueError("no completed evaluation period")
    future = dataset.returns(evaluation, frequency, len(index) + 2).reindex(index)
    factor_test = factor_asof(factors, evaluation).reindex(
        index=index, columns=list(model["exposures"])
    )
    fund_test = future[fund_id] if fund_id in future else pd.Series(np.nan, index=index)
    prediction = factor_test.mul(pd.Series(model["exposures"])).sum(
        axis=1, min_count=factor_test.shape[1]
    )
    prediction += model["intercept"]
    result = pd.DataFrame({"fund_return": fund_test, "style_expected": prediction})
    result["residual"] = result.fund_return - result.style_expected
    result["complete"] = result.notna().all(axis=1)
    return result, {
        "decision": str(decision.date()),
        "evaluation": str(evaluation.date()),
        "frequency": frequency,
        "frozen_model": model,
        "training_start": str(joined.index[0].date()),
        "training_end": str(joined.index[-1].date()),
        "expected_periods": len(index),
        "complete_periods": int(result.complete.sum()),
        "evidence_kind": dataset.metadata.get("classification", "unclassified"),
        "limits": "Return exposures, not holdings. Evaluation uses versions known at evaluation. "
        "No interpolation, fee model or evidence of manager skill.",
    }


def persistence(dataset, *, decision, evaluation, frequency="ME", window=24, min_periods=12):
    """Compare frozen within-peer formation ranks with complete later-period ranks.

    Every training-eligible fund stays in the output, including later unavailable ones.
    Common training endpoints and kind/strategy define peer comparisons.
    """
    decision, evaluation = day(decision), day(evaluation)
    if decision >= evaluation or type(min_periods) is not int or min_periods < 3:
        raise ValueError("later evaluation and at least three training periods required")
    training = dataset.returns(decision, frequency, window)
    index = pd.date_range(decision, evaluation, freq=frequency)
    index = index[index > decision]
    if not len(index):
        raise ValueError("no completed evaluation periods")
    future = dataset.returns(evaluation, frequency, len(index) + 2).reindex(index)
    rows = []
    for code in training:
        series = latest_complete_interval(training[code])
        if len(series) < min_periods:
            continue
        fund = dataset.fund_at(code, decision, decision)
        later = future[code] if code in future else pd.Series(np.nan, index=index)
        complete = bool(later.notna().all())
        rows.append(
            {
                "fund_id": code,
                "kind": fund.kind,
                "strategy": fund.strategy,
                "share_group": fund.share_group or code,
                "training_start": series.index[0],
                "training_end": series.index[-1],
                "training_periods": len(series),
                "training_return": float((1 + series).prod() - 1),
                "test_return": float((1 + later).prod() - 1) if complete else np.nan,
                "test_complete": complete,
                "missing_test_periods": int(later.isna().sum()),
                "terms_version_id": fund.version_id,
            }
        )
    result = pd.DataFrame(rows)
    if result.empty:
        return result, {"status": "insufficient_training", "rank_correlation": None}
    # Deterministic class representative chosen without looking at later performance.
    result = result.sort_values("fund_id").drop_duplicates("share_group").reset_index(drop=True)
    keys = ["kind", "strategy", "training_start", "training_end"]
    result["peer_count"] = result.groupby(keys).fund_id.transform("count")
    result["training_rank"] = result.groupby(keys).training_return.rank(pct=True)
    result["peer_complete"] = result.groupby(keys).test_complete.transform("all")
    result["test_rank"] = result.groupby(keys).test_return.rank(pct=True)
    valid = result.peer_complete & result.peer_count.ge(2)
    result.loc[~valid, "test_rank"] = np.nan
    result.loc[result.peer_count.lt(2), "training_rank"] = np.nan
    x, y = result.loc[valid, "training_rank"], result.loc[valid, "test_rank"]
    correlation = float(x.corr(y)) if len(x) >= 3 and x.nunique() > 1 and y.nunique() > 1 else None
    return result, {
        "decision": str(decision.date()),
        "evaluation": str(evaluation.date()),
        "frequency": frequency,
        "cohort_size": len(result),
        "incomplete_funds": int((~result.test_complete).sum()),
        "rank_correlation": correlation,
        "evidence_kind": dataset.metadata.get("classification", "unclassified"),
        "limits": "Descriptive within-peer ranks; no survivor-only aggregate "
        "or persistence significance.",
    }


def screened_allocation(dataset, date, selected, strategy="equal", **kwargs):
    """Delegate portfolio construction to existing optimizers on an explicit frozen shortlist."""
    selected = tuple(selected)
    if not selected or len(set(selected)) != len(selected) or set(selected) - set(dataset.funds):
        raise ValueError("unique known selected fund IDs required")
    subset = replace(dataset, funds={code: dataset.funds[code] for code in sorted(selected)})
    return allocate(subset, date, strategy, **kwargs)


def preview_rebalance(ledger, targets):
    """Preview through a deep copy of the existing ledger; never mutate its account.

    Whole requested adjustments either pass existing submit checks or are reported
    rejected. No optimistic redemption proceeds, partial hidden allocation or new
    accounting implementation is introduced.
    """
    if ledger.current_date is None:
        raise ValueError("advance the existing ledger to the decision date first")
    weights = pd.Series(targets, dtype=float)
    if weights.index.duplicated().any() or set(weights.index) - set(ledger.dataset.funds):
        raise ValueError("unique known target funds required")
    if not np.isfinite(weights).all() or (weights < 0).any() or weights.sum() > 1 + 1e-10:
        raise ValueError("long-only targets with explicit residual cash required")
    clone = deepcopy(ledger)
    snapshot = clone.snapshot()
    holdings = clone.holdings()
    held = holdings.set_index("fund_id") if not holdings.empty else pd.DataFrame()
    pending = {order.fund_id for order in clone.orders if order.status == "pending"}
    proposals, results = [], []
    for code in sorted(set(weights.index) | (set(held.index) if not held.empty else set())):
        if code in pending:
            results.append(
                {"fund_id": code, "status": "blocked", "reason": "existing pending order"}
            )
            continue
        value = float(held.loc[code, "value"]) if code in held.index else 0.0
        difference = float(weights.get(code, 0)) * snapshot["total_value"] - value
        if abs(difference) <= 1e-8:
            continue
        if difference < 0:
            proposals.append((0, code, "SELL", {"shares": -difference / held.loc[code, "mark"]}))
        else:
            proposals.append((1, code, "BUY", {"amount": difference}))
    for _, code, side, arguments in sorted(proposals):
        try:
            order = clone.submit(code, side, **arguments)
            results.append(
                {
                    "fund_id": code,
                    "side": side,
                    **arguments,
                    "status": "preview_accepted",
                    "deal_date": str(order.deal_date.date()),
                    "terms_version_id": order.terms_version_id,
                }
            )
        except ValueError as exc:
            results.append(
                {
                    "fund_id": code,
                    "side": side,
                    **arguments,
                    "status": "blocked",
                    "reason": str(exc),
                }
            )
    clone.check()
    return pd.DataFrame(results), {
        "mode": "non_mutating_ledger_preview",
        "before": snapshot,
        "after": clone.snapshot(),
        "limits": "Accepted previews are pending requests, "
        "not confirmed fills or permission to trade.",
    }
