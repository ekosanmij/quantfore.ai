# US Equity Trial v0 — Independent Reconciliation

**PROTOTYPE REAL-DATA TRIAL — NOT ELIGIBLE FOR PERFORMANCE CLAIMS**

**Decision:** `CONDITIONAL_PASS`
**Generated:** `2026-07-02T06:58:22.409280Z`
**Primary vendor:** `Tiingo`

## Scope

The deterministic sample contains five equities and twenty XNYS sessions per equity. It includes documented split windows and volatile periods. Vendor values are compared as received; this workflow never repairs either source.

## Deterministic sample

| Ticker | Anchor | Event | Dates | Selection reason |
| --- | --- | --- | ---: | --- |
| AAPL | 2020-08-31 | split | 20 | 4-for-1 stock split effective date |
| NVDA | 2024-06-10 | split | 20 | 10-for-1 stock split effective date |
| META | 2022-02-03 | volatile_period | 20 | large post-earnings price move |
| XOM | 2020-03-16 | volatile_period | 20 | COVID-19 and oil-market volatility |
| JPM | 2023-03-13 | volatile_period | 20 | US regional-bank stress period |

## Rows received and accepted

- Primary rows received: 100
- Independent rows received: 100
- Matched rows accepted for comparison: 100
- Requested sample rows: 100

## Security coverage

| Ticker | Primary | Independent | Compared | Coverage | Missing sessions | Failed | Review | Status |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| AAPL | 20 | 20 | 20 | 100.0% | 0 | 0 | 20 | conditional_pass |
| NVDA | 20 | 20 | 20 | 100.0% | 0 | 0 | 20 | conditional_pass |
| META | 20 | 20 | 20 | 100.0% | 0 | 0 | 20 | conditional_pass |
| XOM | 20 | 20 | 20 | 100.0% | 0 | 0 | 20 | conditional_pass |
| JPM | 20 | 20 | 20 | 100.0% | 0 | 0 | 20 | conditional_pass |

## Price and adjustment differences

- Comparison exceptions: 100
- Adjustment differences requiring review: 100

| Ticker | Date | Status | Notes |
| --- | --- | --- | --- |
| AAPL | 2020-08-18 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; adjusted adj_open exceeds tolerance or is not comparable; adjusted adj_high exceeds tolerance or is not comparable; adjusted adj_low exceeds tolerance or is not comparable; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| AAPL | 2020-08-19 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; adjusted adj_open exceeds tolerance or is not comparable; adjusted adj_high exceeds tolerance or is not comparable; adjusted adj_low exceeds tolerance or is not comparable; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| AAPL | 2020-08-20 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; adjusted adj_open exceeds tolerance or is not comparable; adjusted adj_high exceeds tolerance or is not comparable; adjusted adj_low exceeds tolerance or is not comparable; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| AAPL | 2020-08-21 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; adjusted adj_open exceeds tolerance or is not comparable; adjusted adj_high exceeds tolerance or is not comparable; adjusted adj_low exceeds tolerance or is not comparable; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| AAPL | 2020-08-24 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; adjusted adj_open exceeds tolerance or is not comparable; adjusted adj_high exceeds tolerance or is not comparable; adjusted adj_low exceeds tolerance or is not comparable; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| AAPL | 2020-08-25 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; adjusted adj_open exceeds tolerance or is not comparable; adjusted adj_high exceeds tolerance or is not comparable; adjusted adj_low exceeds tolerance or is not comparable; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| AAPL | 2020-08-26 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; adjusted adj_open exceeds tolerance or is not comparable; adjusted adj_high exceeds tolerance or is not comparable; adjusted adj_low exceeds tolerance or is not comparable; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| AAPL | 2020-08-27 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; adjusted adj_open exceeds tolerance or is not comparable; adjusted adj_high exceeds tolerance or is not comparable; adjusted adj_low exceeds tolerance or is not comparable; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| AAPL | 2020-08-28 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; adjusted adj_open exceeds tolerance or is not comparable; adjusted adj_high exceeds tolerance or is not comparable; adjusted adj_low exceeds tolerance or is not comparable; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| AAPL | 2020-08-31 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; adjusted adj_open exceeds tolerance or is not comparable; adjusted adj_high exceeds tolerance or is not comparable; adjusted adj_low exceeds tolerance or is not comparable; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| AAPL | 2020-09-01 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; adjusted adj_open exceeds tolerance or is not comparable; adjusted adj_high exceeds tolerance or is not comparable; adjusted adj_low exceeds tolerance or is not comparable; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| AAPL | 2020-09-02 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; adjusted adj_open exceeds tolerance or is not comparable; adjusted adj_high exceeds tolerance or is not comparable; adjusted adj_low exceeds tolerance or is not comparable; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| AAPL | 2020-09-03 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; adjusted adj_open exceeds tolerance or is not comparable; adjusted adj_high exceeds tolerance or is not comparable; adjusted adj_low exceeds tolerance or is not comparable; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| AAPL | 2020-09-04 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; adjusted adj_open exceeds tolerance or is not comparable; adjusted adj_high exceeds tolerance or is not comparable; adjusted adj_low exceeds tolerance or is not comparable; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| AAPL | 2020-09-08 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; adjusted adj_open exceeds tolerance or is not comparable; adjusted adj_high exceeds tolerance or is not comparable; adjusted adj_low exceeds tolerance or is not comparable; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| AAPL | 2020-09-09 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; adjusted adj_open exceeds tolerance or is not comparable; adjusted adj_high exceeds tolerance or is not comparable; adjusted adj_low exceeds tolerance or is not comparable; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| AAPL | 2020-09-10 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; adjusted adj_open exceeds tolerance or is not comparable; adjusted adj_high exceeds tolerance or is not comparable; adjusted adj_low exceeds tolerance or is not comparable; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| AAPL | 2020-09-11 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; adjusted adj_open exceeds tolerance or is not comparable; adjusted adj_high exceeds tolerance or is not comparable; adjusted adj_low exceeds tolerance or is not comparable; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| AAPL | 2020-09-14 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; adjusted adj_open exceeds tolerance or is not comparable; adjusted adj_high exceeds tolerance or is not comparable; adjusted adj_low exceeds tolerance or is not comparable; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| AAPL | 2020-09-15 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; adjusted adj_open exceeds tolerance or is not comparable; adjusted adj_high exceeds tolerance or is not comparable; adjusted adj_low exceeds tolerance or is not comparable; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| NVDA | 2024-05-28 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| NVDA | 2024-05-29 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| NVDA | 2024-05-30 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| NVDA | 2024-05-31 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| NVDA | 2024-06-03 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| NVDA | 2024-06-04 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| NVDA | 2024-06-05 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| NVDA | 2024-06-06 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| NVDA | 2024-06-07 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| NVDA | 2024-06-10 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; raw volume exceeds tolerance or is not comparable; adjusted volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| NVDA | 2024-06-11 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| NVDA | 2024-06-12 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| NVDA | 2024-06-13 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| NVDA | 2024-06-14 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| NVDA | 2024-06-17 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| NVDA | 2024-06-18 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| NVDA | 2024-06-20 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| NVDA | 2024-06-21 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| NVDA | 2024-06-24 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| NVDA | 2024-06-25 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; raw volume exceeds tolerance or is not comparable; adjusted volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| META | 2022-01-21 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; adjusted adj_open exceeds tolerance or is not comparable; adjusted adj_high exceeds tolerance or is not comparable; adjusted adj_low exceeds tolerance or is not comparable; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| META | 2022-01-24 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; adjusted adj_open exceeds tolerance or is not comparable; adjusted adj_high exceeds tolerance or is not comparable; adjusted adj_low exceeds tolerance or is not comparable; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| META | 2022-01-25 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; adjusted adj_open exceeds tolerance or is not comparable; adjusted adj_high exceeds tolerance or is not comparable; adjusted adj_low exceeds tolerance or is not comparable; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| META | 2022-01-26 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; adjusted adj_open exceeds tolerance or is not comparable; adjusted adj_high exceeds tolerance or is not comparable; adjusted adj_low exceeds tolerance or is not comparable; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| META | 2022-01-27 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; adjusted adj_open exceeds tolerance or is not comparable; adjusted adj_high exceeds tolerance or is not comparable; adjusted adj_low exceeds tolerance or is not comparable; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| META | 2022-01-28 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; adjusted adj_open exceeds tolerance or is not comparable; adjusted adj_high exceeds tolerance or is not comparable; adjusted adj_low exceeds tolerance or is not comparable; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| META | 2022-01-31 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; adjusted adj_open exceeds tolerance or is not comparable; adjusted adj_high exceeds tolerance or is not comparable; adjusted adj_low exceeds tolerance or is not comparable; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| META | 2022-02-01 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; adjusted adj_open exceeds tolerance or is not comparable; adjusted adj_high exceeds tolerance or is not comparable; adjusted adj_low exceeds tolerance or is not comparable; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| META | 2022-02-02 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; adjusted adj_open exceeds tolerance or is not comparable; adjusted adj_high exceeds tolerance or is not comparable; adjusted adj_low exceeds tolerance or is not comparable; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |
| META | 2022-02-03 | review | raw open is unavailable from one source; raw high is unavailable from one source; raw low is unavailable from one source; raw close is unavailable from one source; adjusted adj_open exceeds tolerance or is not comparable; adjusted adj_high exceeds tolerance or is not comparable; adjusted adj_low exceeds tolerance or is not comparable; raw volume exceeds tolerance or is not comparable; adjustment factor exceeds tolerance or is not comparable |

Only the first 50 exceptions are shown; the JSON contains all 100.

## Failed securities

None.

## Blocking reasons

- None.

## Manual-review notes

- Alpha Vantage TIME_SERIES_DAILY_ADJUSTED was unavailable on the supplied free-tier key; Yahoo Chart API was selected as the independent reconciliation source.
- Yahoo raw OHLCV were intentionally left unavailable because the Chart API quote series is split-adjusted; adjusted close and adjusted-basis fields were compared without repairing either vendor.
- Yahoo terms/licensing were not independently verified for redistribution; this frozen export is restricted to internal reconciliation and is not a product or claims dataset.
- Vendor values were compared as received; no price or adjustment was repaired.

## Claims boundary

This reconciliation does not establish model validity, point-in-time universe validity, or investment performance. `claims_eligible=false`.
