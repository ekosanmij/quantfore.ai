# Point-in-Time Fundamentals v1

```yaml
contract_version: point-in-time-fundamentals-v1
status: locked_pre_ingestion
claims_eligible: false
research_window: 2014-01-01/2025-12-31
fiscal_period_buffer_start: 2012-01-01
primary_vendor: Nasdaq Data Link Sharadar Core US Equities Bundle
primary_table: SHARADAR/SF1
reconciliation_source: SEC EDGAR Companyfacts and filing archives
security_master: sp500-pit-v1 permanent security_id
raw_snapshot_hash: assigned_from_exact_licensed_bytes_at_ingestion
licence_status: executed_rights_evidence_required_before_vendor_ingestion
```

## Purpose and gate

This contract freezes the point-in-time fundamental dataset used by the Sprint
8 multi-factor baseline. The primary source is the licensed
`SHARADAR/SF1` table from the Nasdaq Data Link Sharadar Core US Equities
Bundle. SEC EDGAR is an independent reconciliation source and is not silently
substituted for a missing vendor record.

The repository does not contain executed vendor rights evidence. No vendor
credentials, raw bytes, normalized rows, model result, or performance claim may
be treated as proof-grade until written rights cover internal storage,
historical backtesting, derived features and scores, ML training, commercial
use, audit retention, and the treatment of identifiers. This lock therefore
sets `claims_eligible=false`; it does not claim that the named dataset has been
purchased or licensed.

Changing the vendor, table, dimensions, window, availability policy, concept
map, unit policy, or identity policy requires a new reviewed contract version.

## Window and selected records

- Prediction dates are within `2014-01-01` through `2025-12-31`, inclusive.
- Fiscal periods ending on or after `2012-01-01` may be retained only as a
  two-year calculation buffer for growth, averages, and TTM construction.
  Buffer observations are not separate evaluation observations.
- The frozen raw extract includes every vendor revision delivered for the
  selected securities and dates. A latest-only extract is invalid.
- Annual, quarterly, and vendor-supplied trailing-twelve-month records are
  stored as `ANNUAL`, `QUARTERLY`, and `TTM`. They are not interchangeable.
- The ranked universe and permanent identifiers come from `sp500-pit-v1`.
  Ticker and company name are never fundamental join keys.
- A vendor record that cannot resolve unambiguously to one permanent
  `security_id` is quarantined and reported, not guessed or dropped.

## Availability semantics

All timestamps are UTC. Source precision is retained in the raw snapshot and
ingestion manifest; a date-only source value is never represented as if the
vendor supplied an intraday time.

| Field | Frozen meaning |
| --- | --- |
| `filed_at` | Filing date/time supplied by the vendor or regulator. It is not a proxy for vendor delivery. |
| `accepted_at` | SEC acceptance timestamp when evidenced; otherwise null. |
| `public_release_at` | Earliest independently evidenced public earnings release for the reported values; otherwise null. |
| `vendor_available_at` | Earliest historical vendor availability supplied by the licensed point-in-time feed. A date-only value is conservatively interpreted as end-of-day and marked as date precision in the snapshot manifest. |
| `model_available_at` | Earliest time the value may enter a model: the maximum known public/vendor timestamp, followed by the frozen operational lag below. |
| `source_snapshots.retrieved_at` | When Quantfore fetched the exact bytes. It never substitutes for historical availability. |

The v1 operational lag is one hour after timestamp-precision vendor
availability. A date-only availability becomes model-available at the open of
the next regular NYSE session. If neither a historical vendor timestamp nor a
defensible conservative date exists, `model_available_at` cannot be formed and
the row is ineligible. Every feature query must enforce
`model_available_at <= prediction_timestamp`.

## Fundamental definitions and units

`concept` is the original, case-preserving vendor concept. It is never edited.
`standardized_concept` is Quantfore's versioned mapping. Unmapped concepts stay
in the warehouse with an explicit `unmapped:<concept>` standardized value and
cannot feed a v1 feature.

| Standardized concept | Definition | Canonical unit |
| --- | --- | --- |
| `revenue` | Consolidated revenue/net sales for the period. | reporting currency |
| `gross_profit` | Revenue less cost of revenue, as reported. | reporting currency |
| `ebit` | Earnings before interest and tax; vendor standardized operating result only when its mapping is documented. | reporting currency |
| `net_income_common` | Income available to common shareholders after preferred dividends. | reporting currency |
| `diluted_eps` | Diluted earnings per common share for the period. | reporting currency/share |
| `cash_from_operations` | Net cash provided by operating activities. | reporting currency |
| `capital_expenditure` | Cash paid for property, plant and equipment, stored as a positive outflow magnitude. | reporting currency |
| `free_cash_flow` | `cash_from_operations - capital_expenditure`; vendor FCF is retained separately and must reconcile before use. | reporting currency |
| `total_assets` | Consolidated assets at period end. | reporting currency |
| `total_debt` | Current plus non-current interest-bearing debt. | reporting currency |
| `cash_and_equivalents` | Cash and cash equivalents; restricted cash excluded unless separately mapped. | reporting currency |
| `shareholders_equity` | Equity attributable to common shareholders. | reporting currency |
| `income_tax_expense` | Current plus deferred income tax expense for the period. | reporting currency |
| `pretax_income` | Income before income taxes for the period. | reporting currency |
| `diluted_shares` | Weighted-average diluted common shares for the period. | shares |

Original `value` and `unit` are stored exactly as normalized from the vendor
record. Scale conversions are deterministic and recorded in downstream feature
lineage; they do not overwrite the source fact. Currency conversion is not part
of v1. A ratio mixing currencies or incompatible units is missing. Percentages
are decimals (`0.25`, not `25`) in derived features; monetary values are not
silently divided into thousands or millions.

Quarterly flow concepts represent the single fiscal quarter. Annual concepts
represent the fiscal year. TTM flow concepts represent the latest four fiscal
quarters and must either be vendor-supplied as point-in-time TTM or constructed
from four eligible quarterly revisions with complete lineage. Instant balance
sheet concepts use the period-end value and are never summed.

## Warehouse record

Each row in `fundamentals` preserves:

| Field | Rule |
| --- | --- |
| `fundamental_id` | Immutable warehouse UUID. |
| `security_id` | Required FK to a permanent Sprint 7 security/share class. |
| `fiscal_period_end` | Source fiscal period end, not filing date. |
| `fiscal_year` | Issuer fiscal year label. |
| `fiscal_quarter` | `1`–`4` for quarterly records; null for annual records. |
| `period_type` | Exactly `ANNUAL`, `QUARTERLY`, or `TTM`. |
| `form_type` | Source filing form, preserving amendments such as `10-K/A` and `10-Q/A`. |
| `filing_accession` | Filing accession/document key supplied by the source. |
| `filed_at`, `accepted_at`, `public_release_at` | Distinct source/public timestamps as defined above. |
| `vendor_available_at`, `model_available_at` | Historical feed and model eligibility timestamps. |
| `revision_version` | Positive, monotonically increasing version within a source fact identity. |
| `concept` | Unmodified original vendor/XBRL concept. |
| `standardized_concept` | Versioned Quantfore mapping, separate from `concept`. |
| `value`, `unit` | Source numeric value and unit; no null-to-zero conversion. |
| `source_snapshot_id` | FK to the exact immutable raw retrieval. |
| `source_hash` | Copied SHA-256 hash; must equal the referenced snapshot hash. |

The legacy fields `metric`, `period_end`, `available_at`, and `accession_no`
remain temporarily as compatibility mirrors. They are not independent sources
of truth and new code must use the explicit Sprint 8 fields.

## Revision and restatement rules

- Fundamental rows are append-only. Update and delete operations are rejected.
- The source fact identity is security, fiscal period end, period type,
  original concept, and unit within a vendor series.
- The first historically delivered value has `revision_version=1`. Each later
  correction, restatement, or newly delivered value increments the version.
- `10-K/A`, `10-Q/A`, and equivalent amended forms always create new rows and
  revisions, even when the numeric value is unchanged.
- A later restatement never changes the earlier row, availability timestamp,
  accession, source link, or revision number.
- An as-of query selects only rows with
  `model_available_at <= prediction_timestamp`, then chooses the greatest
  eligible revision for the source fact identity. It never chooses the latest
  revision globally.
- Two vendors or SEC and the primary vendor remain separate source series.
  Reconciliation differences are findings; no source silently overwrites the
  other.

## Exact reconstruction and acceptance

Raw bytes are stored at the private `storage_uri` registered by
`source_snapshot_id` and hashed before decoding. A normalized row can be
located by its fact identity, `source_snapshot_id`, and `revision_version`; its
copied `source_hash` must match the raw snapshot. The ingestion manifest also
records vendor dataset version, query, page/partition, normalization code
commit, concept-map version, unit policy, and availability-precision flags.

The data model passes only when:

1. fresh schemas contain every field above and existing SQLite research stores
   receive an additive compatibility migration;
2. original and standardized concepts, all period types, amendments, and
   historical revisions remain separately queryable;
3. database constraints reject invalid period/revision values and future
   model availability relative to vendor availability;
4. the ORM rejects update and delete of a fundamental row;
5. copied source hashes are validated against `source_snapshots`; and
6. any value used by a prediction can be reconstructed exactly from the raw
   snapshot and eligible revision.

## Vendor-neutral ingestion and reconciliation

Sprint 8.3 uses an immutable directory bundle with schema version
`point-in-time-fundamentals-bundle-v1`. `manifest.json` freezes the vendor,
dataset, licence evidence reference, permanent identifier namespace, concept
map/version, vendor-to-canonical field map, retrieval timestamp, original
source URI, data-file path, and SHA-256. The facts file is a non-empty JSON
array. Arbitrary vendor column names are supported through the field map; the
canonical rules above remain unchanged.

The adapter verifies both hashes before parsing, rejects duplicate JSON keys,
out-of-range warehouse numerics, missing timestamps, non-contiguous or
out-of-order revisions, invalid annual/quarterly metadata, and amendments that
do not increment `revision_version`. Ingestion resolves every vendor ID through
a dated permanent Sprint 7 `security_identifiers` row before opening the
transaction. Missing or ambiguous identities reject the complete bundle and
are written to the ingestion report. Raw data and manifest bytes are copied to
content-addressed immutable paths; rerunning identical bytes reuses the same
rows, while conflicting bytes or normalized values fail.

```bash
python pipelines/ingest_point_in_time_fundamentals.py /private/vendor-bundle \
  --expected-manifest-hash <sha256> \
  --database-url sqlite+pysqlite:///./quantfore_research.db
```

SEC Companyfacts remains independent evidence. Its pipeline now prefers CIK
and permanent identifier resolution over ticker matching, preserves SEC
concepts, classifies discrete quarterly/annual contexts, rejects unsupported
YTD contexts, and records amendments/restatements as later revisions. Because
Companyfacts does not prove historical licensed-vendor availability, SEC facts
become model-visible only at their actual retrieval timestamp and are not
substituted into the primary feature dataset.

```bash
python pipelines/ingest_sec_companyfacts.py MSFT --cik 0000789019
```

The audit checks duplicate fact identities, timestamp completeness and order,
prediction-date availability, source hashes, original/standardized concepts,
unit conflicts, fiscal mapping, revision/restatement order, permanent identity,
balance-sheet plausibility, cash-flow reconciliation, zero denominators, and
extreme ratios. It deterministically pairs primary facts with registered SEC
facts when no manually reviewed reconciliation file is supplied. The hard gate
requires at least 30 distinct issuer-periods spanning all 11 sectors. Numeric
or unit differences are retained as review findings with both source links;
they never repair the primary value.

```bash
python pipelines/audit_point_in_time_fundamentals.py \
  --source-snapshot-id <primary-fundamental-snapshot-id> \
  --database-url sqlite+pysqlite:///./quantfore_research.db
```

Default outputs are
`reports/data-audits/pit-fundamentals-v1.json` and its Markdown rendering. The
CLI exits non-zero for any hard failure but still writes every unresolved
difference. `--allow-incomplete-reconciliation` exists only for local adapter
development and cannot satisfy Sprint 8 acceptance.

No passing schema or audit changes `claims_eligible=false`. Promotion requires
the separately frozen research gates and compliance approval.

Feature construction is a hard audit consumer. Its CLI requires the audit JSON
and expected SHA-256, requires decision `pass`, and recomputes selected source
IDs and hashes, fact hash, availability/revision hash, and counts against the
warehouse. Every feature set stores that binding. Failed, stale, modified, or
wrong-snapshot audit evidence cannot feed normalization.
