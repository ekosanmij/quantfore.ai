# Point-in-Time Equity Bundle and Ingestion v1

This is the operational interface for Sprint 7.3. It accepts a licensed bulk
export from Sharadar or an equivalent provider after that export has been
mapped to five strict JSON arrays. It does not call a vendor API, embed a
credential, or imply that vendor rights have been obtained.

## Bundle layout

```text
vendor-export/
  manifest.json
  securities.json
  memberships.json
  prices.json
  corporate_actions.json
  delistings.json
```

`manifest.json` uses `schema_version=point-in-time-equity-bundle-v1` and must
contain:

```json
{
  "schema_version": "point-in-time-equity-bundle-v1",
  "created_at": "2026-07-02T10:00:00Z",
  "vendor": "Contracted vendor name",
  "license_tag": "internal-rights-register-key",
  "license_rights_confirmed": true,
  "license_evidence_uri": "private://legal/evidence-id",
  "vendor_identifier_type": "VENDOR_PERMANENT_ID",
  "audit_contract": {
    "expected_row_counts": {
      "securities": 842,
      "memberships": 913,
      "prices": 1842000,
      "corporate_actions": 12000,
      "delistings": 221
    },
    "monthly_membership_counts": {
      "2014-01": 500,
      "2025-12": 503
    },
    "independent_membership_samples": [
      {
        "as_of_date": "2014-01-31",
        "vendor_ids": ["vendor-id-1", "vendor-id-2"],
        "source_uri": "private://independent-reconciliation/2014-01-31",
        "source_sha256": "lowercase-sha256"
      }
    ]
  },
  "universe": {
    "universe_id": "sp500-pit-v1",
    "name": "Historical S&P 500",
    "version": "v1",
    "description": "Historical membership by effective date",
    "window_start": "2014-01-01",
    "window_end": "2025-12-31",
    "benchmark_vendor_id": "vendor-id-for-spy",
    "benchmark_excluded_from_rankings": true
  },
  "files": {
    "securities": {
      "path": "securities.json",
      "dataset": "vendor dataset/table and version",
      "source_uri": "private vendor export URI or request identity",
      "retrieved_at": "2026-07-02T10:00:00Z",
      "sha256": "lowercase SHA-256 of exact file bytes"
    }
  }
}
```

`files` must contain exactly `securities`, `memberships`, `prices`,
`corporate_actions`, and `delistings`, each with the five fields shown above.
The complete manifest hash should be retained outside the bundle and supplied
to the pipeline.

`expected_row_counts` must exactly match all five decoded arrays.
`monthly_membership_counts` must contain every month in the universe window.
At least three independently sourced membership samples are required, each
with exact permanent vendor IDs, a private evidence URI and evidence SHA-256.
These expectations are persisted and rechecked from the normalized database.

## Row shapes

- `securities`: `vendor_id`, `ticker`, `name`, optional `exchange`, `sector`,
  `industry`, `cik`, `active_from`, `active_to`, plus arrays `identifiers` and
  `ticker_aliases`. Identifier rows contain `identifier_type`,
  `identifier_value`, `valid_from`, `valid_to`, `is_permanent`. Alias rows
  contain `ticker`, optional `exchange`, `effective_from`, `effective_to`, and
  `announced_at`.

The top-level security `ticker` is a non-authoritative, non-unique display
label. Permanent identifiers establish identity. Different issuers may reuse a
ticker when their dated aliases do not overlap.
- `memberships`: `vendor_id`, `effective_from`, `effective_to`, `announced_at`.
- `prices`: `vendor_id`, `date`, raw `open/high/low/close/volume`, and adjusted
  `adj_open/adj_high/adj_low/adj_close/adj_volume`. Missing vendor values are
  JSON `null`, never synthesized.
- `corporate_actions`: `vendor_id`, `action_type`, `effective_date`,
  `announced_at`, nullable `cash_amount`, `currency`, `ratio_from`, `ratio_to`,
  `related_vendor_id`, and an object-valued `details`. Ratio fields occur as a
  pair.
- `delistings`: `vendor_id`, `delisting_date`, `announced_at`, nullable
  `delisting_return`, `return_available_at`, `successor_vendor_id`, and
  non-empty `reason`. A present return requires `return_available_at`.

Dates use `YYYY-MM-DD`; timestamps must include a timezone and normalize to
UTC. Unknown fields, duplicate JSON keys, unresolved vendor IDs, duplicate
business keys, invalid periods, unconfirmed rights, incomplete monthly counts,
insufficient independent samples, row-total mismatches, and hash mismatches are
hard failures.

## Run

```bash
python pipelines/ingest_point_in_time_equities.py /private/vendor-export \
  --database-url sqlite+pysqlite:///./quantfore_research.db \
  --raw-dir data/raw \
  --expected-manifest-hash <manifest-sha256>
```

The adapter validates every file before opening the database. It then freezes
the manifest and five exact payloads beneath `data/raw/point-in-time-equities/`
using content/retrieval-addressed names. Existing bytes must match exactly.
Database persistence is one transaction and every normalized key is
deterministic. Consequently:

- an interrupted/failed transaction can restart from already frozen bytes;
- replaying the same bundle inserts no duplicate rows;
- the same bundle ingested into clean databases produces the same normalized
  identifiers, lineage and business values; and
- security-master validation runs before commit, so membership overlap or
  ambiguous mappings roll back the whole database transaction.

Raw payloads and licence evidence remain private and Git-ignored. A successful
ingestion does not change `claims_eligible=false`.

## Dataset audit

After a successful licensed ingestion, generate the Sprint 7.4 evidence with:

```bash
python pipelines/audit_point_in_time_equities.py \
  --database-url sqlite+pysqlite:///./quantfore_research.db
```

The default outputs are `reports/data-audits/pit-equity-panel-v1.json` and
`.md`. The command exits non-zero when any hard failure exists. Calendar gaps,
revisions, retained pre/post-membership lookback prices, missing optional
delisting returns and corporate-action discontinuities are explicit review
findings; identity conflicts, membership overlaps, impossible OHLC, missing
inactive-security delistings, future observations and post-delisting prices are
hard failures. Both reports contain source lineage plus one historical-removal
and one delisting demonstration.

For `sp500-pit-v1`, monthly constituent counts outside 450–550 are hard
failures. The audit also requires exact vendor month/row totals and independent
sample agreement. Its `dataset_binding` freezes the membership content hash and
the exact universe, membership and per-security price snapshot IDs/hashes used
by the backtest.

Historical feature and prediction construction must use the guard specified in
`docs/data/point-in-time-leakage-guard-v1.md`.
