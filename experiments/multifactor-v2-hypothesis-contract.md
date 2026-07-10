# Multi-Factor V2 Hypothesis Contract

`claims_eligible=false`

```yaml
contract_version: multifactor-v2-hypothesis-contract-v1
status: hypothesis_locked_implementation_not_outcome_authorized
model_version: multifactor-v2-branch-aware-equal-weight-v1
feature_version: multifactor-v2-branch-aware-v1
classification_version: sec-sic-financial-subtype-v2
normalization_version: multifactor-v2-branch-normalization-v1
universe: sp500-pit-v1 historical monthly membership
benchmark: SPY
frequency: monthly
primary_horizon_sessions: 126
family_weights:
  value: 0.20
  quality: 0.20
  growth: 0.20
  momentum: 0.20
  risk: 0.20
family_renormalization: prohibited
historical_evidence_status: exposed_diagnostic_only
forward_shadow: 2026-07-31/2028-06-30
primary_evaluation: after_all_24_months_have_mature_126_session_outcomes
```

- Design lock: [`multifactor-v2-hypothesis-lock-v1.json`](multifactor-v2-hypothesis-lock-v1.json)
- Sector contract: [`sector-specific-factor-treatment-v1.md`](../docs/research/sector-specific-factor-treatment-v1.md)
- Sprint 8 remains immutable.

## Decision

> **Proceed conditionally with Model V2 as a coverage, classification, and cohort-
> integrity experiment. Do not tune it to improve the Sprint 8 return result. Do not
> evaluate it on outcomes until an executable code/data lock is committed.**

Model V2 is justified because Sprint 8 cannot test its stated hypothesis, not because
Sprint 8 demonstrated alpha. The first question is whether a point-in-time,
branch-aware five-family model can produce broad and comparable cohorts. Only after
that engineering hypothesis passes may the new forward ledger answer whether ranking
or portfolio value exists.

This document locks the hypothesis, allowed change envelope, fixed weights,
eligibility policy, evaluation protocol, promotion gates, and anti-overfitting rules.
It is not yet an executable holdout lock. The exact feature formulas, code commit,
source snapshots, classification ledger, prediction schedule, and portfolio notional
must be hash-bound in a second lock before the first shadow prediction.

## What Sprint 8 taught us

| Evidence | Sprint 8 / Sprint 9 result | Contract consequence |
| --- | --- | --- |
| Engineering reproducibility | Two clean rebuilds matched. | Retain immutable predictions, point-in-time lineage, and two-rebuild closure. |
| Full-universe score coverage | 60 / 50,600 stock-months; 0 / 102 months reached 90%. | Coverage repair is the primary Model V2 hypothesis. The 90% gate is not relaxed. |
| Evaluated cohort | 60 observations, 43 months, five names. | Minimum breadth and branch-size gates are mandatory before performance is read. |
| Sector/subtype evidence | 51 REIT observations under SIC `6798`; nine P&C insurer observations under SIC `6331`. | Replace broad Financials masking with point-in-time subtype branches. |
| Family availability | Quality appeared in 0 / 60 evaluated scores; growth was available in 0.49% of universe stock-months. | All five families are mandatory and family-weight renormalization is prohibited. |
| Rank IC | 0.6889, calculable in nine tiny pre-holdout months only. | The value is diagnostic and creates no efficacy prior. New forward evidence is required. |
| Portfolio result | Gross excess -6.37%; 25 bps net excess -6.41%; 39.53% hit rate. | Positive net portfolio value and an implementable capital curve become promotion gates. |
| Equal-weight comparison | Model selection trailed eligible equal weight by 0.42 percentage points overall and 2.03 points in multi-name months. | Model V2 must add value versus the same eligible cohort, not only versus SPY. |
| Concentration | One name and one sector per selected basket; no bottom quintile. | Minimum branch, basket, sector, and name breadth are locked. |
| Liquidity | Every selected holding exceeded $25m trailing median daily dollar volume. | Liquidity was not the observed failure, but executable order-size checks remain required. |

No Sprint 8 family-ablation result is used to set V2 weights. Growth's apparent
ablation contribution, momentum's standalone IC, and value's negative marginal
result are all based on the same narrow exposed sample and are not tuning inputs.

## Primary hypothesis

### Engineering hypothesis H1

After correcting point-in-time subtype classification, using branch-specific
accounting features, requiring all five families, and expanding fundamental history,
Model V2 will produce an eligible final score for at least 90% of expected
non-benchmark S&P 500 members in every forward monthly cohort, with explicit reasons
for every exclusion and no cross-branch normalization fallback.

### Efficacy hypothesis H2

Conditional on H1 passing, fixed equal-weight value, quality, growth, momentum, and
risk families will rank branch-relative 126-session returns with mean monthly
branch-neutral Spearman Rank IC of at least 0.03 and HAC t-statistic of at least 2.0
in the untouched 24-month forward shadow window.

### Portfolio hypothesis H3

Conditional on H1 and H2 passing, a pre-specified long-only top-bucket portfolio will
add positive net 126-session value after 25 bps relative to both SPY and the same
month's eligible equal-weight cohort, without reproducing Sprint 8's single-name or
single-sector concentration.

These hypotheses are conjunctive. H2 or H3 cannot rescue failed breadth, leakage,
classification, or reproducibility gates.

## Model V2 definition

### Frozen common design

- Point-in-time universe: `sp500-pit-v1`; SPY is excluded from rankings.
- Monthly prediction on the last regular SPY session; entry is the next regular
  session.
- Outcomes: 21, 63, 126, and 252 trading sessions; 126 is primary.
- Five families: value, quality, growth, momentum, risk.
- Family weights: exactly 20% each.
- No unavailable-family weight redistribution.
- Existing price features, directions, 2.5%/97.5% winsorization, z-score clipping
  to `[-3, 3]`, and point-in-time price rules remain unchanged unless an implementation
  defect is proven before the executable lock.
- Every feature retains source IDs, source hashes, availability timestamp, formula
  version, branch, applicability, and reason code.
- No imputation, zero filling, winsorized filling, sector median, or learned missing-
  value model.

### Branch-aware accounting design

The branch structure and candidate feature envelope are governed by
`sector-specific-factor-treatment-v1`:

- `INDUSTRIAL_GENERAL`
- `BANK`
- `INSURER_P_AND_C`
- `INSURER_LIFE_HEALTH`
- `BROKER_DEALER`
- `ASSET_MANAGER`
- `EQUITY_REIT`
- `MORTGAGE_REIT`

An unresolved Financial or REIT subtype is research-only and receives no final score.
SIC `6798` routes to unresolved REIT before any broad Financials rule.

Branch-specific feature selection may use only the candidate concepts in the sector
contract. Exact formulas may be chosen only from accounting validity, source
availability, point-in-time reconciliation, and coverage evidence. Forward-return
correlation, Rank IC, quintile return, or ablation results may not be consulted.

### Normalization and combined ranking

1. Each security-month belongs to exactly one branch.
2. Accounting features are winsorized and standardized only inside that branch.
3. A branch requires at least 20 valid securities for every component used in a
   monthly normalized score.
4. A small branch receives `BRANCH_NORMALIZATION_COHORT_TOO_SMALL`; it never falls
   back to the industrial or full universe.
5. Family z-scores are equal-weight means of the branch's locked valid components.
6. All five families must be available. Each retains its fixed 20% weight.
7. The final score is an average-tie percentile inside the branch.
8. For full-universe evaluation, each branch's score is already uniformly ranked.
   Top and bottom buckets are formed within branch, then unioned across branches.
9. Primary Rank IC is branch neutral: forward returns are demeaned by branch and date
   before the pooled monthly Spearman calculation. Raw all-universe and per-branch
   Rank ICs are secondary diagnostics.

### Missingness and eligibility

A security-month is eligible only when all conditions hold:

- classification and subtype are known at prediction time;
- branch cross-section has at least 20 valid securities;
- all five families are available;
- at least 80% of required branch components are valid;
- each family has at least 60% of its required branch components valid, rounded up;
- no required feature is marked `REPLACE_WITH_BRANCH_FEATURE`, unresolved, or
  research-only;
- all exclusions and component states have stable reason codes.

`NOT_APPLICABLE` cannot shrink a branch's required schema. Industrial components are
outside the specialized branch schema; missing required branch replacements remain
missing and can make the row ineligible.

## What may change

Only these changes are permitted before the executable lock:

1. Append-only point-in-time classification and explicit subtype routing under the
   Sprint 9.6 contract.
2. Point-in-time acquisition and reconciliation of branch-specific accounting inputs.
3. Exact formulas for candidate branch features, selected without any return data.
4. Feature-history construction needed to remove `INSUFFICIENT_HISTORY` when supported
   by genuinely available prior filings.
5. Eligibility logic required by the all-five-family, 80% component-coverage policy.
6. Branch-only normalization and branch-neutral evaluation mechanics.
7. Immutable shadow-ledger and six-sleeve portfolio implementation.
8. Corrections to demonstrated implementation bugs, provided they occur before any
   V2 outcome access and are recorded in the implementation lock.

Each allowed change must have a data or accounting rationale, tests, source lineage,
and a pre-outcome decision record.

## What may not change

- The Sprint 8 warehouse, predictions, outcomes, reports, or lock.
- The point-in-time universe, benchmark, monthly frequency, or primary 126-session
  horizon.
- Equal 20% family weights or component directions after the executable lock.
- Features, formulas, winsor limits, clipping, eligibility, branch routing, costs,
  portfolio construction, or gates after any V2 return is accessed.
- Feature selection based on 2017–2025 returns, Sprint 8 ablations, or forward shadow
  outcomes.
- Sector-specific sign flips or discretionary overrides.
- Cross-branch accounting normalization or universe fallback for a small branch.
- Imputation, survivorship replacement, dropping delisted observations, or replacing
  failed names with current constituents.
- Declaring 2022–2025 an untouched holdout.
- Lowering a threshold after failure or treating `NOT EVALUABLE` as pass.
- Publishing investment or product-performance claims; `claims_eligible=false`
  remains fixed even after promotion.

## Data and evaluation windows

| Window | Dates | Permitted use |
| --- | --- | --- |
| Exposed historical engineering window | 2017-01-01–2025-06-30 | Classification, lineage, accounting reconciliation, missingness, coverage, and code tests. No return-driven feature or threshold decisions. |
| Locked retrospective diagnostic | Same dates, exactly once after executable lock | Quarantined sanity report only. It may stop the experiment for safety but cannot promote V2 or trigger tuning. |
| Untouched forward shadow | 2026-07-31–2028-06-30 | Twenty-four immutable monthly prediction cohorts. No aggregate performance readout until the primary evaluation condition is met. |
| Primary evaluation | After every forward cohort has a mature 126-session outcome | One locked evaluation of H1–H3. |
| Full horizon closure | After every forward cohort has a mature 252-session outcome | Secondary-horizon closure and reproducibility report. |

The 2022–2025 period has been inspected repeatedly in Sprint 8 and Sprint 9. It is
permanently exposed. A historical V2 backtest over that period is useful only as a
quarantined diagnostic and can never satisfy a forward promotion gate.

### Walk-forward protocol

- Predictions are created one month at a time from information available by that
  prediction timestamp.
- Source snapshots are append-only. Later restatements never rewrite a prior feature.
- No model refit occurs: formulas, weights, transformations, and thresholds are fixed.
- Cross-sectional normalization uses only the current prediction cohort.
- Early 21- or 63-session outcomes may be stored when mature but aggregate results are
  blinded and may not be used to change the model.
- Primary evaluation begins only after all 24 scheduled cohorts have mature 126-
  session outcomes and every predeclared completeness condition is met.
- Failure is retained. A new hypothesis requires Model V3 and a new future window.

## Portfolio protocol

The Sprint 9.4 arithmetic average of overlapping forward cohorts is not a deployable
capital curve. Model V2 must build one.

- At each month-end, form top and bottom quintiles independently inside every active
  branch and union them across branches.
- The long-only selected basket is equal weight across all unioned top-bucket names.
- The eligible comparator is equal weight across all eligible names on the same date.
- The long-short diagnostic is equal-weight top minus equal-weight bottom.
- Capital is divided into six equal sleeves. One sleeve enters each monthly basket and
  is held for exactly 126 trading sessions. Active sleeves are marked daily; unallocated
  startup sleeves remain cash.
- Turnover is weight-based one-way turnover inside the sleeve being replaced.
- Report gross and 10, 25, and 50 bps one-way costs, plus bid-ask and market-impact
  estimates fixed in the executable lock.
- The executable lock must declare portfolio notional. Every modeled order must be no
  more than 1% of trailing 20-session median daily dollar volume.
- Report a single stitched daily equity curve, max drawdown, downside capture, sector
  weight, name weight, liquidity, and delisting contribution.

No annualized performance may be calculated from overlapping cohort averages.

## Engineering gates

Every gate must pass on the forward shadow ledger:

| ID | Locked threshold |
| --- | --- |
| E1 | Zero demonstrated look-ahead, revision, membership, classification, or outcome leakage. |
| E2 | Two clean rebuilds match on classification, facts, features, eligibility, predictions, outcomes, metrics, and reports. |
| E3 | 100% of expected security-months have a final disposition and stable reason codes. |
| E4 | At least 98% of expected members have a known point-in-time branch/subtype every month. |
| E5 | Final-score coverage is at least 90% of all expected non-benchmark members in every month. |
| E6 | Final-score coverage is at least 80% inside every active branch in every month. |
| E7 | Every active branch has at least 20 eligible names; at least five branches and five GICS sectors are represented monthly. |
| E8 | No cross-branch accounting normalization or industrial-universe fallback occurs. |
| E9 | All predictions are immutable and timestamped before any horizon outcome is available. |
| E10 | Every selected holding has a complete trailing 20-session liquidity record and passes the executable-lock order-size rule. |

There is no warm-up exception inside the forward window. Historical data needed for a
feature must already be present at the first scheduled prediction.

## Model and portfolio promotion gates

Promotion requires all engineering gates plus every primary gate below:

| ID | Locked threshold |
| --- | --- |
| M1 | Exactly 24 scheduled forward cohorts, at least 24 calculable monthly branch-neutral Rank IC values, and at least 10,000 evaluated stock-months. |
| M2 | Mean monthly branch-neutral Spearman Rank IC at least `0.03`. |
| M3 | Newey-West HAC t-statistic of monthly 126-session Rank IC at least `2.0` with lag `5`. |
| M4 | Mean Rank IC exceeds the aligned Sprint 7 price-only model by at least `0.01`. |
| M5 | Equal-weight within-branch top-minus-bottom spread is strictly positive after 25 bps and non-negative after 50 bps. |
| M6 | Long-only six-sleeve net excess after 25 bps is strictly positive versus both SPY and eligible equal weight; benchmark hit rate exceeds 50%. |
| M7 | Annual mean Rank IC is positive in every forward calendar year containing at least six scheduled cohorts. |
| M8 | No single branch, sector, or year contributes more than 50% of the sum of positive monthly net top-minus-bottom spreads. |
| M9 | Selected basket has at least 20 names, maximum single-name weight 5%, and maximum sector weight 35% at every rebalance. |
| M10 | Six-sleeve downside capture is below 100%, and max drawdown is no more than five percentage points worse than eligible equal weight. |
| M11 | All five families are available in at least 90% of final scored rows; no family ablation may be used as a promotion substitute. |

M2–M11 are not inspected if an engineering gate fails. An unevaluable gate fails
promotion.

## Required reports

The one primary evaluation must report:

- full and per-branch coverage funnels;
- classification and reason-code completeness;
- all five family availability and contribution distributions;
- branch-neutral, raw-universe, per-branch, per-sector, and per-year Rank IC;
- HAC inference with the locked lag;
- aligned V2, price-only, and eligible equal-weight comparisons;
- quintile counts, monotonicity, and top-minus-bottom at 0/10/25/50 bps;
- the six-sleeve daily equity curve, turnover, costs, drawdown, downside capture,
  sector/name concentration, liquidity, and delisting contribution;
- frozen leave-one-family-out diagnostics with no retuning;
- complete source, feature, prediction, and outcome hashes;
- gate-by-gate PASS / FAIL / NOT EVALUABLE status.

## Anti-overfitting rules

1. One primary Model V2 specification and one primary 126-session hypothesis.
2. Exact branch features are selected only from accounting validity, source
   reconciliation, and coverage—not returns.
3. At most one outcome-blind implementation revision cycle is allowed before the
   executable lock. Any later change creates a new model version and prediction start.
4. The executable lock must bind clean code, source snapshots, formulas, directions,
   branches, weights, eligibility, thresholds, costs, portfolio notional, and prediction
   dates before the first shadow prediction.
5. Aggregate forward performance remains blinded until all 24 primary outcomes mature.
6. Secondary horizons, family ablations, subtypes, sectors, and years are diagnostics;
   none may replace a failed primary gate.
7. No multiple model variants, weight grids, threshold grids, feature-selection
   searches, or best-subperiod reporting.
8. Every exclusion, delisting, missing outcome, and failed gate remains in the ledger.
9. No threshold may be relaxed after reading a result.
10. A failed V2 is an acceptable answer. It does not authorize V2.1 on the same forward
    window.

## Implementation lock requirements

Before shadow prediction `2026-07-31`, a clean committed executable lock must add:

- full Git commit SHA;
- exact feature definitions and formula hashes per branch;
- exact classification ledger/version and hash;
- source bundle manifests and snapshot hashes;
- complete historical feature/coverage audit hashes;
- fixed prediction dates;
- immutable score-ledger schema;
- portfolio notional, spread model, market-impact rule, and cash treatment;
- exact evaluation code and report schema hashes;
- proof that no V2 forward outcome report exists before the lock.

The current JSON design lock has null placeholders for these implementation-bound
values and therefore must reject outcome evaluation.

## Go / no-go interpretation

- **Go to implementation:** only for data/classification repair and outcome-blind
  engineering work inside this contract.
- **Go to shadow testing:** only after all pre-outcome coverage gates pass in clean
  rebuilds and the executable lock is committed.
- **Go to model promotion:** only after the one forward evaluation passes E1–E10 and
  M1–M11.
- **No-go:** if branch data cannot produce 90% broad coverage, if the model remains
  price/risk-only in practice, or if any forward gate fails.

This contract does not presume that Model V2 will pass. Its purpose is to make failure
informative and future testing fair.

## Evidence binding

The design lock binds the following Sprint evidence by SHA-256:

- Sprint 8 frozen baseline and holdout lock;
- Sprint 9.1 evidence readout;
- Sprint 9.2 cohort funnel;
- Sprint 9.3 factor-family diagnostic;
- Sprint 9.4 investability diagnostic;
- Sprint 9.6 sector-specific treatment contract;
- claims policy.

## Claims boundary

Model V2 remains internal research. Passing this contract's gates would not by itself
authorize public alpha, outperformance, suitability, portfolio, or investment-advice
claims. `claims_eligible=false` is immutable in this hypothesis and every derived lock.
