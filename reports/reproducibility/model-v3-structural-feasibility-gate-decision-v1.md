# Model V3 Structural Feasibility Gate Decision

- Decision: `NO_GO_MISSING_EXPANDED_UNIVERSE_EVIDENCE`
- Status: `CLOSED_NO_GO_BEFORE_REBUILD`
- Reason: `QUALIFYING_POINT_IN_TIME_UNIVERSE_LEDGER_NOT_PRESENT`
- Claims eligible: `false`
- Outcomes accessed: `false`
- Feature or score rebuild authorized: `false`
- Shadow prediction authorized: `false`

## Locked structural rule

The model requires at least 20 eligible names at 80% minimum branch coverage. Therefore every populated branch must contain at least `ceil(20 / 0.80) = 25` expected names before an expensive rebuild begins.
Populated branches cannot be deactivated and missing data cannot shrink the denominator.

## Audit result

- Audit decision: `FAIL_LINEAGE_OR_REPRODUCIBILITY`
- Evaluated months: `0 / 102`
- Failed or unevaluable gates: `F1, F2, F3, F4, F5, F6, F7`

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

## Decision

No accounting, price, feature, score, executable-lock, or shadow rebuild may proceed. The next action is W0: select and separately authorize a historical expanded-universe evidence source, build two identical point-in-time ledgers, and rerun this unchanged audit.

No threshold change, denominator shrinkage, outcome access, or July 2026 backfill is allowed.
