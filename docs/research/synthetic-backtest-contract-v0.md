# Synthetic Baseline Backtest Contract v0

This contract governs the Sprint 5 historical signal backtest. The Python
source of truth is `quantfore_research.backtest.BACKTEST_CONTRACT`; pipelines,
analytics and reports must consume that contract rather than copy its values.

## Configuration

| Rule | Contract value |
| --- | --- |
| Ranked universe | `QF01` through `QF20` |
| Price panel | `QF01` through `QF20`, plus `SPY` |
| Benchmark | `SPY` |
| Frequency | Monthly |
| Rebalance session | Final available trading session of each month |
| Minimum feature history | 253 sessions dated on or before prediction |
| Evaluation requirement | 127 sessions dated after prediction |
| Horizon | `126d` (126 trading intervals) |
| Model | `baseline_v0.1` |
| Minimum test periods | 12 monthly prediction dates |
| Reproducibility | Deterministic results required |

`SPY` is required for benchmark outcomes but must never enter the
cross-sectional security ranking.

## Temporal Boundaries

A feature input may contain only price observations dated on or before its
prediction date. A security is eligible for prediction only when at least 253
such observations are available.

An outcome input may contain only observations after its prediction date. A
prediction is mature only when 127 future observations are available: an entry
observation followed by 126 trading intervals to the exit observation.

The monthly rebalance date is selected from dates that are actually present in
the aligned price panel. Calendar month-end dates must not be invented when no
trading session exists on that date.

## Scope Warning

This is an engineering backtest over synthetic data. Its output is not evidence
of real-market validity and is not eligible to support investment-performance
claims.
