"""Regression controls for C-FUND-01: whole native intervals must follow freezing."""

import numpy as np
import pandas as pd
import pytest

from quant_fund.selection import persistence, style_oos


def inputs(make_dataset, frequency):
    calendar = pd.bdate_range("2022-01-03", "2024-07-31")
    nav = np.where(calendar >= "2024-01-15", 2.0, 1.0)
    dataset = make_dataset(
        calendar=calendar, prices=nav, inception="2021-01-01", known_at="2021-01-01"
    )
    endpoints = pd.date_range(calendar[0], calendar[-1], freq=frequency)
    factors = pd.DataFrame(
        {
            "period_end": endpoints,
            "known_at": endpoints,
            "factor": "market",
            "value": np.sin(np.arange(len(endpoints))) * 0.02,
        }
    )
    return dataset, factors


@pytest.mark.parametrize(
    "frequency,decision,evaluation,first_start,first_end,excluded_end",
    [
        ("ME", "2024-01-16", "2024-07-31", "2024-01-31", "2024-02-29", "2024-01-31"),
        ("ME", "2024-01-31", "2024-07-31", "2024-01-31", "2024-02-29", None),
        ("W-FRI", "2024-01-17", "2024-03-01", "2024-01-19", "2024-01-26", "2024-01-19"),
        ("W-FRI", "2024-01-19", "2024-03-01", "2024-01-19", "2024-01-26", None),
    ],
)
def test_predecision_jump_never_enters_oos(
    make_dataset, frequency, decision, evaluation, first_start, first_end, excluded_end
):
    dataset, factors = inputs(make_dataset, frequency)
    path, style = style_oos(
        dataset, "F", factors, decision=decision, evaluation=evaluation, frequency=frequency
    )
    cohort, ranks = persistence(
        dataset, decision=decision, evaluation=evaluation, frequency=frequency
    )
    # Independent investor-period check: NAV is identically 2 after the decision.
    raw = dataset.nav.loc[dataset.nav.nav_date >= decision, "total_return_nav"]
    expected = raw.iloc[-1] / raw.iloc[0] - 1
    assert expected == 0
    assert (path.fund_return == expected).all()
    assert cohort.test_return.iloc[0] == expected
    assert path.index[0] == pd.Timestamp(first_end)
    assert path.period_start.iloc[0] == pd.Timestamp(first_start)
    assert (path.period_start >= pd.Timestamp(decision)).all()
    assert cohort.test_start.iloc[0] == pd.Timestamp(first_start)
    assert cohort.test_end.iloc[0] == pd.Timestamp(evaluation)
    assert len(path) == style["expected_periods"] == ranks["expected_periods"] == 6
    assert style["complete_periods"] == cohort.complete_test_periods.iloc[0] == 6
    assert style["missing_periods"] == cohort.missing_test_periods.iloc[0] == 0
    for summary in (style, ranks):
        assert summary["candidate_periods"] == 6 + bool(excluded_end)
        assert summary["excluded_periods"] == bool(excluded_end)
        if excluded_end:
            assert summary["excluded_intervals"][0]["period_end"] == excluded_end
            assert summary["excluded_intervals"][0]["reason"] == (
                "native_return_interval_crosses_decision"
            )
        else:
            assert summary["excluded_intervals"] == []


@pytest.mark.parametrize(
    "frequency,decision,evaluation,missing_nav,missing_factor",
    [
        ("ME", "2024-01-16", "2024-07-31", "2024-03-29", "2024-05-31"),
        ("W-FRI", "2024-01-17", "2024-03-01", "2024-02-02", "2024-02-16"),
    ],
)
def test_missing_native_endpoints_are_separate_from_excluded_intervals(
    make_dataset, frequency, decision, evaluation, missing_nav, missing_factor
):
    dataset, factors = inputs(make_dataset, frequency)
    dataset.nav = dataset.nav.loc[dataset.nav.nav_date != pd.Timestamp(missing_nav)]
    factors = factors.loc[factors.period_end != pd.Timestamp(missing_factor)]
    path, style = style_oos(
        dataset, "F", factors, decision=decision, evaluation=evaluation, frequency=frequency
    )
    cohort, ranks = persistence(
        dataset, decision=decision, evaluation=evaluation, frequency=frequency
    )
    # A missing native NAV boundary invalidates both adjacent returns; a different
    # missing factor invalidates one additional style comparison. None is zero-filled.
    assert len(path) == style["expected_periods"] == ranks["expected_periods"] == 6
    assert path.fund_return.isna().sum() == cohort.missing_test_periods.iloc[0] == 2
    assert cohort.complete_test_periods.iloc[0] == 4
    assert style["missing_periods"] == 3 and style["complete_periods"] == 3
    assert style["excluded_periods"] == ranks["excluded_periods"] == 1
    assert not cohort.test_complete.iloc[0] and pd.isna(cohort.test_return.iloc[0])


@pytest.mark.parametrize(
    "frequency,decision,evaluation",
    [("ME", "2024-01-16", "2024-01-31"), ("W-FRI", "2024-01-17", "2024-01-19")],
)
def test_only_crossing_interval_is_unavailable_not_zero_performance(
    make_dataset, frequency, decision, evaluation
):
    dataset, factors = inputs(make_dataset, frequency)
    path, style = style_oos(
        dataset, "F", factors, decision=decision, evaluation=evaluation, frequency=frequency
    )
    cohort, ranks = persistence(
        dataset, decision=decision, evaluation=evaluation, frequency=frequency
    )
    assert path.empty and not cohort.empty
    for summary in (style, ranks):
        assert summary["status"] == "no_complete_oos_periods"
        assert summary["candidate_periods"] == summary["excluded_periods"] == 1
        assert summary["expected_periods"] == 0
    assert style["complete_periods"] == style["missing_periods"] == 0
    assert cohort.expected_test_periods.iloc[0] == cohort.missing_test_periods.iloc[0] == 0
    assert pd.isna(cohort.test_return.iloc[0]) and not cohort.test_complete.iloc[0]


@pytest.mark.parametrize("frequency", ["ME", "W-FRI"])
def test_boundary_exclusion_is_stable_when_evaluation_extends(make_dataset, frequency):
    dataset, factors = inputs(make_dataset, frequency)
    decision = "2024-01-16"
    short, before = style_oos(
        dataset, "F", factors, decision=decision, evaluation="2024-03-31", frequency=frequency
    )
    long, after = style_oos(
        dataset, "F", factors, decision=decision, evaluation="2024-07-31", frequency=frequency
    )
    pd.testing.assert_frame_equal(short, long.loc[short.index])
    assert before["frozen_model"] == after["frozen_model"]
    assert before["excluded_intervals"] == after["excluded_intervals"]
