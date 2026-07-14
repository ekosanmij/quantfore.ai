# Model V2 Accounting Coverage v1

`claims_eligible=false`

- Decision: `PASS`
- Intended stock-months: `50,600`
- Sprint 9 accounting-input readiness: `11.10%`
- Model V2 accounting-input readiness: `66.30%`
- Absolute improvement: `55.20 pp`

## Decision

The accounting-history expansion derives Q2 and Q3 discrete cash-flow periods from adjacent filed YTD facts and Q4 from filed annual minus Q3 YTD. Every derived row is available no earlier than its latest input filing and carries input accessions, values, units, raw hashes, formula version, and a content-addressed lineage record. No return or score field is read.

## Like-for-like accounting readiness

| Component | Family | Sprint 9 | Model V2 | Improvement |
| --- | --- | ---: | ---: | ---: |
| `earnings_yield` | value | 16.31% | 86.37% | +70.06 pp |
| `ebit_ev` | value | 5.77% | 57.38% | +51.61 pp |
| `eps_growth` | growth | 13.09% | 80.59% | +67.50 pp |
| `fcf_conversion` | quality | 0.00% | 58.36% | +58.36 pp |
| `fcf_growth` | growth | 0.00% | 58.27% | +58.27 pp |
| `fcf_yield` | value | 0.00% | 58.37% | +58.37 pp |
| `gross_profitability` | quality | 5.56% | 32.48% | +26.93 pp |
| `inverse_accruals` | quality | 0.00% | 85.94% | +85.94 pp |
| `inverse_leverage` | quality | 64.70% | 76.68% | +11.98 pp |
| `margin_change` | growth | 6.22% | 60.14% | +53.92 pp |
| `revenue_growth` | growth | 15.95% | 76.70% | +60.75 pp |
| `roic` | quality | 0.39% | 53.50% | +53.11 pp |
| `sales_yield` | value | 16.36% | 77.11% | +60.75 pp |

These rates test only whether the point-in-time accounting inputs needed by the frozen generic formulas exist. They do not test values, returns, branch normalization, or final score eligibility.

## Remaining missingness

| Component | Largest remaining reason | Stock-months |
| --- | --- | ---: |
| `earnings_yield` | `SOURCE_MISSING` | 6,472 |
| `ebit_ev` | `SOURCE_MISSING` | 16,915 |
| `eps_growth` | `SOURCE_MISSING` | 7,035 |
| `fcf_conversion` | `SOURCE_MISSING` | 17,062 |
| `fcf_growth` | `SOURCE_MISSING` | 17,062 |
| `fcf_yield` | `SOURCE_MISSING` | 17,062 |
| `gross_profitability` | `SOURCE_MISSING` | 28,912 |
| `inverse_accruals` | `SOURCE_MISSING` | 6,507 |
| `inverse_leverage` | `SOURCE_MISSING` | 9,506 |
| `margin_change` | `SOURCE_MISSING` | 15,435 |
| `revenue_growth` | `SOURCE_MISSING` | 8,223 |
| `roic` | `SOURCE_MISSING` | 17,865 |
| `sales_yield` | `SOURCE_MISSING` | 8,223 |

Missing rows remain missing. The audit never fills a value from a later filing, a different unit, another issuer, or a cross-branch median.

## Bundle reconciliation

The v2 bundle contains `1,164,393` facts: `950,475` reported facts and `213,918` accepted derived facts. Its formula-lineage ledger contains exactly `1,164,393` rows. Direct reported values won `237,448` reported/derived collisions. The declared concept-priority order resolved `102,092` same-accession synonym collisions; those alternatives are not averaged or selected by coverage.

## Controls

- Filing `accepted_at + 1 hour` remains the earliest model-availability time.
- Later comparative filings create append-only revisions; they never rewrite an earlier as-of view.
- Direct discrete facts take precedence over derived values when both exist.
- Synonymous SEC concepts use the declared accounting-priority order in the bundle manifest.
- Missing source, unit conflicts, stale filings, insufficient quarterly history, insufficient prior TTM history, and insufficient balance-sheet history remain distinct reasons.
- Momentum and risk remain price-derived; no accounting proxy is invented for them.
- Candidate bank, insurer, and REIT concepts are preserved for the Sprint 10.4 formula lock, but no branch formula is selected here.
- The pass threshold was fixed at a `10` percentage-point like-for-like readiness gain; the observed gain was `55.20` points.

## Artifacts

- Bundle: `data/raw/free-point-in-time/sec-fundamentals-bundle-v2`
- Bundle manifest SHA-256: `1170f8cec36962d69c34e889e52d6355fb0729bff2c55a1bcc2c284d98bd8833`
- Fundamentals SHA-256: `d168e2ad3ee971fc2fe96dd70c94fae7b837fef466c301a566e74c652d1b9ce0`
- Formula-lineage SHA-256: `28e4d5c1351fc98dedd80d17c19aa0f0c7ae59c6ddad702360485c98d09929c2`
