# Sprint 9.2 Coverage and Cohort Audit v1

`claims_eligible=false`

- Decision: `fail`
- Evidence generated: `2026-07-10T12:00:00Z`
- Code revision: `77dceb3-dirty-09758a884976`
- Universe: `sp500-pit-v1`
- Window: `2017-01-31` through `2025-06-30`
- Monthly cohorts: `102`
- Authoritative warehouse: `data/raw/free-point-in-time/sprint8-prelock-v9/research.db`
- Stock-month explanation ledger: [`reports/data-audits/sprint9-cohort-funnel-explanations-v1.jsonl.gz`](sprint9-cohort-funnel-explanations-v1.jsonl.gz)

## Decision

> **Sprint 8 evidence is not broad enough to trust as an S&P 500 multi-factor result.**

The point-in-time universe contains `50,600` security-months from `636` unique securities across 102 cohorts. All of them have an exact positive price on the prediction date and all have complete 19-component raw and normalized ledgers. Only `162` security-months reach four available factor families; `102` of those then fail the 70% component-coverage rule. The remaining `60` become predictions, mature outcomes, and final evaluation records without further loss.

Full-window final-score coverage is `0.1186%` (`60 / 50,600`), with `59 / 102` months producing no score and no month reaching the frozen 90% requirement. All 60 evaluated observations are labelled Financials.

## Reconciled processing funnel

These stages are nested and reconcile exactly:

| Stage | Security-months | Drop from prior stage | Retained from universe |
| --- | ---: | ---: | ---: |
| Point-in-time universe members | 50,600 | 0 | 100.0000% |
| Complete 19-component raw feature sets | 50,600 | 0 | 100.0000% |
| Entered monthly scoring with 19 normalized components | 50,600 | 0 | 100.0000% |
| At least four available factor families | 162 | 50,438 | 0.3202% |
| At least 70% coverage after family pass | 60 | 102 | 0.1186% |
| Eligible final scores | 60 | 0 | 0.1186% |
| Security-months with prediction records | 60 | 0 | 0.1186% |
| Security-months with mature 126-session outcomes | 60 | 0 | 0.1186% |
| Final 126-session evaluation observations | 60 | 0 | 0.1186% |

The `240` prediction records and `240` mature outcome records are four horizons for the same 60 security-months. At the primary 126-session horizon there are exactly 60 predictions, 60 mature outcomes, and 60 evaluated observations.

## Data availability diagnostics

These checkpoints overlap and therefore are not subtracted as a nested funnel:

| Checkpoint | Security-months | Share of universe | Meaning |
| --- | ---: | ---: | --- |
| Exact positive close and adjusted close on prediction date | 50,600 | 100.0000% | Raw price presence is not the cause of the 60-row result. |
| At least one model-available fundamental fact | 44,129 | 87.2115% | A raw fact exists before the prediction timestamp. |
| At least one usable price-derived component | 47,568 | 94.0079% | `3,032` rows have prices but insufficient usable lookback features. |
| At least one usable fundamental-derived component | 32,634 | 64.4941% | Raw facts often do not satisfy TTM, growth, unit, or denominator requirements. |
| At least one usable component of both types | 31,026 | 61.3162% | Necessary but far from sufficient for score eligibility. |

### Unique-security cross-check

| Checkpoint | Unique securities |
| --- | ---: |
| Appeared in a point-in-time cohort | 636 |
| Exact prediction-date price | 636 |
| Model-available fundamental fact | 531 |
| Usable fundamental feature | 460 |
| Passed four-family minimum | 14 |
| Eligible final score / final evaluation | 5 |

## Exclusive disposition of every stock-month

Every one of the 50,600 expected rows has exactly one primary disposition:

| Primary reason code | Rows | Meaning |
| --- | ---: | --- |
| `BELOW_MINIMUM_AVAILABLE_FAMILIES` | 50,438 | Fewer than four factor families are available; this rule is checked first. |
| `BELOW_MINIMUM_COMPONENT_COVERAGE` | 102 | Four families are available, but fewer than 70% of applicable components are valid. |
| `INCLUDED_IN_FINAL_EVALUATION` | 60 | The row passes both score gates and has all predictions and mature outcomes. |

Each JSONL explanation also records price/fundamental diagnostics, family availability, component coverage, every missing component and its stored reason, prediction horizons, and outcome status. There are no unclassified stock-months.

## Component evidence behind the exclusions

| Stored component reason | Components |
| --- | ---: |
| `INSUFFICIENT_HISTORY` | 437,458 |
| `VALID` | 323,360 |
| `NOT_APPLICABLE` | 77,274 |
| `SOURCE_MISSING` | 65,063 |
| `SECTOR_UNKNOWN` | 58,194 |
| `INVALID_DENOMINATOR` | 51 |

The dominant failure is `INSUFFICIENT_HISTORY`, not missing prediction-date prices. `SECTOR_UNKNOWN` affects specialized accounting features, while `NOT_APPLICABLE` is the intentional sector mask and is excluded from the component-coverage denominator.

## Family availability

| Available-family pattern | Security-months |
| --- | ---: |
| `momentum+risk` | 43,857 |
| `momentum+risk+value` | 3,454 |
| `NONE` | 2,986 |
| `growth+momentum+risk+value` | 147 |
| `growth+momentum+risk` | 95 |
| `value` | 41 |
| `momentum+quality+risk+value` | 15 |
| `growth` | 4 |
| `growth+value` | 1 |

## Why the final result only contains Financials

All `60` eligible rows are labelled `Financials`; every other sector has zero eligible scores. The financial-sector applicability mask removes nine industrial-accounting components, leaving ten applicable components. Each eligible row has value, growth, momentum, and risk available, while quality is entirely `NOT_APPLICABLE`.

The 5 eligible securities are:

| Ticker | Classification | Industry code | Eligible months | First | Last |
| --- | --- | --- | ---: | --- | --- |
| `AMT` | `SEC_SIC_TO_GICS_V1` | `6798` | 18 | `2019-10-31` | `2021-03-31` |
| `EXR` | `SEC_SIC_TO_GICS_V1` | `6798` | 3 | `2020-02-28` | `2020-04-30` |
| `HIG` | `SEC_SIC_TO_GICS_V1` | `6331` | 9 | `2018-10-31` | `2020-03-31` |
| `REG` | `SEC_SIC_TO_GICS_V1` | `6798` | 15 | `2021-02-26` | `2022-04-29` |
| `VNO` | `SEC_SIC_TO_GICS_V1` | `6798` | 15 | `2019-02-28` | `2020-04-30` |

Four names (`AMT`, `EXR`, `REG`, and `VNO`) carry SIC industry code `6798`; `HIG` carries `6331`. Under the stored `SEC_SIC_TO_GICS_V1` classification they are labelled Financials, so the financial mask—not the contract's separate GICS REIT mask—is applied to the SIC 6798 names. This classification/treatment issue belongs in Sprint 9.6.

### Sector coverage

| Sector | Universe stock-months | Eligible scores | Coverage |
| --- | ---: | ---: | ---: |
| Financials | 8,586 | 60 | 0.6988% |
| Communication Services | 656 | 0 | 0.0000% |
| Consumer Discretionary | 6,072 | 0 | 0.0000% |
| Consumer Staples | 1,985 | 0 | 0.0000% |
| Energy | 1,277 | 0 | 0.0000% |
| Health Care | 3,795 | 0 | 0.0000% |
| Industrials | 12,077 | 0 | 0.0000% |
| Information Technology | 5,217 | 0 | 0.0000% |
| Materials | 923 | 0 | 0.0000% |
| Real Estate | 136 | 0 | 0.0000% |
| Unknown | 6,466 | 0 | 0.0000% |
| Utilities | 3,410 | 0 | 0.0000% |

## Why quintile 1 is empty

Only `43` of 102 months have any eligible score. Nonempty cohorts contain one to `4` securities; none contains five. The evaluator assigns `ceil(ascending_average_rank * 5 / cohort_size)`. With cohort sizes 1, 2, 3, and 4, the lowest-ranked security falls into quintile 5, 3, 2, and 2 respectively. Quintile 1 is therefore mathematically impossible in every evaluated month.

Reason code: `COHORT_TOO_SMALL_FOR_BOTTOM_QUINTILE`.

## Holdout-specific breadth

The frozen holdout contains `42` cohorts and `20,923` expected stock-months. Only `52` reach four families and `4` receive an eligible final score, for `0.0191%` coverage. Those four scores occur in January through April 2022; the other holdout months have none. This is why the locked holdout gates cannot be evaluated.

## Monthly cohort table

| Date | Universe | Exact price | Fundamental fact | Usable fundamental | Four families | Eligible score | Predictions | Evaluated 126d | Score coverage |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `2017-01-31` | 485 | 485 | 390 | 263 | 0 | 0 | 0 | 0 | 0.0000% |
| `2017-02-28` | 486 | 486 | 391 | 266 | 0 | 0 | 0 | 0 | 0.0000% |
| `2017-03-31` | 490 | 490 | 394 | 267 | 0 | 0 | 0 | 0 | 0.0000% |
| `2017-04-28` | 491 | 491 | 395 | 267 | 0 | 0 | 0 | 0 | 0.0000% |
| `2017-05-31` | 491 | 491 | 395 | 268 | 0 | 0 | 0 | 0 | 0.0000% |
| `2017-06-30` | 491 | 491 | 396 | 268 | 0 | 0 | 0 | 0 | 0.0000% |
| `2017-07-31` | 492 | 492 | 398 | 270 | 0 | 0 | 0 | 0 | 0.0000% |
| `2017-08-31` | 492 | 492 | 399 | 270 | 0 | 0 | 0 | 0 | 0.0000% |
| `2017-09-29` | 494 | 494 | 401 | 270 | 0 | 0 | 0 | 0 | 0.0000% |
| `2017-10-31` | 494 | 494 | 402 | 273 | 0 | 0 | 0 | 0 | 0.0000% |
| `2017-11-30` | 494 | 494 | 403 | 276 | 1 | 0 | 0 | 0 | 0.0000% |
| `2017-12-29` | 493 | 493 | 403 | 276 | 1 | 0 | 0 | 0 | 0.0000% |
| `2018-01-31` | 494 | 494 | 404 | 278 | 1 | 0 | 0 | 0 | 0.0000% |
| `2018-02-28` | 494 | 494 | 404 | 287 | 0 | 0 | 0 | 0 | 0.0000% |
| `2018-03-29` | 493 | 493 | 406 | 288 | 0 | 0 | 0 | 0 | 0.0000% |
| `2018-04-30` | 493 | 493 | 407 | 291 | 0 | 0 | 0 | 0 | 0.0000% |
| `2018-05-31` | 493 | 493 | 407 | 286 | 0 | 0 | 0 | 0 | 0.0000% |
| `2018-06-29` | 493 | 493 | 407 | 286 | 0 | 0 | 0 | 0 | 0.0000% |
| `2018-07-31` | 493 | 493 | 407 | 286 | 0 | 0 | 0 | 0 | 0.0000% |
| `2018-08-31` | 493 | 493 | 409 | 286 | 0 | 0 | 0 | 0 | 0.0000% |
| `2018-09-28` | 493 | 493 | 409 | 286 | 0 | 0 | 0 | 0 | 0.0000% |
| `2018-10-31` | 492 | 492 | 411 | 290 | 1 | 1 | 1 | 1 | 0.2033% |
| `2018-11-30` | 492 | 492 | 413 | 292 | 1 | 1 | 1 | 1 | 0.2033% |
| `2018-12-31` | 494 | 494 | 416 | 296 | 1 | 1 | 1 | 1 | 0.2024% |
| `2019-01-31` | 493 | 493 | 417 | 297 | 1 | 1 | 1 | 1 | 0.2028% |
| `2019-02-28` | 493 | 493 | 417 | 293 | 1 | 1 | 1 | 1 | 0.2028% |
| `2019-03-29` | 493 | 493 | 417 | 293 | 1 | 1 | 1 | 1 | 0.2028% |
| `2019-04-30` | 493 | 493 | 416 | 292 | 1 | 1 | 1 | 1 | 0.2028% |
| `2019-05-31` | 493 | 493 | 418 | 290 | 1 | 1 | 1 | 1 | 0.2028% |
| `2019-06-28` | 493 | 493 | 417 | 289 | 1 | 1 | 1 | 1 | 0.2028% |
| `2019-07-31` | 494 | 494 | 419 | 291 | 1 | 1 | 1 | 1 | 0.2024% |
| `2019-08-30` | 495 | 495 | 421 | 294 | 1 | 1 | 1 | 1 | 0.2020% |
| `2019-09-30` | 496 | 496 | 422 | 295 | 1 | 1 | 1 | 1 | 0.2016% |
| `2019-10-31` | 496 | 496 | 422 | 297 | 3 | 2 | 2 | 2 | 0.4032% |
| `2019-11-29` | 496 | 496 | 424 | 300 | 4 | 3 | 3 | 3 | 0.6048% |
| `2019-12-31` | 497 | 497 | 425 | 302 | 4 | 3 | 3 | 3 | 0.6036% |
| `2020-01-31` | 497 | 497 | 426 | 303 | 4 | 3 | 3 | 3 | 0.6036% |
| `2020-02-28` | 497 | 497 | 426 | 327 | 7 | 4 | 4 | 4 | 0.8048% |
| `2020-03-31` | 497 | 497 | 427 | 329 | 7 | 4 | 4 | 4 | 0.8048% |
| `2020-04-30` | 497 | 497 | 426 | 326 | 5 | 3 | 3 | 3 | 0.6036% |
| `2020-05-29` | 497 | 497 | 428 | 326 | 2 | 1 | 1 | 1 | 0.2012% |
| `2020-06-30` | 497 | 497 | 430 | 327 | 2 | 1 | 1 | 1 | 0.2012% |
| `2020-07-31` | 497 | 497 | 430 | 327 | 2 | 1 | 1 | 1 | 0.2012% |
| `2020-08-31` | 497 | 497 | 430 | 327 | 2 | 1 | 1 | 1 | 0.2012% |
| `2020-09-30` | 497 | 497 | 429 | 327 | 2 | 1 | 1 | 1 | 0.2012% |
| `2020-10-30` | 497 | 497 | 431 | 328 | 2 | 1 | 1 | 1 | 0.2012% |
| `2020-11-30` | 497 | 497 | 432 | 329 | 2 | 1 | 1 | 1 | 0.2012% |
| `2020-12-31` | 497 | 497 | 432 | 330 | 2 | 1 | 1 | 1 | 0.2012% |
| `2021-01-29` | 497 | 497 | 434 | 332 | 2 | 1 | 1 | 1 | 0.2012% |
| `2021-02-26` | 497 | 497 | 434 | 365 | 6 | 2 | 2 | 2 | 0.4024% |
| `2021-03-31` | 497 | 497 | 434 | 370 | 6 | 2 | 2 | 2 | 0.4024% |
| `2021-04-30` | 497 | 497 | 435 | 358 | 5 | 1 | 1 | 1 | 0.2012% |
| `2021-05-28` | 497 | 497 | 437 | 353 | 4 | 1 | 1 | 1 | 0.2012% |
| `2021-06-30` | 498 | 498 | 438 | 352 | 4 | 1 | 1 | 1 | 0.2008% |
| `2021-07-30` | 498 | 498 | 439 | 352 | 3 | 1 | 1 | 1 | 0.2008% |
| `2021-08-31` | 498 | 498 | 440 | 354 | 3 | 1 | 1 | 1 | 0.2008% |
| `2021-09-30` | 498 | 498 | 439 | 353 | 3 | 1 | 1 | 1 | 0.2008% |
| `2021-10-29` | 498 | 498 | 439 | 353 | 3 | 1 | 1 | 1 | 0.2008% |
| `2021-11-30` | 498 | 498 | 439 | 353 | 3 | 1 | 1 | 1 | 0.2008% |
| `2021-12-31` | 498 | 498 | 440 | 353 | 3 | 1 | 1 | 1 | 0.2008% |
| `2022-01-31` | 496 | 496 | 438 | 351 | 3 | 1 | 1 | 1 | 0.2016% |
| `2022-02-28` | 496 | 496 | 439 | 355 | 3 | 1 | 1 | 1 | 0.2016% |
| `2022-03-31` | 497 | 497 | 440 | 356 | 3 | 1 | 1 | 1 | 0.2012% |
| `2022-04-29` | 496 | 496 | 441 | 347 | 3 | 1 | 1 | 1 | 0.2016% |
| `2022-05-31` | 497 | 497 | 443 | 324 | 1 | 0 | 0 | 0 | 0.0000% |
| `2022-06-30` | 496 | 496 | 443 | 324 | 1 | 0 | 0 | 0 | 0.0000% |
| `2022-07-29` | 496 | 496 | 443 | 324 | 1 | 0 | 0 | 0 | 0.0000% |
| `2022-08-31` | 496 | 496 | 443 | 324 | 1 | 0 | 0 | 0 | 0.0000% |
| `2022-09-30` | 496 | 496 | 443 | 324 | 1 | 0 | 0 | 0 | 0.0000% |
| `2022-10-31` | 495 | 495 | 445 | 326 | 1 | 0 | 0 | 0 | 0.0000% |
| `2022-11-30` | 497 | 497 | 447 | 327 | 1 | 0 | 0 | 0 | 0.0000% |
| `2022-12-30` | 497 | 497 | 449 | 330 | 1 | 0 | 0 | 0 | 0.0000% |
| `2023-01-31` | 496 | 496 | 448 | 330 | 1 | 0 | 0 | 0 | 0.0000% |
| `2023-02-28` | 496 | 496 | 448 | 337 | 1 | 0 | 0 | 0 | 0.0000% |
| `2023-03-31` | 497 | 497 | 449 | 338 | 1 | 0 | 0 | 0 | 0.0000% |
| `2023-04-28` | 497 | 497 | 450 | 337 | 1 | 0 | 0 | 0 | 0.0000% |
| `2023-05-31` | 498 | 498 | 451 | 338 | 1 | 0 | 0 | 0 | 0.0000% |
| `2023-06-30` | 498 | 498 | 452 | 339 | 1 | 0 | 0 | 0 | 0.0000% |
| `2023-07-31` | 498 | 498 | 452 | 339 | 1 | 0 | 0 | 0 | 0.0000% |
| `2023-08-31` | 498 | 498 | 452 | 338 | 1 | 0 | 0 | 0 | 0.0000% |
| `2023-09-29` | 498 | 498 | 452 | 338 | 1 | 0 | 0 | 0 | 0.0000% |
| `2023-10-31` | 499 | 499 | 455 | 340 | 1 | 0 | 0 | 0 | 0.0000% |
| `2023-11-30` | 499 | 499 | 455 | 341 | 1 | 0 | 0 | 0 | 0.0000% |
| `2023-12-29` | 499 | 499 | 455 | 341 | 1 | 0 | 0 | 0 | 0.0000% |
| `2024-01-31` | 499 | 499 | 455 | 341 | 1 | 0 | 0 | 0 | 0.0000% |
| `2024-02-29` | 499 | 499 | 456 | 348 | 1 | 0 | 0 | 0 | 0.0000% |
| `2024-03-28` | 499 | 499 | 457 | 349 | 2 | 0 | 0 | 0 | 0.0000% |
| `2024-04-30` | 499 | 499 | 457 | 346 | 2 | 0 | 0 | 0 | 0.0000% |
| `2024-05-31` | 499 | 499 | 459 | 344 | 1 | 0 | 0 | 0 | 0.0000% |
| `2024-06-28` | 499 | 499 | 460 | 343 | 1 | 0 | 0 | 0 | 0.0000% |
| `2024-07-31` | 500 | 500 | 461 | 343 | 1 | 0 | 0 | 0 | 0.0000% |
| `2024-08-30` | 500 | 500 | 461 | 344 | 1 | 0 | 0 | 0 | 0.0000% |
| `2024-09-30` | 501 | 501 | 461 | 342 | 1 | 0 | 0 | 0 | 0.0000% |
| `2024-10-31` | 500 | 500 | 460 | 342 | 1 | 0 | 0 | 0 | 0.0000% |
| `2024-11-29` | 500 | 500 | 462 | 342 | 1 | 0 | 0 | 0 | 0.0000% |
| `2024-12-31` | 500 | 500 | 464 | 343 | 1 | 0 | 0 | 0 | 0.0000% |
| `2025-01-31` | 500 | 500 | 464 | 343 | 1 | 0 | 0 | 0 | 0.0000% |
| `2025-02-28` | 500 | 500 | 464 | 346 | 1 | 0 | 0 | 0 | 0.0000% |
| `2025-03-31` | 500 | 500 | 464 | 346 | 1 | 0 | 0 | 0 | 0.0000% |
| `2025-04-30` | 500 | 500 | 464 | 345 | 1 | 0 | 0 | 0 | 0.0000% |
| `2025-05-30` | 500 | 500 | 465 | 343 | 1 | 0 | 0 | 0 | 0.0000% |
| `2025-06-30` | 500 | 500 | 465 | 343 | 1 | 0 | 0 | 0 | 0.0000% |

## Explain any stock/month

The explanation ledger contains one row for every expected stock/month. A targeted lookup can be reproduced from the authoritative warehouse:

```bash
python pipelines/audit_sprint9_cohort_funnel.py \
  --explain REG --asof-date 2022-05-31 --explain-only
```

The returned row states the exclusive primary disposition and the exact component-level reasons. Ticker or permanent security ID may be used.

## Integrity checks

| Check | Passed |
| --- | --- |
| All Monthly Score Rows Match Point In Time Membership | `true` |
| All Security Months Have 19 Raw Features | `true` |
| All Security Months Have 19 Normalized Features | `true` |
| Normalized Component Total Matches | `true` |
| Every Security Month Has One Primary Disposition | `true` |
| Eligible Scores Equal Prediction Security Months | `true` |
| Prediction Records Cover All Four Horizons | `true` |
| Eligible Scores Equal Evaluated 126D Observations | `true` |

## Claims boundary

Sprint 8 evidence is not broad enough to support model promotion or investability conclusions.

This is an internal cohort and data-coverage audit. It does not establish predictive value, investability, outperformance, suitability, or investment advice. `claims_eligible=false` remains mandatory.

## Evidence provenance

| Artifact | SHA-256 |
| --- | --- |
| `reports/backtests/pit_multifactor_baseline_v1.json` | `5e1cca6e568599ee1f3badcc6bf051edc1155b15bfb0b408b17051c2b5f60612` |
| `reports/comparisons/price-vs-multifactor-v1.json` | `2963e3d618df41f4ef55b8c86e63ba505c56a117c8dba7ba2f4d757ffd25b80a` |
| `reports/reproducibility/sprint8-closure-v1.json` | `edce8123922a5157dc979ae9db34834a69fbe31b8a0aa1baf8ae132088fc064c` |
| `experiments/multifactor-holdout-lock-v1.json` | `3857baa255562a89862a39919b550004b8860d733e4990a361cd81473d23878f` |
| `docs/research/multifactor-baseline-v1.md` | `3a650dcf5d1837bd1a922b8b80b21210f63838e657b713b836eb30b37918f7c5` |
| `reports/data-audits/pit-fundamentals-v1.json` | `97a99d0ea830abf26aa50d2dd86f093f2d7ce6ac194d1194bbd4e62337eebbc9` |
| `data/raw/free-point-in-time/composite-equity-bundle-v1/manifest.json` | `8b39fe268b3414495f7a2f95fe00e7b76f4afc1f33cec961ef095f4495a90a6e` |
| `data/raw/free-point-in-time/sec-fundamentals-bundle-v1/manifest.json` | `0a1aef3b2527672ff3febb2702479fe86ddebd51f393ae5eede26007f805985f` |

The 21 GB pre-lock warehouse is intentionally bound by the deterministic normalization-run and security-month fingerprints recorded in the JSON, rather than duplicated into the report repository.
