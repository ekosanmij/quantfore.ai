# Multi-Factor Baseline v1

```yaml
contract_version: multifactor-baseline-v1
status: locked_before_holdout
claims_eligible: false
primary_hypothesis: >-
  A diversified, sector-neutral multi-factor score ranks future 126-session
  SPY-relative returns more reliably than the Sprint 7 price-only baseline.
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
development: 2014-01-01/2018-12-31
validation: 2019-01-01/2021-12-31
holdout: 2022-01-01/2025-06-30
```

## Purpose and non-claim status

This is Quantfore's first interpretable fundamental and market multi-factor
baseline. It is a ranking research model, not a buy/sell system. Formula,
direction, applicability, missingness, weights, periods, costs, and promotion
thresholds are frozen here before the holdout is inspected.

All results remain internal research evidence and
`claims_eligible=false`, including a passing holdout. Public or product claims
require the claims-policy process, data-rights evidence, and compliance review.

## Cohort and point-in-time cut-off

The cohort is the complete historical `sp500-pit-v1` membership on the last
regular SPY trading session of each month. SPY is the benchmark and never a
ranked member. Securities later removed, acquired, bankrupt, or delisted remain
in earlier cohorts. Entry is the next regular session after the prediction.

Fundamental inputs must have
`model_available_at <= prediction_timestamp`. For each source fact identity,
the model selects the greatest eligible `revision_version`, not the latest
revision known today. Price, membership, alias, and delisting inputs use the
Sprint 7 leakage rules. Every feature retains the raw value, formula version,
input fact IDs, source snapshot IDs/hashes, and applicability/missingness code.

## Shared financial definitions

All flow variables below are eligible TTM sums of four non-overlapping fiscal
quarters. Vendor TTM records may be used only when they reconcile to eligible
quarters under the data contract. Balance-sheet variables are period-end
instant values. `average(X)` is the mean of the current eligible value and the
eligible value approximately one fiscal year earlier; both are required.

```text
FCF              = cash_from_operations - capital_expenditure
market_cap       = contemporaneous raw close * latest eligible diluted/common shares
net_debt         = total_debt - cash_and_equivalents
enterprise_value = market_cap + total_debt - cash_and_equivalents
invested_capital = total_debt + shareholders_equity - cash_and_equivalents
effective_tax    = income_tax_expense / pretax_income
NOPAT            = EBIT * (1 - effective_tax)
```

Market price and shares must be positive. Enterprise value, invested capital,
average assets, and every explicitly positive denominator must be greater than
zero. Effective tax requires positive pretax income and a rate in `[0, 0.50]`;
otherwise ROIC is missing. No statutory tax-rate imputation is allowed. Values
in one formula must have compatible units and reporting currency.

## Frozen features

Every component is version `multifactor-v1`. “Higher” in the direction column
means a larger raw value receives a larger standardized component. Lower-risk
components are sign-reversed after winsorisation as stated.

| Family | Feature | Frozen raw formula | Preferred direction |
| --- | --- | --- | --- |
| Value | `fcf_yield` | `TTM FCF / market_cap` | Higher |
| Value | `earnings_yield` | `TTM net_income_common / market_cap` | Higher |
| Value | `ebit_ev` | `TTM EBIT / enterprise_value` | Higher |
| Value | `sales_yield` | `TTM revenue / enterprise_value` | Higher |
| Quality | `roic` | `TTM NOPAT / average(invested_capital)` | Higher |
| Quality | `gross_profitability` | `TTM gross_profit / average(total_assets)` | Higher |
| Quality | `fcf_conversion` | `TTM FCF / TTM net_income_common`; denominator must be positive | Higher |
| Quality | `inverse_accruals` | `-(TTM net_income_common - TTM cash_from_operations) / average(total_assets)` | Higher |
| Quality | `inverse_leverage` | `-total_debt / average(total_assets)` | Higher |
| Growth | `revenue_growth` | `(TTM revenue[t] - TTM revenue[t-4q]) / abs(TTM revenue[t-4q])` | Higher |
| Growth | `eps_growth` | `(TTM diluted_eps[t] - TTM diluted_eps[t-4q]) / abs(TTM diluted_eps[t-4q])` | Higher |
| Growth | `fcf_growth` | `(TTM FCF[t] - TTM FCF[t-4q]) / abs(TTM FCF[t-4q])` | Higher |
| Growth | `margin_change` | `TTM EBIT/revenue[t] - TTM EBIT/revenue[t-4q]`; both revenues positive | Higher |
| Momentum | `momentum_6_1` | `adj_close[t-21] / adj_close[t-126] - 1` | Higher |
| Momentum | `momentum_12_1` | `adj_close[t-21] / adj_close[t-252] - 1` | Higher |
| Risk | `volatility_126d` | Sample standard deviation of the latest 126 daily total returns, annualized by `sqrt(252)` | Lower; reverse z-score |
| Risk | `beta_252d` | `cov(security, SPY) / var(SPY)` over 252 aligned daily total returns | Lower; reverse z-score |
| Risk | `downside_volatility_126d` | Root mean square of `min(daily_return, 0)` over 126 sessions, annualized by `sqrt(252)` | Lower; reverse z-score |
| Risk | `maximum_drawdown_252d` | Minimum of `price / running_peak - 1` over 252 sessions | Higher (less negative) |

The `t-21`, `t-126`, and `t-252` indices mean observations, not calendar days.
Returns and momentum use vendor-adjusted total-return series selected under the
Sprint 7 contract. Absolute valuation never multiplies that restated series by
reported shares: market capitalization uses contemporaneous unadjusted `close`
and point-in-time shares outstanding. A missing raw close makes market cap and
dependent value features missing. Beta requires at least 240 aligned returns
and non-zero benchmark variance. Other risk and momentum features require their
full stated history.

The growth denominator may be negative but not zero because the formula uses
its absolute value; sign transitions remain visible and are controlled by the
cross-sectional winsorisation. Non-finite values and invalid denominators are
missing, never zero or capped during raw feature construction.

## Sector-specific applicability

Applicability is determined from the point-in-time sector classification. An
inapplicable component is excluded from the applicable-feature denominator and
is recorded as `NOT_APPLICABLE`; it is not treated as ordinary missing data.

| Group | Inapplicable v1 components | Reason |
| --- | --- | --- |
| Banks, diversified financials, insurance, and other GICS sector 40 issuers | `fcf_yield`, `ebit_ev`, `roic`, `gross_profitability`, `fcf_conversion`, `inverse_accruals`, `inverse_leverage`, `fcf_growth`, `margin_change` | Debt, cash flow, working capital, and enterprise value have structurally different meanings. |
| Equity REITs (GICS industry `601010`) | `fcf_yield`, `ebit_ev`, `roic`, `fcf_conversion`, `fcf_growth` | FFO/AFFO and property leverage require a separately validated model; they are not silently substituted in v1. |
| All other sectors | None by sector | Row-level denominator and source checks still apply. |

Financials retain earnings yield, sales yield, revenue growth, EPS growth,
momentum, and risk where inputs are valid. REITs retain earnings/sales yield,
gross profitability, accruals, leverage, revenue/EPS/margin growth, momentum,
and risk. If the point-in-time sector is unknown, specialized accounting
features are marked `SECTOR_UNKNOWN`, the security fails the 70% coverage test
when appropriate, and it is never assumed to be a general industrial company.

## Missing-value policy

- There is no cross-sectional, time-series, sector-median, zero, or model-based
  imputation in v1.
- Stable reason codes include `SOURCE_MISSING`, `NOT_YET_AVAILABLE`,
  `NOT_APPLICABLE`, `SECTOR_UNKNOWN`, `UNIT_CONFLICT`, `INVALID_DENOMINATOR`,
  `INSUFFICIENT_HISTORY`, `NONFINITE_VALUE`, and `SOURCE_RECONCILIATION_HOLD`.
- A family is available when at least half of its applicable components,
  rounded up, are valid. A family with no applicable components is unavailable.
- A final score requires at least four available families and at least 70% of
  all applicable components across the five families.
- If exactly one family is unavailable, the available family weights are
  renormalized proportionally. With equal v1 weights this is 25% each. No
  component or family receives an implicit zero.
- The score row stores applicable, valid, missing, and inapplicable component
  counts plus every reason code and renormalized weight.

## Cross-sectional normalization and scoring

For each monthly cohort and feature independently:

1. calculate raw values from then-eligible inputs;
2. winsorise valid values at the cohort's 2.5th and 97.5th percentiles using
   linear interpolation;
3. standardize within point-in-time sector using population mean and standard
   deviation;
4. use universe-wide standardization when a sector has fewer than 10 valid
   observations for that feature;
5. assign z-score `0` when the selected valid group has zero dispersion;
6. multiply lower-is-better component z-scores by `-1`; and
7. clip component z-scores to `[-3, 3]`.

A family z-score is the equal-weight mean of its valid component z-scores. The
display family score is `100 * Phi(family_z)` and is bounded to `[0, 100]`.
The composite z-score is the weighted mean of available family z-scores using
the fixed/renormalized family weights. The final score is the within-cohort
percentile rank of composite z-score:

```text
score = 100 * (average_tie_rank - 1) / (number_scored - 1)
```

One scored security receives `50`. Ties receive the average rank. No weights,
winsor limits, minimum sector size, feature list, or score mapping may be tuned
in Sprint 8.

Stored evidence includes raw and winsorized values, normalization group and
fallback flag, group count/mean/deviation, directed z-score, family and final
contributions, formula version, all input fact/price IDs, source hashes, and
missingness/applicability records.

## Raw feature implementation

Sprint 8.4 is implemented by
`quantfore_research.features.multifactor` and
`pipelines/build_multifactor_features.py`. The constructor selects only
fundamental revisions with `model_available_at <= prediction_timestamp`, uses
the greatest eligible revision for each source fact identity, and requires
explicit primary fundamental and price snapshot bindings. SEC reconciliation
facts are therefore not mixed into primary features unless a caller violates
the snapshot contract, which downstream audit rejects.

TTM flows use a vendor-supplied eligible TTM observation when present;
otherwise they require four consecutive quarterly observations with compatible
units. Growth requires a second eligible TTM observation approximately one
year earlier or eight consecutive quarterly observations. Instant balance
sheet averages require current and 300–430-day prior values. Invalid, zero, or
non-positive denominators produce a missing component and stable reason code,
never a numeric zero.

Each stored raw component now includes `raw_value`, family, formula text and
version, preferred direction, applicability status, missing reason, and an
`inputs_json` ledger containing every fact/price record ID, value, unit,
availability timestamp, source snapshot ID, and source hash. Missing and
inapplicable components are stored as explicit rows with null values. Existing
SQLite feature tables are migrated without losing legacy rows, including
relaxing the old non-null value constraint.

Sector and industry are resolved from append-only dated
`security_classifications`, never caller-supplied strings or the security's
current labels. The selected record must cover the prediction date and be model
available by the prediction timestamp. Its ID, system, effective interval,
source snapshot, and hash are stored in feature evidence. An explicit unknown
classification yields `SECTOR_UNKNOWN`. Financial and REIT masks remain
explicit `NOT_APPLICABLE` rows.

```bash
python pipelines/build_multifactor_features.py \
  --security-id <permanent-security-id> \
  --benchmark-security-id <spy-security-id> \
  --prediction-timestamp 2021-12-31T23:59:59Z \
  --classification-id <dated-classification-id> \
  --fundamental-source-snapshot-id <primary-fundamental-snapshot> \
  --security-price-snapshot-id <security-price-snapshot> \
  --benchmark-price-snapshot-id <spy-price-snapshot> \
  --fundamental-audit-json reports/data-audits/pit-fundamentals-v1.json \
  --expected-fundamental-audit-hash <sha256>
```

The pipeline stores all 19 components across value, quality, growth, momentum,
and risk. Stored-feature leakage validation also rechecks every input timestamp
inside `inputs_json`; a future restatement cannot be hidden behind an earlier
feature timestamp.

## Cross-sectional implementation

Sprint 8.5 is implemented by `quantfore_research.scoring.multifactor` and
`pipelines/normalize_multifactor_features.py`. The pipeline reconstructs the
complete historical universe for one prediction timestamp and refuses a raw
feature cohort with missing, duplicate, or extra securities. It then applies
the frozen 2.5%/97.5% linear-interpolation winsor limits independently to each
component.

Valid observations are standardized using population mean/deviation inside
their supplied point-in-time sector when that component has at least 10 valid
sector observations. Smaller groups and unknown sectors use the full valid
universe. Zero-dispersion groups receive neutral z-score zero. Lower-is-better
features are reversed after standardization and every directed component is
clipped to `[-3, 3]`.

Family values are equal-weight means of valid directed components. A family is
available only when at least half of its applicable components, rounded up,
are valid. Display family scores use `100 * Phi(family_z)`. Final eligibility
requires four available families and 70% valid/applicable component coverage.
Unavailable families receive zero weight; remaining 20% weights are
proportionally renormalized. Final eligible scores use average-tie percentile
ranks, with a one-security cohort fixed at 50.

The warehouse stores an immutable `normalization_runs` record, one
`normalized_features` row per raw component, and one `multifactor_scores` row
per security. These preserve raw and winsorized values, undirected and directed
z-scores, group scope/count/mean/deviation, score contribution, five family
scores, renormalized weights, coverage counts, eligibility, and machine-readable
missingness. The normalization input hash binds every output to exact raw
feature IDs and frozen parameters.

```bash
python pipelines/normalize_multifactor_features.py \
  --universe-id sp500-pit-v1 \
  --prediction-timestamp 2021-12-31T23:59:59Z \
  --database-url sqlite+pysqlite:///./quantfore_research.db
```

The command exits non-zero when stored raw features do not exactly match the
historical monthly cohort. It never fills a missing feature with zero, a
sector median, or a universe median.

## Frozen evaluation

| Split | Dates | Permitted use |
| --- | --- | --- |
| Development | 2014-01-01–2018-12-31 | Formula implementation and data-quality debugging. |
| Validation | 2019-01-01–2021-12-31 | One documented controlled revision cycle only. Any change creates `v1.1` and a new lock hash. |
| Holdout | 2022-01-01–2025-06-30 | Exactly one locked final evaluation after code, data, formulas, weights, and thresholds are committed. June 30, 2025 is the latest month-end whose 252-session outcome is mature on July 2, 2026. |

Outcomes are benchmark-relative total returns over 21, 63, 126, and 252
sessions. The 126-session result is primary. Reports include monthly Spearman
Rank IC; non-overlapping Rank IC mean/t-statistic; quintile monotonicity;
top-minus-bottom equal-weight spread; gross and 10/25/50 bps one-way costs;
sector/year stability; turnover; drawdown; downside capture; delisted-security
contribution; family correlations; and coverage/missingness bias.

Comparisons use identical prediction dates and security/outcome intersections:
the equal-weight cohort, Sprint 7 `baseline_v0.1`, and this multi-factor model.
Five family ablations remove one family at a time without retuning. Every
prediction exposes its final score, five family scores, strongest positive and
negative component contributions, missing-data flags, sector-normalization
context, and source evidence references.

Holdout artifacts may not be generated until the contract hash, feature code
commit, source/audit snapshot hashes, and promotion gates are committed to the
experiment registry. Accidental early holdout access invalidates the run and
requires a newly declared untouched holdout or explicit classification as
exploratory evidence.

## Frozen evaluation implementation

Sprint 8.6 is implemented by `quantfore_research.evaluation.multifactor`,
`quantfore_research.evaluation.multifactor_warehouse`, and
`pipelines/evaluate_multifactor_baseline.py`. It accepts no caller-supplied
scores or returns. It reloads `MultiFactorScore`, its linked immutable
`ModelPrediction`, and complete `ModelOutcome` rows from the warehouse,
recalculates prediction/outcome hashes, and reconstructs returns from the exact
stored price snapshots before reporting all four horizons (`21d`, `63d`,
`126d`, and `252d`).

Each horizon reports monthly Rank IC, quintile returns and monotonicity,
top-minus-bottom spread, year and sector stability, top/bottom turnover,
one-way entry and exit cost sensitivity at 10/25/50 bps, drawdown and downside
capture, and delisted-security contribution. Rank IC t-statistics use genuinely
non-overlapping monthly cohorts with strides of 1, 3, 6, and 12 months for the
four respective horizons. Family-score Pearson correlations and coverage-band
missingness diagnostics are reported once over unique score rows.

```bash
python pipelines/evaluate_multifactor_baseline.py \
  --database-url sqlite+pysqlite:///./quantfore_research.db \
  --universe-id sp500-pit-v1 \
  --output reports/backtests/pit_multifactor_baseline_v1.json
```

Any observation dated from `2022-01-01` through `2025-06-30` makes both
`--lock-json` and
`--expected-lock-hash` mandatory. The exact lock binds contract, feature,
normalization and model versions; the full code commit; normalization-run IDs;
the score-ledger hash; exact evaluated source hashes; holdout dates; the frozen
promotion thresholds; and `claims_eligible=false`. Its bytes must equal the
copy committed at a clean `HEAD`, and that commit must predate the earliest
holdout outcome. A late, missing, modified, or incomplete lock rejects
evaluation before any metric is calculated.

The score-ledger hash binds only pre-outcome scores and immutable predictions.
The complete feature, universe, security-price, and benchmark-price snapshot
hash list comes from the frozen bundle manifest, so the lock can be committed
before any `ModelOutcome` is calculated. The later evaluator requires that
exact list to equal the sources actually used.

Prepare the lock only from a clean code commit and before any
`2022-01-01` through `2025-06-30`
multi-factor or comparison outcome exists. The command refuses a late lock,
then writes a file whose only permitted subsequent source-control change is the
lock-only commit:

```bash
python pipelines/prepare_multifactor_holdout_lock.py \
  --database-url sqlite+pysqlite:///./quantfore_research.db \
  --universe-id sp500-pit-v1 \
  --outcome-source-snapshot-id <frozen-security-prices> \
  --outcome-source-snapshot-id <frozen-benchmark-prices> \
  --locked-at 2026-01-01T00:00:00Z
```

The output retains verified database record IDs, source hashes, score-ledger
SHA-256, holdout-lock SHA-256, code revision, and `claims_eligible=false`. The default destination is
`reports/backtests/pit_multifactor_baseline_v1.json`.

## Baseline comparison and attribution implementation

Sprint 8.7 is implemented by
`quantfore_research.evaluation.multifactor_comparison` and
`pipelines/compare_price_vs_multifactor.py`. The comparison first intersects
Sprint 7 and Sprint 8 predictions by exact prediction date and permanent
security ID. The equal-weight cohort, price-only model, multi-factor model,
and all five ablations then use only that shared set. A canonical SHA-256 binds
the report to its ordered date/security intersection.

Each leave-one-family-out diagnostic removes value, quality, growth, momentum,
or risk, renormalizes the frozen equal weights over at least three remaining
available families, and recalculates within-date average-tie percentile scores.
It does not tune weights or alter component definitions.

The comparison reloads every normalized component and source reference. It
also verifies the Sprint 7 prediction/outcome hash and requires the price-only
and multi-factor outcome values to be identical. Consequently every aligned prediction in the output includes its
final score, all five family scores, strongest positive and negative component,
machine-readable missing-data flags, component-level sector/universe
normalization group statistics, and de-duplicated source-evidence references.

```bash
python pipelines/compare_price_vs_multifactor.py \
  --database-url sqlite+pysqlite:///./quantfore_research.db \
  --universe-id sp500-pit-v1 \
  --output reports/comparisons/price-vs-multifactor-v1.json
```

As with the standalone evaluation, any `2022-01-01` through `2025-06-30` row
requires the exact frozen
holdout lock and expected SHA-256. The report preserves verified warehouse
lineage, code revision, `claims_eligible=false`, and explicit no-retuning design.

## Reproducibility and closure implementation

Sprint 8.8 is implemented by
`quantfore_research.validation.sprint8_reproducibility` and
`pipelines/close_multifactor_sprint.py`. Closure requires a clean committed
worktree, an exact frozen bundle-manifest hash, and a hash-bound passing Sprint
7 closure report. It invokes one frozen rebuild program twice in separate fresh
temporary SQLite databases.
The rebuild-program interface receives the bundle directory, expected manifest
hash, fresh database URL, isolated output root, and the same frozen
`--generated-at` value on both invocations.
Its exact bytes are verified against the required SHA-256 immediately before
each invocation, and that digest is recorded in the closure document.

The two runs must match on fundamental fact and availability/revision hashes,
feature count and value hash, monthly eligible-universe hash, immutable
prediction/outcome counts and hash, backtest metrics, and canonical hashes for
the audit, backtest, and comparison. Any mismatch publishes no passing closure.

```bash
python pipelines/close_multifactor_sprint.py /private/frozen-sprint8-bundle \
  --expected-manifest-hash <sha256> \
  --rebuild-program /private/bin/rebuild_sprint8.py \
  --expected-rebuild-program-hash <sha256> \
  --fundamental-source-snapshot-id <snapshot-id> \
  --sprint7-closure-json reports/reproducibility/sprint7-closure-v1.json \
  --expected-sprint7-closure-hash <sha256> \
  --generated-at 2026-01-01T00:00:00Z
```

The repository deliberately contains no synthetic passing Sprint 7 or Sprint
8 closure artifact. Those reports can exist only after licensed frozen data
passes the real two-rebuild process.

## Engineering and promotion gates

Engineering completion requires all of the following:

- zero demonstrated look-ahead or revision leakage;
- two clean database rebuilds with identical fundamental fact, availability,
  revision, feature, monthly universe, prediction, outcome, metric, and report
  hashes;
- monthly final-score coverage of at least 90% of expected non-benchmark
  members in every evaluated cohort;
- every exclusion and missing/inapplicable component represented by a stable
  reason code; and
- proof that holdout outputs were produced only after the lock.

The model is promoted only as a candidate for Sprint 9 research when the
primary 126-session holdout satisfies every threshold below:

1. mean monthly Spearman Rank IC is at least `0.03`;
2. the equal-weight top-minus-bottom quintile spread is strictly positive
   after 25 bps one-way entry and exit costs;
3. annual mean Rank IC is positive in at least three of the four holdout years;
4. no single year contributes more than 50% of the sum of positive monthly
   top-minus-bottom spreads;
5. no single sector contributes more than 50% of the positive aggregate
   sector-neutral top-minus-bottom spread; and
6. all engineering gates pass.

Failure is retained and publishable internally. It does not trigger weight,
threshold, sector-mask, or feature changes against the locked holdout. A future
revision must state a new hypothesis and use a new untouched evaluation design.
