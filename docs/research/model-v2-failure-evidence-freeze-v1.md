# Model V2 Failure Evidence Freeze v1

`claims_eligible=false`

## Decision

Model V2 is closed as `FROZEN_FAILED_NOT_SHADOW_READY`. Its design thresholds,
coverage failure, pre-shadow lock, synthetic rehearsal, and first-batch no-go are
append-only evidence. They must not be edited to make the failed implementation
appear ready.

The freeze is anchored to repository commit
`e18303e686f1946f83a5451e868a12cd1aa45375`, the merge commit that completed
Sprint 10. Every frozen file must remain byte-identical to its copy at that commit.

## What is frozen

The manifest `experiments/model-v2-failure-evidence-freeze-v1.json` binds:

- the original hypothesis contract and design lock;
- the subtype classification ledger and coverage evidence;
- accounting, feature-input, formula, score, and readiness manifests;
- the failed Sprint 10.5 coverage report;
- the blocked, non-executable Sprint 10.6 pre-shadow lock;
- the synthetic-only Sprint 10.7 rehearsal fixture and reports; and
- the Sprint 10.8 first-batch no-go and anti-backfill decision.

The manifest also embeds and hashes the full locked model, universe, evaluation,
portfolio-cost, engineering-gate, and promotion-gate contract. The observed failed
criteria and reconciliation counts are separately embedded and hashed.

## Enforcement

Run:

```bash
python pipelines/freeze_model_v2_failure_evidence.py --verify
```

Verification fails if:

- a frozen path differs from its exact bytes at the closure commit;
- a bound SHA-256 no longer reproduces;
- a model threshold, universe rule, cost, schedule, or gate changes;
- the readiness decision is no longer failed;
- the pre-shadow lock becomes executable;
- the synthetic rehearsal is represented as real authorization; or
- the first-batch no-go is relabelled as a created batch.

## Mutation and correction policy

Frozen paths are immutable. A factual correction must be an append-only,
versioned amendment that names and hashes the original artifact. The original
file remains unchanged, and the amendment must explain why it does not constitute
outcome-driven model selection or retroactive evidence rewriting.

Thresholds may not be relaxed in place. Locks may not be rewritten. The failed
decision may not be renamed or removed.

## Next-version boundary

Remediation must use a new model version and a new design lock. Any broader
point-in-time universe, branch consolidation, hierarchical normalization, or
revised feasibility rule must be declared before outcomes are opened. New work
must first prove that every active branch can theoretically satisfy its minimum
cross-section.

The July 2026 batch remains non-backfillable. A later shadow date may be selected
only prospectively after the new version passes every outcome-blind readiness
gate and receives its own executable lock.

## Claims boundary

This freeze preserves a failed engineering result. It is not a prediction,
performance claim, recommendation, product output, or authorization to start
shadow testing.
