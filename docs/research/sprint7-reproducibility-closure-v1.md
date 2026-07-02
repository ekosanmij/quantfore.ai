# Sprint 7 Reproducibility and Closure v1

Sprint 7.8 is implemented by `pipelines/close_point_in_time_sprint.py`. The
command rebuilds the licensed point-in-time bundle into two independent fresh
SQLite databases and closes the sprint only when the required evidence is
identical.

## Clean-worktree gate

The command's first action is a strict Git check. It refuses tracked changes,
staged changes, untracked files, submodule changes, a missing commit, or a path
that is not the checkout root. Ignored private data is not treated as source
code.

Commit all reviewed Sprint 7 code before running closure. A successful run
writes report artifacts, so those outputs may make the worktree dirty
afterward; review and commit them deliberately if licensing permits.

## Run

The bundle manifest hash and evidence timestamp are mandatory pins:

```bash
PYTHONPATH="$PWD:$PWD/packages/research" .venv/bin/python \
  pipelines/close_point_in_time_sprint.py \
  /private/vendor-export \
  --expected-manifest-hash <manifest-sha256> \
  --universe-id sp500-pit-v1 \
  --start-date 2015-01-01 \
  --end-date 2025-06-30 \
  --experiment-id pit_baseline_v0_1 \
  --evidence-timestamp 2026-07-02T10:00:00Z
```

The timestamp must be on or after the source retrieval and evaluation window.
It is used for both audits and outcome ledgers, eliminating `now()` from the
reproducibility comparison.

Each rebuild performs the complete licensed ingestion, dataset audit, leakage
guards, dynamic-universe feature construction, scoring, outcome evaluation,
and cohort coverage gate. Temporary databases and copied raw bytes are deleted
after comparison.

Before coverage is evaluated, the S&P 500 audit enforces the 450–550 monthly
plausibility range, exact vendor row/month totals, and at least three
independent historical membership samples. The backtest is then pinned to the
audit's membership hash and exact price snapshot binding.

## Required matches

Closure requires exact equality for:

- normalized universe-membership content hash;
- historically eligible security count for every monthly cohort;
- prediction count;
- outcome count;
- dataset audit decision;
- complete backtest metrics object; and
- SHA-256 of the canonical point-in-time backtest JSON.

The canonical audit hash is also compared as a stronger additional guard. A
single mismatch, audit hard failure, or cohort below 95% coverage exits nonzero
and publishes no passing closure artifacts.

The membership hash covers permanent security IDs, effective dates,
announcement availability, source snapshot IDs, and source hashes. Warehouse
insertion timestamps are excluded.

## Outputs

After both clean rebuilds match, the first run is published as the canonical
evidence:

```text
reports/data-audits/pit-equity-panel-v1.json
reports/data-audits/pit-equity-panel-v1.md
reports/backtests/pit_baseline_v0_1.json
reports/backtests/pit_baseline_v0_1.md
reports/backtests/pit_baseline_v0_1.lineage.json
reports/reproducibility/sprint7-closure-v1.json
reports/reproducibility/sprint7-closure-v1.md
```

The closure report records the clean commit, pinned vendor-manifest hash,
configuration, both values for every invariant, canonical audit/report/lineage
hashes, and the Sprint 7 Definition of Done statement.

`claims_eligible=false` remains in force. Reproducibility closes the data and
baseline engineering sprint; it does not establish investment efficacy.

## Current evidence state

The repository contains the tested closure machinery but no fabricated passing
closure report. A real closure decision requires the licensed historical
membership, price, corporate-action, delisting, and permanent-identifier bundle
defined by Sprint 7.3.
