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

Run `python examples/selection_study.py <new-output-directory>` for generated
synthetic input, frozen style/persistence tables and allocation/ledger previews.
Real fund conclusions still require authorized historical total-return NAV,
disclosure/terms versions, delisted/closed funds, factor release lineage and actual
dealing calendars. Missing data or a blocked preview is never a successful trade.
