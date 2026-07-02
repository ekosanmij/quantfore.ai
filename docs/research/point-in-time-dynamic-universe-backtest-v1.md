# Point-in-Time Dynamic-Universe Baseline v1

Sprint 7.6 reruns the unchanged baseline over historical monthly membership
rather than the retrospective Sprint 6 CSV.

## Frozen model

- Model version: `baseline_v0.1`
- Frequency: monthly, final available benchmark session
- Horizon: `126d`
- Minimum feature history: 253 adjusted-close observations
- Features and scoring weights are unchanged:
  - `momentum_6_1`
  - `momentum_12_1`
  - `return_21d`
  - `volatility_126d`

SPY is loaded through `universe_definitions.benchmark_security_id` and is never
included in ranked membership cohorts.

## Cohort construction

For every monthly prediction timestamp, the runner calls the Sprint 7.5
leakage guard to reconstruct all effective, then-known memberships and their
historical ticker aliases. It validates the complete cohort before feature
construction. Current listing status is not a filter: a company that delists
later remains in every earlier eligible cohort.

Each cohort records:

- expected security IDs;
- securities with complete features;
- securities with complete realized outcomes;
- evaluated coverage;
- delisting outcomes;
- entry and exit dates;
- membership, ticker, price and delisting source lineage; and
- every exclusion with a stage, stable reason code and human-readable detail.

Supported exclusion codes include `INSUFFICIENT_HISTORY`,
`MISSING_FEATURE_DATA`, `ENTRY_UNAVAILABLE`, `EXIT_UNAVAILABLE`,
`MISSING_OUTCOME_DATA`, `BENCHMARK_EXIT_UNAVAILABLE`,
`DELISTING_RETURN_UNAVAILABLE` and `INVALID_DELISTING_RETURN`.

The gate is applied independently to every cohort:

```text
evaluated_members / expected_members >= 0.95
```

The CLI writes reports even when the gate fails, then exits non-zero. This
preserves a machine-readable explanation instead of hiding weak coverage in an
average across months.

## Delisting outcomes

If a security delists inside the 126-session outcome window, the terminal
security value is:

```text
last_adjusted_close * (1 + delisting_return)
```

The benchmark is measured from the normal entry session through its last
session on or before the delisting date. The outcome is tagged `delisting` and
retains the delisting event ID, return and source snapshot. A missing terminal
return is never imputed to zero; it becomes
`DELISTING_RETURN_UNAVAILABLE`.

## Run

The audited dataset is mandatory. Before constructing a feature, the runner
validates the audit's membership hash and exact universe, membership and
per-security price snapshot bindings against the database. Later or larger
snapshots are ignored until a new audit binds them:

```bash
python pipelines/run_point_in_time_backtest.py \
  --database-url sqlite+pysqlite:///./quantfore_research.db \
  --universe-id sp500-pit-v1 \
  --start-date 2015-01-01 \
  --end-date 2025-06-30 \
  --experiment-id pit_baseline_v0_1 \
  --audit-json reports/data-audits/pit-equity-panel-v1.json
```

Default outputs are:

```text
reports/backtests/pit_baseline_v0_1.json
reports/backtests/pit_baseline_v0_1.md
reports/backtests/pit_baseline_v0_1.lineage.json
```

The JSON and Markdown contain cohort coverage and all exclusions. The lineage
manifest contains deterministic membership, prediction, outcome, audit and
source-snapshot references. `claims_eligible=false` remains mandatory.
