# Sprint 6 versus Point-in-Time Comparison v1

Sprint 7.7 is implemented by `pipelines/compare_static_vs_point_in_time.py`.
It produces a canonical JSON evidence document and a human-readable Markdown
report. Both remain `claims_eligible=false`.

## Evidence contract

The comparison does not trust aggregate metrics copied from either input
report. It:

1. validates the Sprint 6 and point-in-time report/lineage identities;
2. requires the point-in-time coverage gate to have passed;
3. requires the feature version, horizon, frequency, and benchmark to match;
4. reloads every lineage prediction and outcome from the research databases;
5. refuses generation when stored outcome hashes differ from the lineages; and
6. recomputes both sides on their shared prediction dates only.

Dates present on only one side are excluded and listed in
`comparison_window`. This prevents period selection from being mistaken for a
dataset effect.

## Run

Use separate database URLs when the Sprint 6 and point-in-time evidence were
built in separate clean databases:

```bash
PYTHONPATH="$PWD:$PWD/packages/research" .venv/bin/python \
  pipelines/compare_static_vs_point_in_time.py \
  --static-database-url 'sqlite+pysqlite:////absolute/path/sprint6.db' \
  --pit-database-url 'sqlite+pysqlite:////absolute/path/sprint7-pit.db' \
  --static-report reports/backtests/real_price_baseline_trial_v0_1.json \
  --static-lineage reports/backtests/real_price_baseline_trial_v0_1.lineage.json \
  --pit-report reports/backtests/pit_baseline_v0_1.json \
  --pit-lineage reports/backtests/pit_baseline_v0_1.lineage.json
```

Defaults are:

- `reports/backtests/sprint6-vs-pit-v1.json`
- `reports/backtests/sprint6-vs-pit-v1.md`

If a database URL is omitted, the normal `QUANTFORE_DATABASE_URL` setting is
used. Separate URLs are recommended because clean rebuilds may intentionally
store the two experiment lineages independently.

## Diagnostics and definitions

The report contains every Sprint 7.7 diagnostic:

- mean and median monthly Spearman Rank IC;
- a t-statistic based on six-month-spaced, non-overlapping 126-session ICs;
- returns and counts for score quintiles 1 through 5;
- quintile 5 minus quintile 1 excess-return spread;
- the same summary grouped by calendar year and security sector;
- equal-weight top-quintile turnover, with initial deployment counted as 100%;
- top-quintile net excess returns after turnover-scaled round-trip costs of 10,
  25, and 50 bps;
- security-window maximum drawdown summaries and top-quintile downside capture;
- count, return, and overall-mean contribution of explicit delisting outcomes;
  and
- monthly static-only, PIT-only, symmetric-difference, and Jaccard universe
  diagnostics.

Downside capture is the mean top-quintile realised return during periods with a
negative mean benchmark return, divided by the corresponding mean benchmark
return and expressed as a percentage. A value is `null` when the required
observations do not exist; it is never silently imputed.

## Completion rule

Generation succeeds when the evidence is complete and consistent. Positive
Rank IC, spreads, cost-adjusted returns, or stability are not pass conditions.
An adverse or statistically insignificant point-in-time result is valid Sprint
7.7 evidence.

The repository does not ship a fabricated comparison output. The canonical
report can be generated after a licensed point-in-time export has passed the
Sprint 7.3–7.6 ingestion, audit, leakage, coverage, and backtest gates.
