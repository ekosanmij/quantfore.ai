# Sprint 9.4 Investability Diagnostic v1

`claims_eligible=false`

- Decision: `NOT_INVESTABLE_ON_OBSERVED_EVIDENCE`
- Deployable portfolio evaluable: `false`
- Evidence generated: `2026-07-10T16:57:05Z`
- Code revision: `6e0ad2a-dirty-d04e94e40308`
- Warehouse: `data/raw/free-point-in-time/sprint8-prelock-v9/research.db`
- Machine-readable companion: [`sprint9-investability-diagnostic-v1.json`](sprint9-investability-diagnostic-v1.json)

## Decision

> **The observed Sprint 8 cohort does not support an investable portfolio. The selected basket underperforms SPY before costs and underperforms the same eligible cohort held equal weight. A deployable capital-account backtest is not evaluable from these overlapping, single-name forward windows.**

The diagnostic covers `43` monthly rebalance periods and `60` stock-months. The top bucket contains exactly one security in every month, every selected holding is labelled `Financials`, and `34` months contain only one eligible security. No bottom bucket exists.

The selected stock earns an average 126-session return of `0.65%` while SPY earns `7.02%` on the aligned windows. Gross excess is `-6.37%` and net excess after 25 bps is `-6.41%`. The result is negative before transaction costs, so cost drag is not the root cause.

## Portfolio outcome summary

| Metric | Result | Interpretation |
| --- | ---: | --- |
| Mean selected-stock forward return | 0.65% | Arithmetic mean of overlapping 126-session cohorts. |
| Mean aligned SPY return | 7.02% | Same entry/exit sessions as each selected holding. |
| Mean gross excess return | -6.37% | Negative before costs. |
| Gross benchmark hit rate | 39.53% | Selected stock beats SPY in 17 of 43 periods. |
| Positive absolute return rate | 48.84% | Selected stock has a positive return in 21 of 43 periods. |
| Mean eligible equal-weight excess return | -5.95% | The narrow cohort itself also underperforms SPY. |
| Selected minus eligible equal-weight | -0.42% | Ranking reduces return versus holding every eligible name. |

These figures are not annualized or compounded. Monthly cohorts have overlapping 126-session holding windows, so treating them as a single sequential equity curve would double-count capital.

## Top-minus-bottom

Top-minus-bottom is **not evaluable**: `0` of 43 months contain a bottom bucket. The largest eligible cohort contains four securities; quintile 1 requires at least five. No spread, monotonicity, or long-short portfolio claim can be made.

## Turnover and transaction costs

Mean selection turnover is `13.95%` and the median is `0.00%`. Turnover is non-zero in `6` periods, including initial entry. Because the top basket is a single name, turnover is either 0% or 100%.

| Cost assumption | Mean net excess return | Mean cost drag | Net benchmark hit rate |
| ---: | ---: | ---: | ---: |
| 10 bps | -6.38% | 0.0140% | 39.53% |
| 25 bps | -6.41% | 0.0349% | 39.53% |
| 50 bps | -6.44% | 0.0698% | 39.53% |

At 25 bps the average drag is only 3.49 basis points, compared with gross excess of -6.37%. This cost calculation does not include bid-ask spread or market impact.

### Monthly turnover ledger

| Prediction date | Selected holding | Turnover |
| --- | --- | ---: |
| 2018-10-31 | HIG | 100.00% |
| 2018-11-30 | HIG | 0.00% |
| 2018-12-31 | HIG | 0.00% |
| 2019-01-31 | HIG | 0.00% |
| 2019-02-28 | VNO | 100.00% |
| 2019-03-29 | VNO | 0.00% |
| 2019-04-30 | VNO | 0.00% |
| 2019-05-31 | VNO | 0.00% |
| 2019-06-28 | VNO | 0.00% |
| 2019-07-31 | VNO | 0.00% |
| 2019-08-30 | VNO | 0.00% |
| 2019-09-30 | VNO | 0.00% |
| 2019-10-31 | AMT | 100.00% |
| 2019-11-29 | HIG | 100.00% |
| 2019-12-31 | HIG | 0.00% |
| 2020-01-31 | HIG | 0.00% |
| 2020-02-28 | HIG | 0.00% |
| 2020-03-31 | AMT | 100.00% |
| 2020-04-30 | AMT | 0.00% |
| 2020-05-29 | AMT | 0.00% |
| 2020-06-30 | AMT | 0.00% |
| 2020-07-31 | AMT | 0.00% |
| 2020-08-31 | AMT | 0.00% |
| 2020-09-30 | AMT | 0.00% |
| 2020-10-30 | AMT | 0.00% |
| 2020-11-30 | AMT | 0.00% |
| 2020-12-31 | AMT | 0.00% |
| 2021-01-29 | AMT | 0.00% |
| 2021-02-26 | AMT | 0.00% |
| 2021-03-31 | REG | 100.00% |
| 2021-04-30 | REG | 0.00% |
| 2021-05-28 | REG | 0.00% |
| 2021-06-30 | REG | 0.00% |
| 2021-07-30 | REG | 0.00% |
| 2021-08-31 | REG | 0.00% |
| 2021-09-30 | REG | 0.00% |
| 2021-10-29 | REG | 0.00% |
| 2021-11-30 | REG | 0.00% |
| 2021-12-31 | REG | 0.00% |
| 2022-01-31 | REG | 0.00% |
| 2022-02-28 | REG | 0.00% |
| 2022-03-31 | REG | 0.00% |
| 2022-04-29 | REG | 0.00% |

## Drawdown and downside capture

| Diagnostic | Result |
| --- | ---: |
| Mean selected holding-window max drawdown | -19.90% |
| Median selected holding-window max drawdown | -15.88% |
| Worst selected holding-window max drawdown | -57.00% |
| Down-market periods | 11 |
| Mean selected return in down markets | -12.25% |
| Mean SPY return in down markets | -9.10% |
| Downside capture | 134.62% |

The worst single cohort loses 57.00% peak-to-trough and downside capture is 134.62%, meaning the selected holding loses more than SPY on average when SPY is down. A stitched capital-account max drawdown is not reported because no non-overlapping daily allocation ledger exists.

## Concentration

| Measure | Result |
| --- | ---: |
| Holdings per selected basket | 1 |
| Maximum single-name weight | 100.00% |
| Mean single-name HHI | 1.0000 |
| Unique selected names | 4 |
| Maximum sector weight | 100.00% |
| Mean sector HHI | 1.0000 |
| Unique selected sectors | 1 |

| Selected name | Periods selected | Share of periods |
| --- | ---: | ---: |
| AMT | 13 | 30.23% |
| HIG | 8 | 18.60% |
| REG | 14 | 32.56% |
| VNO | 8 | 18.60% |

The only selected sector is `Financials` at `100.00%` of holding observations. This is complete sector and single-name concentration in each period, not a diversified portfolio.

## Liquidity screen

Volume is available, so the report uses the point-in-time median of unadjusted close × reported volume over the 20 sessions ending on each prediction date.

| Liquidity statistic | Result |
| --- | ---: |
| Complete 20-session windows | 43 / 43 |
| Minimum median daily dollar volume | $41.91m |
| Median median daily dollar volume | $79.97m |
| Maximum median daily dollar volume | $764.23m |

| Diagnostic threshold | Holding observations passing | Pass rate |
| ---: | ---: | ---: |
| $1.00m | 43 / 43 | 100.00% |
| $5.00m | 43 / 43 | 100.00% |
| $10.00m | 43 / 43 | 100.00% |
| $25.00m | 43 / 43 | 100.00% |
| $50.00m | 40 / 43 | 93.02% |
| $100.00m | 20 / 43 | 46.51% |

All selected holding observations pass the $25 million screen; liquidity is therefore not the observed reason for the negative result. These are diagnostic thresholds, not promotion gates, and dollar volume alone does not establish capacity or executable slippage.

## Model selection versus equal weight

The eligible equal-weight basket has mean excess return `-5.95%` versus SPY. Model selection reduces that by a further `-0.42%` across all periods. In the nine months with an actual choice among multiple names, selection lift averages `-2.03%` and is positive in `44.44%` of those months.

The comparison separates two effects: the narrow Financials-labelled cohort itself trails SPY, and the model's top selection trails that narrow cohort. Because 34 months are singletons and no sector-neutral benchmark was frozen, the relative contribution of weak signal and benchmark mismatch cannot be identified cleanly.

## Why net excess is negative

| Candidate cause | Finding | Evidence |
| --- | --- | --- |
| Transaction costs | **Not primary** | 25 bps costs add only 0.0349% drag; gross excess is already -6.37%. |
| Model selection | **Negative incremental value** | Selected minus eligible equal-weight is -0.42%; multi-name-month lift is -2.03%. |
| Cohort construction | **Dominant structural limitation** | 34 singleton months, one selected name, one selected sector, and no bottom bucket. |
| Benchmark mismatch | **Unresolved** | The eligible Financials-labelled cohort is -5.95% versus broad-market SPY; no sector-neutral benchmark exists. |
| Liquidity | **Not an observed bottleneck** | Minimum trailing median daily dollar volume is $41.91m. |
| Weak signal | **Not separately identifiable** | Only nine tiny cross-sections have Rank IC, while portfolio selection is negative. |

Negative net excess is already present before costs. Costs add only a small drag; extreme cohort and benchmark concentration prevent a clean weak-signal versus benchmark-mismatch attribution.

## Implementability boundary

The current evidence is a cohort-level forward-outcome diagnostic, not a deployable portfolio backtest. It lacks a single daily capital-allocation curve, non-overlapping return protocol, bid-ask spreads, market impact, and a sector-neutral comparator. Accordingly:

- Investability established: `false`
- Ranking usefulness for portfolio construction established: `false`
- Annualized return reported: `false`
- Stitched capital-account curve available: `false`
- Volume screen available: `true`

## Evidence integrity

All reconstructed gross return, equal-weight return, turnover, cost, drawdown, and downside-capture metrics reconcile to the published Sprint 8 comparison within `1e-05`.

| Artifact | SHA-256 |
| --- | --- |
| `reports/comparisons/price-vs-multifactor-v1.json` | `2963e3d618df41f4ef55b8c86e63ba505c56a117c8dba7ba2f4d757ffd25b80a` |
| `reports/backtests/pit_multifactor_baseline_v1.json` | `5e1cca6e568599ee1f3badcc6bf051edc1155b15bfb0b408b17051c2b5f60612` |
| `reports/data-audits/sprint9-cohort-funnel-v1.json` | `1e5b3b1e001d77ceeded4fbedfcc9387bd8af6f27e89792616b53b257f329d1d` |
| `reports/research/sprint9-factor-diagnostics-v1.json` | `c84a1eca3d9ab938bdb3de796a40a9d264dfd9cb319198b3ffef6263feb2935d` |
| `docs/research/multifactor-baseline-v1.md` | `3a650dcf5d1837bd1a922b8b80b21210f63838e657b713b836eb30b37918f7c5` |

The large derived warehouse is bound through the reconstructed outcome, portfolio-period, and point-in-time liquidity hashes in the JSON companion.

## Claims boundary

This report does not establish predictive value, outperformance, investability, suitability, executable capacity, or investment advice. `claims_eligible=false` remains mandatory.
