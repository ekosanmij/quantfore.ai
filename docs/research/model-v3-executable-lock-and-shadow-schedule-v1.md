# Model V3 Executable Lock and Prospective Shadow Schedule Contract

Status: `BLOCKED_PREREQUISITES_FAILED`
Claims eligible: `false`
Executable lock created: `false`
Shadow date selected: `false`

## Purpose

This is the final pre-shadow gate for Model V3. It may create an executable lock and
select a prospective shadow schedule only after structural feasibility, data
readiness, the outcome-blind rebuild, every coverage gate, and two-rebuild
reproducibility pass.

The current decision is recorded in
[`model-v3-executable-lock-decision-v1.md`](../../reports/reproducibility/model-v3-executable-lock-decision-v1.md).

## Mandatory prerequisites

All prerequisites are conjunctive:

1. `PASS_STRUCTURALLY_FEASIBLE` over all 102 exposed diagnostic months.
2. Hash-bound expanded-universe, identity, classification, price, corporate-action,
   delisting, and accounting evidence.
3. An outcome-blind Model V3 rebuild completed twice from clean directories.
4. Exact rebuild equality across classifications, facts, features, eligibility,
   scores, dispositions, manifests, and reports.
5. At least 90% overall score coverage in every month.
6. At least 80% score coverage and 20 eligible names in every active branch every
   month.
7. At least five represented branches and sectors every month.
8. At least 98% known point-in-time branch/subtype and 100% stable final
   dispositions every month.
9. Zero cross-branch fallback, return, outcome, or post-boundary access.
10. Exact code, source, universe, formula, schedule, report, portfolio, and cost
    bindings available for a new executable lock.

A missing or `NOT EVALUABLE` prerequisite fails the gate.

## Required executable-lock bindings

The future lock must bind exact hashes for:

- Model V3 hypothesis and structural feasibility contracts;
- frozen Model V2 failure evidence;
- implementation code revision and dependency environment;
- expanded-universe membership, identity, delisting, and classification ledgers;
- price, corporate-action, and accounting source manifests;
- inherited formula and branch schema;
- feature, eligibility, score, reason-code, and report schemas;
- two-clean-rebuild fingerprints and readiness report;
- benchmark, monthly formation rule, execution lag, horizons, costs, portfolio
  notional, liquidity rule, and prediction schedule.

No placeholder may remain null. The lock must be committed before the first source
snapshot or prediction associated with the forward schedule is created.

## Prospective schedule rule

No shadow date is selected while this gate is blocked. After an executable lock is
committed, a separate immutable schedule may select 24 consecutive future monthly
information boundaries. The first scheduled boundary must be strictly after the
executable-lock commit and must still be operationally reachable before the source
cutoff and prediction timestamp.

The blocked July 2026 V2 batch is permanently non-backfillable. A future V3 schedule
cannot claim or recreate it.

## Current decision

The structural gate is a no-go, the data inputs are absent, the outcome-blind rebuild
was not authorized or started, no Model V3 coverage report exists, and no canonical
Model V3 score artifact exists. Therefore:

- no executable lock is created;
- no prediction schedule or first shadow date is selected;
- no source snapshots or real prediction batch are created;
- no outcome or performance evaluation is authorized.

The only permitted next action remains W0: establish the expanded point-in-time
universe, pass structural feasibility, and proceed again through the data and rebuild
gates without changing thresholds.
