# Model V3 Expanded-Universe Structural Feasibility Audit

- Decision: `FAIL_LINEAGE_OR_REPRODUCIBILITY`
- Status: `BLOCKED_MISSING_EXPANDED_UNIVERSE_INPUT`
- Claims eligible: `false`
- Outcomes accessed: `false`
- Data acquisition authorized: `false`
- Score rebuild authorized: `false`
- Shadow authorized: `false`

## Gate results

| Gate | Result | Observed | Threshold |
| --- | --- | ---: | ---: |
| F0 | PASS | 0 | 0 |
| F1 | FAIL | not evaluable | 25 |
| F2 | FAIL | not evaluable | 20 |
| F3 | FAIL | not evaluable | 5 |
| F4 | FAIL | not evaluable | 5 |
| F5 | FAIL | not evaluable | 1.0 |
| F6 | FAIL | not evaluable | 0.98 |
| F7 | FAIL | not evaluable | True |

## Local evidence inventory

- Existing point-in-time bundle: 638 securities and 645 membership episodes; it is S&P 500-only and does not qualify as the Model V3 universe.
- Existing SEC ticker file: 10415 current records; it has no historical monthly membership or delisting proof.
- No qualifying expanded-universe manifest or two-rebuild structural ledger is present.

## Decision

The data-repair phase is blocked. Acquire and reconcile the required historical listing, identity, security-type, domicile, exchange, delisting, branch, and sector evidence first. Do not acquire accounting or price data for the expanded universe and do not rebuild scores.

July 2026 remains non-backfillable.
