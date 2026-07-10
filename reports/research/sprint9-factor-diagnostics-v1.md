# Sprint 9.3 Factor Family Diagnostic v1

`claims_eligible=false`

- Decision: `NOT_A_BROADLY_VALIDATED_FIVE_FAMILY_SIGNAL`
- Evidence generated: `2026-07-10T12:17:01Z`
- Code revision: `77dceb3-dirty-82e6c9129d78`
- Warehouse: `data/raw/free-point-in-time/sprint8-prelock-v9/research.db`
- Machine-readable companion: [`sprint9-factor-diagnostics-v1.json`](sprint9-factor-diagnostics-v1.json)

## Decision

> **Sprint 8 is not a broadly validated five-family signal. In the only evaluated rows it is a four-family value/growth/momentum/risk score; quality contributes nothing.**

The diagnostic covers `50,600` stock-months and `60` evaluated stock-months. Those evaluated rows contain only `5` unique names, all labelled `Financials`, and only `9` months have enough names to calculate Rank IC. The published full-model mean Rank IC is `0.6889`, but its non-overlapping t-statistic remains `None` and its 25 bps top-bucket net excess return is `-6.41%`.

No family is established as genuinely useful. Momentum, risk, and growth show positive ranking behavior in the tiny evaluated cross-sections; value does not, and quality is absent. These are root-cause findings, not promotion evidence.

## Family verdicts

| Family | Universe family availability | Evaluated rows with family | Universe valid component rate | Standalone Rank IC | IC loss when removed | Absolute score contribution | Verdict |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Value | 7.2292% | 60 / 60 | 5.18% | 0.0000 | -0.0222 | 24.41% | Sparse And Not Supported By Ablation |
| Quality | 0.0296% | 0 / 60 | 14.00% | — | 0.0000 | 0.00% | Broken Or Effectively Absent |
| Growth | 0.4881% | 60 / 60 | 0.36% | 0.3778 | 0.3556 | 30.03% | Severely Sparse With Narrow Positive Behavior |
| Momentum | 94.0079% | 60 / 60 | 90.99% | 0.5778 | 0.2556 | 23.92% | Broadly Available With Narrow Positive Behavior |
| Risk | 94.0079% | 60 / 60 | 91.24% | 0.3444 | 0.3111 | 21.63% | Broadly Available With Narrow Positive Behavior |

Availability means the frozen scorer found at least half of a family's applicable components valid. Absolute contribution is descriptive score attribution, not return attribution. Every family verdict retains `evidence_is_sufficient_to_call_useful=false`.

## Where the reported Rank IC came from

| Signal | Mean Rank IC | Calculable months | Top-bucket gross excess | Top-bucket net excess at 25 bps |
| --- | ---: | ---: | ---: | ---: |
| Full Sprint 8 model | 0.6889 | 9 | -6.37% | -6.41% |
| Fundamentals block: value + growth; quality unavailable | 0.0333 | 9 | -7.33% | -7.38% |
| Price/risk block: momentum + risk | 0.4889 | 9 | -5.93% | -5.96% |
| Sprint 7 price-only baseline | 0.5222 | — | — | — |

The grouped diagnostic is the clearest answer to the fundamental-versus-price question: the momentum/risk block records mean Rank IC `0.4889`, versus `0.0333` for the available fundamentals block. Momentum is the strongest standalone family. However, removing growth causes the largest full-model Rank IC loss, so the full ranking appears to depend on interaction between the blocks. Nine tiny pre-holdout cross-sections cannot establish that interaction as stable.

All standalone and grouped top buckets remain negative versus the benchmark after 25 bps. No bottom bucket exists, so top-minus-bottom performance is undefined throughout.

## Frozen family ablations

The Sprint 8 ablations remove one family, renormalize the frozen equal weights across the remaining available families, require at least three remaining families, and do not retune.

| Removed family | Ablated mean Rank IC | Full minus ablated IC | 25 bps top-bucket net excess | Interpretation |
| --- | ---: | ---: | ---: | --- |
| Value | 0.7111 | -0.0222 | -6.41% | Narrow Negative Marginal Contribution |
| Quality | 0.6889 | 0.0000 | -6.41% | No Effect Family Absent |
| Growth | 0.3333 | 0.3556 | -6.80% | Narrow Positive Marginal Contribution |
| Momentum | 0.4333 | 0.2556 | -5.88% | Narrow Positive Marginal Contribution |
| Risk | 0.3778 | 0.3111 | -6.77% | Narrow Positive Marginal Contribution |

Growth has the largest positive removal delta (`0.3556`), followed by risk (`0.3111`) and momentum (`0.2556`). Removing value slightly improves mean Rank IC (`-0.0222` loss). Removing quality changes nothing because quality is unavailable in every evaluated row.

## Missingness by component

The table below uses all 50,600 security-months. A valid component has a stored directed normalized value; every other row retains the scorer's exact reason code.

| Family | Component | Valid | Dominant state | NOT_APPLICABLE | SOURCE_MISSING |
| --- | --- | ---: | --- | ---: | ---: |
| Growth | `eps_growth` | 0.88% | `INSUFFICIENT_HISTORY` (85.20%) | 0 | 7,043 |
| Growth | `fcf_growth` | 0.00% | `INSUFFICIENT_HISTORY` (67.82%) | 8,586 | 1,230 |
| Growth | `margin_change` | 0.05% | `INSUFFICIENT_HISTORY` (63.57%) | 8,586 | 3,361 |
| Growth | `revenue_growth` | 0.51% | `INSUFFICIENT_HISTORY` (82.93%) | 0 | 8,377 |
| Momentum | `momentum_12_1` | 87.97% | `VALID` (87.97%) | 0 | 0 |
| Momentum | `momentum_6_1` | 94.01% | `VALID` (94.01%) | 0 | 0 |
| Quality | `fcf_conversion` | 0.00% | `INSUFFICIENT_HISTORY` (67.82%) | 8,586 | 1,230 |
| Quality | `gross_profitability` | 9.59% | `INSUFFICIENT_HISTORY` (31.45%) | 8,586 | 14,781 |
| Quality | `inverse_accruals` | 0.00% | `INSUFFICIENT_HISTORY` (70.10%) | 8,586 | 77 |
| Quality | `inverse_leverage` | 60.34% | `VALID` (60.34%) | 8,586 | 4,879 |
| Quality | `roic` | 0.08% | `INSUFFICIENT_HISTORY` (63.38%) | 8,586 | 3,390 |
| Risk | `beta_252d` | 88.96% | `VALID` (88.96%) | 0 | 0 |
| Risk | `downside_volatility_126d` | 94.01% | `VALID` (94.01%) | 0 | 0 |
| Risk | `maximum_drawdown_252d` | 87.97% | `VALID` (87.97%) | 0 | 0 |
| Risk | `volatility_126d` | 94.01% | `VALID` (94.01%) | 0 | 0 |
| Value | `earnings_yield` | 7.12% | `INSUFFICIENT_HISTORY` (80.04%) | 0 | 6,494 |
| Value | `ebit_ev` | 3.47% | `INSUFFICIENT_HISTORY` (59.87%) | 8,586 | 3,498 |
| Value | `fcf_yield` | 0.00% | `INSUFFICIENT_HISTORY` (67.82%) | 8,586 | 1,230 |
| Value | `sales_yield` | 10.10% | `INSUFFICIENT_HISTORY` (71.18%) | 0 | 9,473 |

### Components mostly NOT_APPLICABLE or SOURCE_MISSING in evaluated rows

Using a strict greater-than-50% threshold, the following evaluated-score components qualify:

- `fcf_growth` (growth): 100.00%; reasons `{"NOT_APPLICABLE": 60}`.
- `margin_change` (growth): 100.00%; reasons `{"NOT_APPLICABLE": 60}`.
- `fcf_conversion` (quality): 100.00%; reasons `{"NOT_APPLICABLE": 60}`.
- `gross_profitability` (quality): 100.00%; reasons `{"NOT_APPLICABLE": 60}`.
- `inverse_accruals` (quality): 100.00%; reasons `{"NOT_APPLICABLE": 60}`.
- `inverse_leverage` (quality): 100.00%; reasons `{"NOT_APPLICABLE": 60}`.
- `roic` (quality): 100.00%; reasons `{"NOT_APPLICABLE": 60}`.
- `ebit_ev` (value): 100.00%; reasons `{"NOT_APPLICABLE": 60}`.
- `fcf_yield` (value): 100.00%; reasons `{"NOT_APPLICABLE": 60}`.

All nine qualifying components are 100% `NOT_APPLICABLE` in the evaluated cohort: `ebit_ev`, `fcf_yield`, all five quality components, `fcf_growth`, and `margin_change`. This matches the broad Financials mask applied to the five evaluated names. `sales_yield` is additionally `SOURCE_MISSING` in 9 of 60 evaluated rows.

Universe-wide missingness is driven mainly by `INSUFFICIENT_HISTORY`, not only accounting applicability: momentum and risk are broadly available, while fundamental growth/value histories are exceptionally sparse. Quality's component-valid rate is propped up by `inverse_leverage`, but the family-level half-component rule leaves quality available in only 15 of 50,600 stock-months.

## Is the model multi-factor in practice?

| Test | Result |
| --- | --- |
| Every evaluated row has exactly four available families | `true` |
| Quality appears in any evaluated score | `false` |
| Broad five-family model in practice | `false` |
| Broad multi-factor validation established | `false` |
| Any family established as useful | `false` |

Every evaluated score is a four-family value/growth/momentum/risk composite, quality is absent, and performance is measurable in only nine two-to-four-security cross-sections from five Financials-labelled names.

The dominance tests also disagree: momentum leads standalone Rank IC, growth causes the largest ablation loss and has the largest absolute contribution share. Therefore `consistent_single_family_dominance=false`. The correct conclusion is not that one family dominates robustly; it is that the current evidence cannot separate stable signal from a five-name cohort artifact.

## Sprint 9 implications

1. Do not promote or tune Model V2 from this result.
2. Treat quality as broken/effectively absent until family coverage is repaired.
3. Treat value and growth as sparse; growth's positive ablation result is a hypothesis to retest, not a validated family claim.
4. Retain momentum and risk as broadly computable baselines, but do not call them investable while their selected baskets remain negative versus SPY.
5. Resolve Financials/REIT applicability and classification in Sprint 9.6 before interpreting the accounting-family results.

## Integrity and provenance

The recomputed full-model Rank IC matches the published value exactly. Reconstructed monthly outcome returns match the published comparison within `1e-07`; the maximum absolute difference is `2.2760821825863758e-08`.

| Artifact | SHA-256 |
| --- | --- |
| `reports/comparisons/price-vs-multifactor-v1.json` | `2963e3d618df41f4ef55b8c86e63ba505c56a117c8dba7ba2f4d757ffd25b80a` |
| `reports/backtests/pit_multifactor_baseline_v1.json` | `5e1cca6e568599ee1f3badcc6bf051edc1155b15bfb0b408b17051c2b5f60612` |
| `reports/data-audits/sprint9-cohort-funnel-v1.json` | `1e5b3b1e001d77ceeded4fbedfcc9387bd8af6f27e89792616b53b257f329d1d` |
| `docs/research/multifactor-baseline-v1.md` | `3a650dcf5d1837bd1a922b8b80b21210f63838e657b713b836eb30b37918f7c5` |

The large derived warehouse is bound through the deterministic score-family, component-aggregate, and reconstructed-outcome hashes in the JSON companion.

## Claims boundary

This diagnostic does not establish predictive value, outperformance, investability, suitability, or investment advice. `claims_eligible=false` remains mandatory.
