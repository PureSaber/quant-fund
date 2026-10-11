# Frozen fund selection research

`quant_fund.selection` consumes the existing `Dataset` and `Ledger` APIs. It does
not change NAV, historical terms, settlement or public output schemas.

* `factor_asof`: versioned factor *returns* with `period_end,known_at,factor,value`.
  Dates follow the existing daily disclosure precision. Return availability cannot
  precede its period end; duplicates fail; only versions known at the decision enter.
* `style_oos`: existing unconstrained OLS fitted on the last complete contiguous
  native-frequency fund/factor interval and frozen for later dates. Output includes
  observed return, predicted style return, residual and completeness. This is a
  return-exposure estimate, not actual holdings or evidence of skill.
* `persistence`: formation compound-return ranks within kind, strategy and identical
  history endpoints. Later returns never affect formation eligibility. Missing or
  later-ineligible funds stay visible; any incomplete peer suppresses that entire
  peer's later ranks. A/C share groups have one deterministic representative. Rank
  association is descriptive; no p-value, manager survival or skill inference.
* `screened_allocation`: explicit shortlist passed to existing allocation methods;
  caps, cash buffer and peer share-class checks remain in that implementation.
* `preview_rebalance`: deep-copies the caller's ledger, attempts requests through
  its existing `submit`, then checks invariants. Pending funds are skipped with a
  reason. Sales do not increase spendable cash. Oversized purchases, locked shares,
  notice windows and minimum subscriptions retain existing rejection reasons.
  The input ledger is unchanged and accepted previews are unconfirmed requests.

Use `ME` or `W-FRI` consistently for fund and factor periods. Factors must already
represent those exact native-period returns: this module does not resample them.
The existing Dataset determines complete NAV endpoints and known-at versions;
private monthly NAV is never interpolated. Test labels use the versions known at
the specified evaluation cutoff, not a claim of contemporaneous observation.

## Whole-period OOS boundaries (C-FUND-01)

Both `style_oos` and `persistence` use the same interval definition:
`(previous_native_endpoint, native_endpoint]`, where endpoints are the `ME` or
`W-FRI` labels of `Dataset.returns`. Only completed intervals whose **start is at
or after the decision** enter evaluation. A decision at an endpoint can start the
next full interval; a decision inside a month/week excludes that entire first
bucket. No return is clipped, prorated, interpolated or manufactured from the
decision-date NAV. Calendar endpoint labels retain Dataset's complete disclosed
NAV convention (for example, the last exchange session before a weekend month-end).
Factor returns must use the identical labeled native intervals.

For a January 16 freeze, January's December 31–January 31 return is excluded;
the first monthly OOS interval is January 31–February 29. For a January 31
freeze, that same February interval is included. A Wednesday freeze excludes the
week ending that Friday; a Friday freeze can start the next full Friday-to-Friday
interval. Thus a January 15 NAV jump cannot enter OOS returns frozen January 16.

Both summaries retain `excluded_intervals` with start/end and
`native_return_interval_crosses_decision`, `candidate_periods`, `excluded_periods`
and `expected_periods`. Candidates are completed endpoints after the decision;
`candidate_periods = excluded_periods + expected_periods`. Style rows expose
`period_start` beside the `period_end` index; `complete_periods + missing_periods`
equals the expected count. Persistence rows expose `test_start`, `test_end`,
`expected_test_periods`, `complete_test_periods` and `missing_test_periods`.
Excluded crossing periods are never counted as missing eligible OOS observations.
Missing NAV endpoints still invalidate adjacent full-period returns, and missing
factor periods invalidate their style comparisons. Neither is filled with zero.

When no full eligible interval exists, the summary is `no_complete_oos_periods`;
style returns an empty table and persistence retains the training cohort with
`test_return=NaN` and `test_complete=false`. Zero eligible periods do not become
a zero-return success. Run `python examples/verify_oos_boundaries.py NEW_OUTPUT`
for an independent raw-NAV control of the coordinator's predecision-jump case,
including both endpoint and within-period decisions at both supported frequencies.

Run `python examples/selection_study.py <new-output-directory>` for generated
synthetic input, frozen style/persistence tables and allocation/ledger previews.
Real fund conclusions still require authorized historical total-return NAV,
disclosure/terms versions, delisted/closed funds, factor release lineage and actual
dealing calendars. Missing data or a blocked preview is never a successful trade.
