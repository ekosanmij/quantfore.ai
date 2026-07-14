# Model V3 Outcome-Blind Rebuild Contract

Status: `BLOCKED_PREREQUISITES_FAILED`
Claims eligible: `false`
Rebuild executable: `false`
Outcomes accessible: `false`

## Purpose

This contract governs the first full Model V3 classification, accounting, feature,
eligibility, and score rebuild. It is deliberately downstream of the structural
universe feasibility gate and cannot run while that gate is a no-go.

The current decision is recorded in
[`model-v3-outcome-blind-rebuild-decision-v1.md`](../../reports/reproducibility/model-v3-outcome-blind-rebuild-decision-v1.md).

## Required predecessors

Every predecessor is mandatory:

1. The expanded-universe structural audit returns
   `PASS_STRUCTURALLY_FEASIBLE` for all 102 monthly information boundaries.
2. Every populated branch contains at least 25 expected names every month, making
   20 eligible names attainable at 80% branch coverage.
3. Two independently rebuilt universe ledgers are identical.
4. A separately authorized data phase produces hash-bound universe, identity,
   classification, price, corporate-action, delisting, and accounting manifests.
5. Price history supplies at least 252 pre-boundary sessions wherever listing
   history permits, without removing missing-price securities from the denominator.
6. Accounting evidence has point-in-time accession, acceptance, period, unit,
   revision, tag, identity, and source lineage.
7. A versioned data-readiness authorization is committed before the rebuild starts.
8. Return, outcome, Rank IC, spread, portfolio, and future performance stores remain
   inaccessible to every rebuild process.

`NOT EVALUABLE` is a failure. A missing input cannot be treated as a pass.

## Locked rebuild stages

The rebuild must execute twice from clean, separate work directories:

1. reconstruct expected security-months from the point-in-time universe ledger;
2. resolve historical security, issuer, ticker, membership, branch, and sector;
3. select point-in-time prices, corporate actions, filings, and accounting facts;
4. calculate inherited V2 branch-specific components and five fixed-weight families;
5. apply branch-only normalization with a minimum valid cross-section of 20;
6. create exactly one scored or explicitly excluded disposition for every expected
   security-month;
7. compare every classification, fact, feature, eligibility, score, manifest, report,
   and reason-code hash across the two clean rebuilds;
8. run the locked coverage audit without reading outcomes.

The two rebuilds must use identical frozen inputs and code. Temporary artifacts are
not canonical evidence. Canonical outputs may be published only after the two hashes
match exactly.

## Locked acceptance gates

| Gate | Required result |
| --- | --- |
| Overall monthly score coverage | `>= 90%` of every expected non-benchmark cohort |
| Active-branch monthly score coverage | `>= 80%` in every active branch |
| Branch breadth | `>= 20` eligible names in every active branch |
| Representation | `>= 5` active branches and `>= 5` sectors every month |
| Classification | `>= 98%` known point-in-time branch/subtype every month |
| Reconciliation | `100%` final dispositions with stable reason codes |
| Reproducibility | Two clean rebuilds identical on all locked artifacts |
| Cross-branch fallback | `0` |
| Return or outcome access | `0` |

All gates are conjunctive. There is no warm-up, small-branch, missing-data, or early
month exception.

## Prohibited inputs and behavior

- returns, outcomes, benchmark returns, Rank IC, spreads, portfolio results, or
  later information;
- current constituents substituted for historical membership;
- deletion of expected members because prices, filings, features, or scores are
  missing;
- branch deactivation because a populated branch is small;
- cross-branch normalization, industrial fallback, family-weight renormalization,
  imputation, or availability-driven schema changes;
- formula, threshold, benchmark, cost, schedule, or horizon changes after observing
  failure;
- mutation of frozen Model V2 evidence or any July 2026 backfill.

## Current decision

The structural gate is currently
`NO_GO_MISSING_EXPANDED_UNIVERSE_EVIDENCE`, zero of 102 months have been evaluated,
and the required Model V3 universe and data manifests do not exist. Therefore no
rebuild may start and no canonical feature or score output may be created.

The only next action remains W0: separately authorize the structural evidence source,
build two identical expanded-universe ledgers, and rerun the unchanged feasibility
audit.
