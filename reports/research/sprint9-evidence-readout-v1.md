# Sprint 9.1 Evidence Readout v1

`claims_eligible=false`

- Evidence cut: `2026-07-10`
- Frozen contract: `multifactor-baseline-v1`
- Primary horizon: `126` trading sessions
- Frozen holdout: `2022-01-01` through `2025-06-30`
- Machine-readable companion: [`sprint9-evidence-readout-v1.json`](sprint9-evidence-readout-v1.json)

## Decision

> **Sprint 8 is an engineering reproducibility success, but not a complete
> engineering-gate success and not a model-performance success. The model must
> not be promoted.**

The two-rebuild closure passed: the system reproduced point-in-time
fundamentals, revisions, features, eligible-universe hashes, predictions,
outcomes, metrics, and canonical reports from the frozen bundle. That proves
that the research machinery can produce the same result twice.

It does not prove that the result is broad or investable. Only `60` eligible
stock-months appear in the multi-factor evidence across `43` months. All `60`
are labelled `Financials`, `34` of the `43` months contain only one eligible
security, and no month contains more than four. Quintile 1 is empty, so the
locked top-minus-bottom tests are undefined. The 25 bps top-basket net excess
return is `-6.4054%` and the benchmark hit rate is `39.53%`.

The reported mean Rank IC of `0.6889` is not a locked-holdout result. It can be
calculated in only nine small cross-sections, all before the holdout, and the
non-overlapping t-statistic is `null`. The four available holdout months each
contain one security, so holdout Rank IC is not calculable.

| Question | Verdict | Meaning |
| --- | --- | --- |
| Did the Sprint 8 engineering pipeline rebuild reproducibly? | **PASS** | Both clean rebuilds matched on every closure invariant. |
| Did Sprint 8 pass every frozen engineering promotion gate? | **FAIL** | Full-universe final-score coverage is far below 90%; exclusion completeness is not demonstrated. |
| Did Sprint 8 pass the model-performance promotion gates? | **FAIL** | The conjunctive gate set is not satisfied; five model gates cannot be evaluated and the engineering-gate dependency fails. |
| Is genuine signal efficacy established? | **NO — NOT ESTABLISHED** | The positive IC is based on nine pre-holdout, two-to-four-security months. |
| Is investability established? | **NO — NOT ESTABLISHED** | No bottom quintile exists and the top basket is negative after costs. |
| May investment or product-performance claims be made? | **NO** | The contract and every evidence artifact retain `claims_eligible=false`. |

## Evidence scope

The available multi-factor ledger covers `2018-10-31` through `2022-04-29`,
while the frozen holdout is `2022-01-01` through `2025-06-30`.

| Scope statistic | Recorded value |
| --- | ---: |
| Prediction months in multi-factor report | 43 |
| Eligible/evaluated stock-months at 126 sessions | 60 / 60 |
| Months with one eligible security | 34 |
| Months with two eligible securities | 3 |
| Months with three eligible securities | 4 |
| Months with four eligible securities | 2 |
| Holdout months represented | 4 |
| Holdout observations | 4 |
| Holdout months with calculable Rank IC | 0 |
| Sectors represented | 1 (`Financials`) |

Therefore, metrics aggregated over the whole report must be described as
mixed development/validation/partial-holdout evidence, not as full holdout
performance.

## Engineering readout

### What passed

The Sprint 8 closure decision is `pass`, the worktree was verified clean, and
both fresh SQLite rebuilds matched on all ten recorded invariants:

| Rebuild invariant | Result | Rebuild value where applicable |
| --- | --- | ---: |
| Fundamental fact hash | Match | `0632039d...4c71fc39` |
| Availability/revision hash | Match | `35cf370c...66f6066` |
| Feature count | Match | 961,400 |
| Feature value hash | Match | `06175f6b...336ccbc` |
| Monthly eligible-universe hash | Match | `6f4c1113...ed85bb` |
| Prediction count | Match | 41,264 |
| Outcome count | Match | 41,012 |
| Prediction/outcome hash | Match | `fbf13fa3...c4a62` |
| Backtest metrics hash | Match | `ac0aa35a...f6759` |
| Canonical report hashes | Match | All three reports matched |

The point-in-time fundamental audit records zero hard failures. The holdout
lock was frozen at `2026-07-09T14:43:36Z`; the evaluation was generated at
`2026-07-10T09:56:46Z` and carries the exact lock SHA-256
`3857baa255562a89862a39919b550004b8860d733e4990a361cd81473d23878f`.

### What did not pass

The frozen engineering coverage gate requires a final score for at least 90%
of expected non-benchmark members in every evaluated monthly cohort. The
multi-factor report's `coverage=1.0` does **not** measure that requirement. In
the evaluator it means that outcomes exist for all already-eligible linked
predictions.

Using the Sprint 7 point-in-time cohort counts as the expected-member
denominator for the same universe and exact 43 dates gives:

| Final-score coverage statistic | Value |
| --- | ---: |
| Expected point-in-time stock-months | 21,330 |
| Eligible multi-factor score rows | 60 |
| Aggregate final-score coverage | 0.2813% |
| Expected members per month | 492–498 |
| Eligible scores per month | 1–4 |
| Minimum monthly coverage | 0.2008% |
| Maximum monthly coverage | 0.8048% |
| Months at or above 90% | 0 / 43 |

This gate **fails**; it is not a near miss.

The evaluated rows do contain stable component-level reason codes:
`INSUFFICIENT_HISTORY` (`148` occurrences), `NOT_APPLICABLE` (`240`), and
`SOURCE_MISSING` (`36`). However, the Sprint 8 summary artifacts do not map
every expected member that failed to produce an eligible score to an explicit
reason. The all-exclusions reason-code gate therefore **cannot be evaluated**
from the published Sprint 8 evidence. Sprint 9.2 must produce that funnel.

The fundamentals audit also remains `review`, not `pass`: it contains `156`
review findings and its cross-sector reconciliation gate was not enforced
(`0` issuer-period samples versus a minimum of `30`). These are not recorded
as leakage failures, but they are unresolved evidence-quality limitations.

## Model-performance readout

### Primary 126-session metrics

| Metric | Sprint 8 multi-factor | Interpretation |
| --- | ---: | --- |
| Mean monthly Spearman Rank IC | 0.6889 | Above the numeric 0.03 threshold, but calculated from nine pre-holdout small-cohort months only. |
| Median monthly Rank IC | 0.5000 | Same narrow cohort limitation. |
| Months with calculable Rank IC | 9 / 43 | The other 34 months are single-security cross-sections. |
| Non-overlapping Rank IC periods | 2 | Insufficient for a t-statistic; reported t-statistic is `null`. |
| Quintile counts, 1 through 5 | 0 / 6 / 5 / 6 / 43 | Bottom quintile is empty. |
| Quintile monotonicity | `null` | Cannot be calculated. |
| Gross top-minus-bottom spread | `null` | Cannot be calculated. |
| Gross top-quintile excess return | -6.3705% | Negative relative to SPY. |
| Top-basket net excess after 25 bps | -6.4054% | Negative; this is not a top-minus-bottom spread. |
| Benchmark hit rate after 25 bps | 39.53% | Fewer than half of evaluated periods beat SPY. |
| Mean top-basket turnover | 13.95% | Based on one-name top baskets in every month. |
| Worst top-quintile maximum drawdown | -57.00% | No investability inference is supported. |
| Top-quintile downside capture | 134.62% | Losses exceeded the benchmark on average in down markets. |
| Sector coverage | Financials: 60 / 60 | No cross-sector stability evidence. |
| Delisted-security observations | 0 | Delisting robustness is not demonstrated by this sample. |

### Why the Rank IC does not establish model success

Rank IC exists only for the nine months with at least two observations:
October 2019 through April 2020, plus February and March 2021. Each of those
cross-sections contains only two, three, or four securities. None is in the
locked holdout. With so few ranks, correlations can take large discrete values
and are not evidence of broad S&P 500 ranking performance.

On the same 60 rows, the multi-factor mean Rank IC exceeds the Sprint 7
price-only mean by `0.1667` (`0.6889` versus `0.5222`). That comparison does
not cure the cohort problem. It also does not translate into better top-basket
economics: at 25 bps, multi-factor net excess is `-6.4054%` versus `-5.1044%`
for price-only, a deterioration of about `1.30` percentage points.

## Frozen gate-by-gate decision

Status vocabulary is strict: **PASS** means the recorded evidence meets the
frozen rule; **FAIL** means it does not; **NOT EVALUABLE** means a required
metric or denominator is absent and is never treated as a pass.

### Engineering gates

| ID | Frozen gate | Status | Evidence |
| --- | --- | --- | --- |
| E1 | Zero demonstrated look-ahead or revision leakage | **PASS** | No hard audit finding demonstrates leakage; point-in-time availability/revision hashes reproduce across both rebuilds. The audit's review findings remain data-quality caveats. |
| E2 | Two clean rebuilds with identical fundamentals, revisions, features, universe, predictions, outcomes, metrics, and reports | **PASS** | Closure decision `pass`; all ten invariants match. |
| E3 | At least 90% monthly final-score coverage of expected non-benchmark members in every cohort | **FAIL** | 60 / 21,330 aggregate stock-months (0.2813%); monthly range 0.2008%–0.8048%; 0 / 43 months pass. |
| E4 | Every exclusion and missing/inapplicable component has a stable reason code | **NOT EVALUABLE** | Component codes are present for evaluated rows, but excluded expected members are not enumerated in the Sprint 8 summary evidence. |
| E5 | Proof holdout outputs were produced only after the lock | **PASS** | Evaluation timestamp follows lock timestamp and the exact committed lock hash is embedded in both output reports. |

**Engineering promotion-gate verdict: FAIL.** E3 fails, so the conjunctive
engineering gate cannot pass even before resolving E4.

### Model promotion gates

| ID | Frozen 126-session holdout gate | Status | Evidence |
| --- | --- | --- | --- |
| M1 | Mean monthly Spearman Rank IC at least 0.03 | **NOT EVALUABLE** | The reported 0.6889 is mixed-period and comes entirely from pre-holdout months. All four represented holdout months are singletons with `null` Rank IC. |
| M2 | Top-minus-bottom quintile spread strictly positive after 25 bps one-way entry and exit costs | **NOT EVALUABLE** | Quintile 1 has zero observations; gross and net top-minus-bottom spreads are `null`. The separate top-only 25 bps net excess is negative. |
| M3 | Annual mean Rank IC positive in at least three of four holdout years | **NOT EVALUABLE** | No holdout year has a calculable annual Rank IC; 2023–2025 are absent. |
| M4 | No year contributes more than 50% of positive monthly top-minus-bottom spreads | **NOT EVALUABLE** | Monthly top-minus-bottom spreads do not exist. |
| M5 | No sector contributes more than 50% of positive aggregate sector-neutral top-minus-bottom spread | **NOT EVALUABLE** | Sector spreads do not exist and all 60 observations are Financials. |
| M6 | All engineering gates pass | **FAIL** | E3 fails and E4 is not evaluable. |

**Model promotion verdict: FAIL.** Promotion requires every gate to pass. The
result is not “five neutral gates and one failure”; an unevaluable required
gate is not a pass.

## Why `claims_eligible=false` remains correct

1. The frozen contract explicitly retains `claims_eligible=false` even if a
   passing holdout is eventually produced.
2. This evidence does not contain the full locked holdout and does not pass the
   conjunctive promotion rules.
3. The available sample has no bottom quintile, no cross-sector breadth, no
   calculable holdout Rank IC, no non-overlapping t-statistic, and negative
   top-basket net excess return.
4. The fundamental audit remains under review and has no enforced cross-sector
   reconciliation sample.
5. The claims policy does not permit alpha, outperformance, stock-picking,
   suitability, or investment-advice claims from this internal evidence.
   Public claims also require data-rights evidence and compliance review.

This report is an internal research decision record. It makes no statement
that the model predicts returns, beats SPY, is suitable for portfolio use, or
should drive any buy or sell decision.

## Required next step

Proceed to Sprint 9.2 before changing features or weights. The cohort audit
must reconcile the expected point-in-time universe to raw prices,
fundamentals, component coverage, final-score eligibility, prediction links,
mature outcomes, and the final 60 rows, with a stable reason code for every
drop-off.

## Evidence provenance

| Artifact | SHA-256 |
| --- | --- |
| [`docs/research/multifactor-baseline-v1.md`](../../docs/research/multifactor-baseline-v1.md) | `3a650dcf5d1837bd1a922b8b80b21210f63838e657b713b836eb30b37918f7c5` |
| [`experiments/multifactor-holdout-lock-v1.json`](../../experiments/multifactor-holdout-lock-v1.json) | `3857baa255562a89862a39919b550004b8860d733e4990a361cd81473d23878f` |
| [`reports/reproducibility/sprint8-closure-v1.json`](../reproducibility/sprint8-closure-v1.json) | `edce8123922a5157dc979ae9db34834a69fbe31b8a0aa1baf8ae132088fc064c` |
| [`reports/backtests/pit_multifactor_baseline_v1.json`](../backtests/pit_multifactor_baseline_v1.json) | `5e1cca6e568599ee1f3badcc6bf051edc1155b15bfb0b408b17051c2b5f60612` |
| [`reports/comparisons/price-vs-multifactor-v1.json`](../comparisons/price-vs-multifactor-v1.json) | `2963e3d618df41f4ef55b8c86e63ba505c56a117c8dba7ba2f4d757ffd25b80a` |
| [`reports/backtests/pit_baseline_v0_1.json`](../backtests/pit_baseline_v0_1.json) | `cb98d3b0ec01df61375b10f05ad0759d5bc0d8f0afb83624e8274b8fd5dfb013` |
| [`reports/data-audits/pit-fundamentals-v1.json`](../data-audits/pit-fundamentals-v1.json) | `97a99d0ea830abf26aa50d2dd86f093f2d7ce6ac194d1194bbd4e62337eebbc9` |
| [`docs/compliance/claims-policy.md`](../../docs/compliance/claims-policy.md) | `db7fff2525ca6a92eef7b835deb3bad6696f0defa393fe32bb563435d94341c1` |
