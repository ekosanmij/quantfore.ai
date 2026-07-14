# Model V2 Branch-Aware Implementation v1

`claims_eligible=false`

- Model: `multifactor-v2-branch-aware-equal-weight-v1`
- Features: `multifactor-v2-branch-aware-v1`
- Formulas: `multifactor-v2-branch-formulas-v1`
- Classification: `sec-sic-financial-subtype-v2`
- Normalization: `multifactor-v2-branch-normalization-v1`
- Outcome access: prohibited and not performed

## Implementation decision

Model V2 is implemented separately from the immutable Sprint 8 model. Every
classification-eligible security-month is evaluated only against the required
schema for its point-in-time branch. Accounting components are winsorized and
standardized only inside that branch; a component or branch with fewer than 20
valid securities has no normalization fallback.

All five families are mandatory. Family weights are fixed at 20% each and are
never redistributed. A family requires at least 60% of its locked components,
rounded up, and the row requires at least 80% of all locked components. A branch
must retain at least 20 fully eligible rows before final branch percentiles are
emitted.

## Locked branch formulas

The six price components remain unchanged for every active branch:
`momentum_6_1`, `momentum_12_1`, `volatility_126d`, `beta_252d`,
`downside_volatility_126d`, and `maximum_drawdown_252d`.

| Branch | Value | Quality | Growth |
| --- | --- | --- | --- |
| `INDUSTRIAL_GENERAL` | FCF yield; earnings yield; EBIT/EV; sales/EV | ROIC; gross profitability; FCF conversion; inverse accruals; inverse leverage | revenue growth; EPS growth; FCF growth; margin change |
| `BANK` | earnings yield | ROA; ROE | loan growth; deposit growth; EPS growth |
| `BROKER_DEALER` | earnings yield | ROE | net-revenue growth; EPS growth |
| `ASSET_MANAGER` | earnings yield; inverse price/book | operating margin; ROE | EPS growth |
| `INSURER_P_AND_C` | earnings yield; inverse price/book | inverse loss ratio; ROE | book-value-per-share growth; EPS growth |
| `INSURER_LIFE_HEALTH` | earnings yield; inverse price/book | ROE; investment yield | premium growth; book-value-per-share growth; EPS growth |
| `EQUITY_REIT` | FFO yield | interest coverage | FFO-per-share growth |
| `MORTGAGE_REIT` | inverse price/book; earnings yield | inverse economic leverage; liquidity ratio | book-value-per-share growth; net-interest-income growth |

Every selected specialist feature is inside the candidate envelope of
`sector-specific-factor-treatment-v1`. Candidate measures not supported by the
frozen source bundle—such as CET1, non-performing loans, statutory risk-based
capital, combined ratio expenses, AUM and net flows, AFFO, NAV, occupancy, and
duration gap—are not proxied, imputed, or borrowed from the industrial branch.

The exact formula strings, directions, family assignments, and required flags are
embedded in every score manifest under `branch_schema`, with a deterministic
`branch_schema_sha256`.

## Normalization and exclusion rules

For each prediction date and branch:

1. Each component needs at least 20 valid branch observations.
2. Valid values are winsorized at 2.5% and 97.5% inside the branch.
3. Population z-scores are clipped to `[-3, 3]` after applying the locked direction.
4. Family z-scores are equal-weight means of the valid required components.
5. All five family z-scores are combined with fixed 20% family weights.
6. Final scores are average-tie percentiles among eligible rows in that branch.
7. If fewer than 20 fully eligible rows remain, the branch emits no final score.

No normalization scope other than `BRANCH` or `NONE` is legal. The scorer asserts
that a `BRANCH` component's group is identical to the security's classified branch.

Stable row-level exclusions include classification reasons plus:

- `BRANCH_REQUIRED_FEATURE_MISSING`
- `COMPONENT_COVERAGE_BELOW_MINIMUM`
- `FAMILY_COVERAGE_BELOW_MINIMUM`
- `ALL_FIVE_FAMILIES_REQUIRED`
- `BRANCH_NORMALIZATION_COHORT_TOO_SMALL`
- `SECTOR_BRANCH_EXCLUDED`

Component records retain the underlying input reason, normalization reason,
branch, group count, winsor bounds, mean, standard deviation, and source-lineage
identifiers.

## Outcome-blind pipeline

The first command prepares formula scalars directly from the frozen accounting
bundle, point-in-time subtype ledger, raw prices, and the unchanged Sprint 8 price
components. The warehouse is opened read-only and only
`security_identifiers`, `features`, and `prices` are queried.

```bash
.venv/bin/python pipelines/build_model_v2_score_inputs.py
```

The second command evaluates branch formulas and creates branch-local scores:

```bash
.venv/bin/python pipelines/build_model_v2_scores.py \
  --input experiments/model-v2-branch-feature-inputs-v1.jsonl.gz
```

The scorer recursively rejects input fields named for returns, outcomes, Rank IC,
alpha, future price, or benchmark/excess return before constructing any feature.

## Sprint 10.4 implementation run

The implementation run reconciled all `50,600` classification-ledger rows across
`102` monthly cohorts.

| Control | Result |
| --- | ---: |
| Final scored rows | `16,349` |
| Explicitly excluded rows | `34,251` |
| Scored rows missing any family | `0` |
| Cross-branch fallback count | `0` |
| Outcome fields accessed | `0` |

Only `INDUSTRIAL_GENERAL` produced final scores in this implementation run.
Specialist branches remained excluded when their monthly branch size or locked
feature coverage was insufficient. This is not a coverage-gate decision and does
not inspect efficacy; Sprint 10.5 separately reconciles monthly, branch, and sector
breadth and determines readiness.

## Artifacts

- Prepared inputs (local, reproducible, SHA-bound): `experiments/model-v2-branch-feature-inputs-v1.jsonl.gz`
- Prepared-input manifest: `experiments/model-v2-branch-feature-inputs-v1.manifest.json`
- Score ledger (local, reproducible, SHA-bound): `experiments/model-v2-branch-aware-scores-v1.jsonl.gz`
- Score manifest: `experiments/model-v2-branch-aware-scores-v1.manifest.json`
- Feature formulas: `packages/research/quantfore_research/features/model_v2.py`
- Point-in-time scalar selection: `packages/research/quantfore_research/features/model_v2_inputs.py`
- Scoring: `packages/research/quantfore_research/scoring/model_v2.py`

The two compressed row ledgers are intentionally excluded from Git because each is
larger than GitHub's per-file limit. Their versioned manifests retain exact SHA-256
bindings, row counts, source lineage, formulas, and output paths. The Sprint 10.5
clean-rebuild command reproduces both ledgers byte-for-byte from the frozen source
bundle; it does not require either ledger to be downloaded from GitHub.

## Claims boundary

This implementation proves only that the branch mechanics execute with complete
dispositions, fixed weights, all-family enforcement, and no cross-branch fallback.
It does not establish coverage readiness, signal efficacy, portfolio value,
investability, suitability, or performance. The design lock remains non-executable
for shadow predictions until the later executable lock is completed.
