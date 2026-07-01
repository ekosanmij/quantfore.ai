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

### Sprint 5 synthetic backtest panel

`synthetic_backtest_prices.csv` contains 1,000 aligned weekday sessions for
the fictional securities `QF01` through `QF20` and the `SPY` benchmark. The
series use deterministic synthetic momentum, volatility, cycle and drawdown
patterns. They are engineering fixtures, not real or proof-grade market data.

Regenerate the file from the repository root:

```bash
python scripts/generate_synthetic_backtest_data.py
```

The generator uses the fixed seed `20250302`. Repeated runs produce identical
bytes and therefore the same SHA-256 hash. Every CSV row carries an explicit
synthetic-data warning and a fictional pattern label.

After ingesting the panel, run the historical prediction and outcome ledger:

```bash
python pipelines/ingest_prices_csv.py \
  data/sample/synthetic_backtest_prices.csv

python pipelines/run_baseline_backtest.py \
  --benchmark SPY \
  --start-date 2023-01-01 \
  --end-date 2024-12-31 \
  --horizon 126d \
  --frequency monthly \
  --experiment-id synthetic_baseline_v0_1
```

The runner writes canonical, deterministic JSON and Markdown metrics reports to
`reports/backtests/synthetic_baseline_v0_1.json` and
`reports/backtests/synthetic_baseline_v0_1.md`. These exclude database-generated
UUIDs, retrieval timestamps and storage paths, so identical code and CSV input
produce identical metrics reports across clean databases. A separate ignored
`synthetic_baseline_v0_1.lineage.json` records sorted prediction IDs, outcome
hashes and source snapshot IDs for same-database audit and idempotency. The
runner also registers the hypothesis, source hash, code commit, configuration,
synthetic data classification, claims restriction and JSON result URI in
`experiment_registry`.
