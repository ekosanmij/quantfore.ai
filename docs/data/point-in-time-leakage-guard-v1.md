# Point-in-Time Leakage Guard v1

Sprint 7.5 makes historical eligibility and input availability executable
preconditions of feature and prediction construction.

## Enforced rules

For prediction timestamp T and prediction date D, every selected input must
prove:

```text
model_available_at <= T
membership_effective_from <= D
membership_effective_to is null or membership_effective_to >= D
price_date <= D
```

Membership `announced_at` is its availability timestamp. Ticker alias
`announced_at` is the earliest time that symbol mapping may be used. Daily
prices use the date-level market availability boundary frozen by the Sprint 7
contract. Vendor `retrieved_at` remains lineage metadata and is not substituted
for historical market availability.

Ticker lookup always uses dated `ticker_aliases`; `securities.ticker` is never
used as historical identity. An effective membership announced after T is
treated as an unavailable revision and fails rather than silently rewriting
the past.

`expected_point_in_time_cohort` reconstructs the complete historical member
set without filtering on present-day status. `validate_point_in_time_cohort`
then rejects missing, duplicate or ineligible security IDs. A company that
delists after D therefore remains mandatory in the cohort at D.

## Feature and score construction

The guarded feature path is available through
`construct_point_in_time_baseline_features`. The existing feature CLI enables
it with:

```bash
python pipelines/build_baseline_features.py FB \
  --asof-date 2020-06-30 \
  --universe-id sp500-pit-v1 \
  --prediction-timestamp 2020-06-30T23:59:59Z
```

The resulting `feature_sets.config_json.point_in_time` records the universe,
prediction timestamp, membership row, historical ticker alias, price count,
maximum price date and a canonical SHA-256 over all input evidence. Stored
features use the prediction timestamp as `available_at`.

Scoring a guarded feature set requires the same historical context:

```bash
python pipelines/build_baseline_score.py FB \
  --asof-date 2020-06-30 \
  --universe-id sp500-pit-v1 \
  --prediction-timestamp 2020-06-30T23:59:59Z
```

Scoring refuses point-in-time feature sets without the guard, evidence with a
different membership/alias/timestamp, feature rows available after T, and
feature as-of dates after D.

## Required adversarial cases

Automated tests inject and reject:

- a future constituent;
- a future price and a historical price with future availability;
- a ticker known only after a rename;
- a revised membership record announced after prediction time; and
- omission of a historically eligible company that later delisted.

The legacy Sprint 2 calculator still ignores unused future rows for backward
compatibility. Point-in-time research must enter through the guarded
constructor, which rejects unavailable candidate inputs before calculation.

The dynamic monthly consumer of this guard is specified in
`docs/research/point-in-time-dynamic-universe-backtest-v1.md`.
