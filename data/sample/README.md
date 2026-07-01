## Sample Data

`msft_prices.csv` is synthetic weekday-only data for local smoke tests.

It is not real MSFT market history. The rows are shaped like daily price observations so ingestion, feature calculation, and audit fields can be tested without depending on a market data vendor.

### Sprint 4 outcome dataset

`msft_spy_outcome_prices.csv` is entirely synthetic weekday-only data. All
prices, volumes, returns, and drawdowns are fictional and must not be treated
as real MSFT or SPY market history.

- Test prediction date: `2025-12-26` (present in the CSV and marked as
  `TEST_PREDICTION_DATE`).
- History: 253 sessions before the prediction date.
- Evaluation data: 127 aligned MSFT and SPY sessions after the prediction
  date.
- Entry date: `2025-12-29`.
- 126-session exit date: `2026-06-23`.
- Expected MSFT return: `0.12`.
- Expected SPY return: `0.07`.
- Expected excess return: `0.05`.
- Expected MSFT maximum drawdown: `-0.20`.

The `synthetic_warning` column repeats the warning on every row so the nature
of the data remains explicit when the CSV is copied or viewed independently.
