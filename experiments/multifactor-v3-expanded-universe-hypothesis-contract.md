# Multi-Factor V3 Expanded-Universe Hypothesis Contract

`claims_eligible=false`

```yaml
contract_version: multifactor-v3-expanded-universe-hypothesis-contract-v1
status: design_locked_pending_outcome_blind_universe_feasibility
model_version: multifactor-v3-expanded-universe-branch-aware-equal-weight-v1
universe_version: us-listed-common-equity-pit-v1
parent_failure: model-v2-failure-evidence-freeze-v1
formula_strategy: inherit_model_v2_branch_formulas_without_return_driven_changes
benchmark: SPY
frequency: monthly
primary_horizon_sessions: 126
minimum_eligible_names_per_active_branch: 20
minimum_active_branch_score_coverage: 0.80
minimum_expected_names_per_populated_branch: 25
forward_shadow: pending_new_executable_lock_after_feasibility_and_rebuild
july_2026_backfill: prohibited
```

- Design lock: [`multifactor-v3-expanded-universe-design-lock-v1.json`](multifactor-v3-expanded-universe-design-lock-v1.json)
- Structural feasibility contract: [`model-v3-expanded-universe-feasibility-v1.md`](../docs/research/model-v3-expanded-universe-feasibility-v1.md)
- Frozen predecessor: [`model-v2-failure-evidence-freeze-v1.json`](model-v2-failure-evidence-freeze-v1.json)

## Decision

> **Proceed only to an outcome-blind structural feasibility audit of a broader,
> point-in-time US common-equity universe. Do not acquire accounting data, rebuild
> scores, evaluate returns, or schedule a shadow batch under this design lock.**

Model V2 remains a frozen failed experiment. Model V3 is a new hypothesis prompted
by a structural impossibility in the S&P 500 universe: five specialist branches
could not contain the 20 eligible names required by the locked normalization gate.
Model V3 changes the universe, not the observed V2 result and not the failed V2
thresholds.

## Primary hypothesis

### Structural hypothesis F1

A survivorship-free, monthly point-in-time universe of US-domiciled operating-company
common stocks and REIT common stocks, primary-listed on a US national securities
exchange, can contain enough expected names for every populated accounting branch to
make the unchanged V2 branch gates mathematically attainable.

The audit floor is 25 expected names per populated branch per month:

```text
ceil(20 minimum eligible names / 0.80 minimum branch coverage) = 25
```

The denominator is defined from membership, security type, domicile, listing, and
point-in-time branch classification only. Price availability, filing availability,
feature completeness, eventual eligibility, returns, and score outcomes may not
remove names from the structural denominator.

### Engineering hypothesis H1

Conditional on F1 passing, the expanded universe plus repaired point-in-time source
coverage can satisfy the unchanged V2 engineering gates: at least 90% overall score
coverage, at least 80% coverage in each active branch, at least 20 eligible names in
each active branch, at least five represented branches and sectors, two identical
rebuilds, and zero outcome or cross-branch fallback access.

### Efficacy and portfolio hypotheses H2/H3

The V2 H2 and H3 thresholds are inherited as untested hypotheses. They may be
evaluated only in a new prospective shadow window selected after structural
feasibility, data readiness, score coverage, reproducibility, and a separate
executable lock all pass. No V2 result supplies an efficacy prior.

## Model definition

The initial V3 candidate inherits these V2 choices exactly:

- the eight accounting branches and subtype-specific formula schema;
- branch-only winsorization, standardization, and percentile ranking;
- a minimum normalization cross-section of 20 valid securities;
- value, quality, growth, momentum, and risk at fixed 20% family weights;
- all five families required, with no weight renormalization;
- at least 80% required component coverage and at least 60% coverage within
  each family;
- no imputation, zero filling, cross-branch normalization, or industrial fallback;
- monthly formation, one-session execution lag, 126-session primary horizon,
  portfolio construction, costs, and promotion thresholds.

Inheritance is hash-bound to the frozen V2 branch-score manifest. Model V3 may not
silently alter those formulas during universe feasibility. If feasibility later
proves that the branch taxonomy itself is structurally impossible even in the wider
universe, this candidate fails and any consolidated normalization design requires a
separately named hypothesis and lock.

## Expanded point-in-time universe

`us-listed-common-equity-pit-v1` is a source-agnostic evidence contract, not a claim
that the dataset already exists. It includes US-domiciled operating-company common
stocks and REIT common stocks whose primary listing was active on a US national
securities exchange at the monthly information boundary.

It excludes ETFs and other funds, preferred stock, debt, warrants, rights, units,
depositary receipts, OTC-only securities, non-operating shells, blank-check companies,
the benchmark, and any security whose historical type, domicile, primary listing, or
identity cannot be established point in time. Delisted securities remain in earlier
cohorts; current membership may never replace historical membership.

Every episode must retain stable security and issuer IDs, historical ticker,
effective-from and effective-to dates, source availability time, exchange, security
type, domicile, branch, sector, and source snapshot hash. Later revisions are
append-only.

## Feasibility before acquisition and scoring

The next permitted operation is the structural audit specified in
`model-v3-expanded-universe-feasibility-v1.md`. It must run over every monthly
information boundary in the exposed 2017-01 through 2025-06 diagnostic window.

Every branch with one or more expected members is active for the structural audit.
Branches may not be deactivated because they are small or lack data. The audit fails
if any populated branch-month contains fewer than 25 expected names, if fewer than
five branches or sectors are represented, if identity or classification dispositions
are incomplete, if outcomes are accessed, or if two clean rebuilds differ.

Passing feasibility does not authorize scoring. It only proves that the fixed
branch-size rule is theoretically attainable. Accounting and price readiness must
then pass separately against the full structural denominator.

## Gates retained after feasibility

The original V2 gates remain the minimum V3 engineering thresholds:

| Gate | Threshold |
| --- | --- |
| Overall monthly score coverage | `>= 90%` of all expected non-benchmark members |
| Active-branch monthly score coverage | `>= 80%` in every active branch |
| Eligible names | `>= 20` per active branch every month |
| Representation | `>= 5` active branches and `>= 5` sectors every month |
| Classification | `>= 98%` known point-in-time branch/subtype every month |
| Reconciliation | `100%` final dispositions with stable reason codes |
| Reproducibility | Two clean rebuilds identical |
| Leakage and fallback | Zero |

No feasibility denominator or later score denominator may be reduced in response to
missing price, filing, feature, or outcome data.

## Allowed changes after feasibility passes

Only a later implementation lock may authorize:

1. acquisition and reconciliation of the expanded point-in-time membership and
   identity history;
2. extension of price history and branch-specific accounting evidence;
3. append-only classification of new securities under the inherited branch taxonomy;
4. implementation changes strictly required to process a larger universe;
5. a new future prediction schedule and portfolio notional selected before the first
   V3 prediction.

Every change must be outcome blind, versioned, hash-bound, tested, and reconciled to
the feasibility denominator.

## Prohibited changes

- Editing or relabelling any frozen V2 artifact, threshold, lock, or decision.
- Treating July 2026 as a V3 shadow batch or backfilling any missed batch.
- Reading returns, Rank IC, spreads, portfolio results, or outcomes during universe
  feasibility, data repair, formula inheritance, or coverage rebuilding.
- Dropping names because price, filing, feature, or score data are missing.
- Deactivating a populated branch because it is below the structural floor.
- Lowering the 25-name structural floor, 20-name eligible gate, 80% branch gate, or
  90% overall gate after observing failure.
- Cross-branch normalization, industrial fallback, family renormalization, or
  availability-driven imputation.
- Selecting a new forward window before all pre-shadow engineering gates pass.

## Activation boundary

This design lock is deliberately non-executable. A V3 executable lock may be created
only after structural feasibility, data readiness, score coverage, and two-rebuild
reproducibility pass with zero outcome access. That later lock must bind exact source
snapshots, code revision, formulas, classification ledger, universe ledger,
prediction schedule, report schemas, portfolio notional, and a future shadow start
strictly after the lock is committed.
