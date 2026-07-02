# Real-Data Trial v0 — WP6.7 Plausibility Review

> **PROTOTYPE REAL-DATA TRIAL**
> **NOT POINT-IN-TIME UNIVERSE VALIDATION**
> **NOT ELIGIBLE FOR PERFORMANCE CLAIMS**

**Decision:** `requires_revision_before_model_claims`

The data and execution path are mechanically plausible, but the stored trial does not support model-performance claims.

## Findings

- **HIGH** — Quintile returns are non-monotonic and the unadjusted top-minus-bottom spread is negative.
- **HIGH** — Mean Rank IC is weak, statistically unpersuasive, and changes sign across calendar years.
- **MEDIUM** — The top-minus-bottom spread changes sign under the fixed outlier winsorisation diagnostic.
- **MEDIUM** — The mega-cap proxy is over-represented in the top quintile relative to its universe share.
- **REVIEW** — Excluding WP6.3 split-flagged securities does not restore monotonic quintile behaviour.
- **PASS** — Feature completeness and evaluated-outcome coverage are complete for the stored trial.

## Baseline diagnostics

- Periods: 55
- Observations: 1375
- Coverage: 1.000000
- Mean Rank IC: 0.059287
- Rank IC t-statistic: 0.588890
- Top-minus-bottom spread: -0.001212
- Monotonic quintiles: False

### Rank IC stability

| Year | Periods | Mean Rank IC | Positive periods | Top-bottom spread |
| ---: | ---: | ---: | ---: | ---: |
| 2020 | 1 | -0.264615 | 0.000000 | -0.151573 |
| 2021 | 12 | -0.010321 | 0.416667 | -0.036334 |
| 2022 | 12 | -0.047692 | 0.583333 | -0.104348 |
| 2023 | 12 | 0.354487 | 0.833333 | 0.177984 |
| 2024 | 12 | 0.045321 | 0.583333 | 0.008009 |
| 2025 | 6 | -0.096026 | 0.333333 | -0.076472 |

## Score and feature checks

- Feature values expected/received: 5500/5500
- Missing/duplicate/unexpected: 0/0/0
- Scores at 0/100: 0/1

| Feature | Min | Median | Mean | Max | Inferred clamp count |
| --- | ---: | ---: | ---: | ---: | ---: |
| momentum_6_1 | -0.506149 | 0.060302 | 0.079356 | 1.695960 | 21 |
| momentum_12_1 | -0.712877 | 0.136282 | 0.200163 | 2.870082 | 55 |
| return_21d | -0.330191 | 0.016534 | 0.015670 | 0.337876 | 0 |
| volatility_126d | 0.006839 | 0.015942 | 0.016888 | 0.043806 | 0 |

## Concentration and dependence

- Largest top-quintile sector: Information Technology (0.189091)
- Top-quintile sector HHI: 0.123729
- Mega-cap proxy universe share: 0.240000
- Mega-cap proxy top-quintile share: 0.370909

## Before/after diagnostics

| Scenario | Observations | Mean Rank IC | Top-bottom spread | Monotonic |
| --- | ---: | ---: | ---: | --- |
| Baseline | 1375 | 0.059287 | -0.001212 | False |
| Exclude WP6.3 review securities | 1045 | 0.077225 | -0.008322 | False |
| Exclude mega-cap proxy | 1045 | 0.054577 | 0.013137 | False |
| Winsorise outcomes at +/-75% | 1375 | 0.059226 | 0.004403 | False |

WP6.3 review exclusions: AAPL, AMZN, GOOGL, NEE, NVDA, WMT.
Mega-cap proxy exclusions: AAPL, AMZN, GOOGL, META, MSFT, NVDA.
Outlier winsorisation affected 15 observations and is a post-hoc sensitivity diagnostic only.

## Largest outcome outliers

| Ticker | Prediction date | Excess return |
| --- | --- | ---: |
| NVDA | 2022-12-30 | 1.778711 |
| NVDA | 2023-12-29 | 1.489351 |
| META | 2022-10-31 | 1.390357 |
| NVDA | 2023-11-30 | 1.330947 |
| NVDA | 2022-11-30 | 1.229068 |
| META | 2022-11-30 | 1.195973 |
| META | 2022-12-30 | 1.177500 |
| NVDA | 2022-09-30 | 1.070796 |
| NVDA | 2023-01-31 | 1.025214 |
| NVDA | 2023-02-28 | 1.019778 |

## Required follow-up

1. Validate corporate actions and adjusted-price conventions for every WP6.3 review security.
2. Replace the retrospective universe with point-in-time membership before any performance interpretation.
3. Investigate the 2023 regime concentration and NVDA/META outcome outliers.
4. Revisit the heuristic or feature normalisation; require stable, monotonic out-of-sample behaviour before promotion.
5. Add realistic turnover, liquidity and market-impact modelling.
