# Point-in-Time US Equity Panel v1

```yaml
contract_version: pit-equity-panel-v1
dataset_kind: proof_candidate_point_in_time
claims_eligible: false
window_start: 2014-01-01
window_end: 2025-12-31
membership_universe: historical_sp_500_constituents_by_effective_date
benchmark: SPY
benchmark_rank_eligible: false
preferred_source: Nasdaq Data Link / Sharadar Core US Equities Bundle
source_snapshot_ids: assigned_during_ingestion
source_hashes: assigned_from_exact_raw_bytes_during_ingestion
licence_status: procurement_and_written_rights_confirmation_required_before_ingestion
publication_status: contract_and_aggregate_derived_audits_only
```

## Purpose and hard gate

This contract freezes the inputs and interpretation of Quantfore's first
survivorship-aware US equity research panel. It replaces the retrospective
Sprint 6 trial universe. No ingestion may begin until the vendor package and
written licence evidence satisfy the rights gate below. Because those rights
have not been evidenced in this repository, this contract does **not** claim
that Sharadar data has been licensed. `claims_eligible=false` remains mandatory
until Sprint 7 validation and reproducibility gates pass.

The preferred source is the Nasdaq Data Link / Sharadar bundle identified in
`docs/data/data-vendor-matrix.md`. An equivalent source may replace it only by
creating a new reviewed contract version; silently mixing vendors or changing
the universe methodology is prohibited.

## Frozen universe and dates

- The observation window is inclusive: `2014-01-01` through `2025-12-31`.
  Rows before or after it are rejected, except earlier alias/listing history
  retained solely to resolve an in-window security.
- The ranked universe on date D is the set of securities whose historical S&P
  500 membership has `effective_from <= D` and either no `effective_to` or
  `effective_to >= D`. Both boundaries are inclusive and use US market dates.
- Membership is stored at security/share-class level. A company with multiple
  listed classes contributes only the classes explicitly present in the source
  membership history. Membership is never inferred from current constituents,
  price availability, market capitalisation, or later company status.
- Removed, acquired, merged, bankrupt, liquidated, inactive and delisted
  securities remain in history. Removal or delisting closes a dated interval;
  it never deletes the security or its prior observations.
- SPY is the sole benchmark. It must have prices across the window and a
  permanent `security_id`, but it is not a universe member and is always
  excluded from cross-sectional rankings, quintiles and coverage denominators.
- Securities without sufficient history or valid prices are not removed from
  the universe. Downstream cohorts must retain them as exclusions with a
  machine-readable reason.
- Weekends and exchange holidays do not shift membership dates. A downstream
  monthly cohort uses membership effective on its defined prediction date; its
  trading-date policy is separately frozen by the backtest contract.

## Identity rules

`securities.security_id` is Quantfore's permanent, opaque security identifier.
`securities.ticker` is a non-authoritative, non-unique convenience label;
historical resolution uses permanent identifiers and dated aliases.
It is independent of ticker, company name and vendor. Once assigned it is never
reused or changed. It identifies a listed security/share class rather than an
issuer; a genuinely different share class receives a different `security_id`.

Vendor permanent identifiers (preferably Sharadar `permaticker`), CIK, FIGI,
CUSIP or other identifiers are dated in `security_identifiers`. Identifier type
and value comparisons are trimmed and case-insensitive. An identifier may map
to only one security at a point in time. CUSIP values must be stored only if the
licence expressly permits storage and derived use; otherwise their absence is
expected and must not be backfilled from an unlicensed source.

Tickers are aliases, not identity. Each ticker has inclusive effective dates
and an `announced_at` timestamp. A rename (for example `FB` to `META`) adds a
new `ticker_aliases` row linked to the existing `security_id`; it must not
create a new security. Reuse of the same ticker by a genuinely different
security is allowed only in non-overlapping periods. Overlapping ticker-to-
security mappings are a hard validation failure. The display ticker on
`securities` is deliberately non-unique; recycled historical symbols are
represented in `ticker_aliases`, and identity logic must never join on the
display ticker.

## Security-master field dictionary

All timestamps are UTC ISO 8601 values. All dates are ISO `YYYY-MM-DD` market
dates. Empty strings are invalid; unknown optional values are SQL `NULL`.
Every foreign key is mandatory unless explicitly marked optional.

### `source_snapshots`

| Field | Rule |
| --- | --- |
| `snapshot_id` | Immutable Quantfore UUID for one exact vendor retrieval/snapshot. |
| `vendor` | Contracted provider name, exactly as recorded in the ingestion manifest. |
| `dataset` | Vendor table/feed and version; membership, prices and actions use separately identifiable snapshots. |
| `retrieved_at` | Actual successful UTC retrieval time, not a data effective date. |
| `license_tag` | Internal key linking the snapshot to retained rights evidence. |
| `hash` / ORM `source_hash` | Lowercase SHA-256 of exact raw bytes. |
| `storage_uri` | Unique private immutable raw location; never a public URL. |
| `created_at`, `updated_at` | Warehouse metadata only; neither establishes market availability. |

### `securities`

| Field | Rule |
| --- | --- |
| `security_id` | Permanent Quantfore UUID for one listed security/share class; joins use this value. |
| `ticker` | Unique legacy display symbol, never an identity or historical-universe key. |
| `name` | Display name; a rename does not change identity. |
| `exchange`, `sector`, `industry`, `cik` | Optional descriptive/current values. Historical research must use dated source records rather than assume these values applied in the past. |
| `active_from`, `active_to` | Optional inclusive listing/activity boundaries, not S&P 500 membership dates. |
| `created_at`, `updated_at` | Warehouse metadata. |

### `security_identifiers`

| Field | Rule |
| --- | --- |
| `identifier_id` | Immutable Quantfore UUID for the row. |
| `security_id` | Required FK to exactly one permanent `securities` row. |
| `identifier_type` | Normalized namespace such as `SHARADAR_PERMATICKER`, `CIK`, `FIGI`, or licensed `CUSIP`. |
| `identifier_value` | Vendor/regulator value; unique to one security for overlapping validity dates. |
| `valid_from`, `valid_to` | Inclusive validity interval; `valid_to=NULL` means open-ended and may not precede `valid_from`. |
| `is_permanent` | True only for a vendor-defined non-reassignable identifier. |
| `source_snapshot_id`, `source_hash` | Immutable source lineage; hash must equal the referenced snapshot hash. |
| `created_at` | UTC warehouse insertion timestamp; never used as market availability. |

### `ticker_aliases`

| Field | Rule |
| --- | --- |
| `ticker_alias_id` | Immutable Quantfore UUID. |
| `security_id` | Permanent security; renames retain the same value. |
| `ticker` | Trimmed, case-insensitive US market symbol as supplied; not a permanent key. |
| `exchange` | Optional source exchange/venue label. |
| `effective_from`, `effective_to` | Inclusive ticker validity; open-ended when `effective_to=NULL`. |
| `announced_at` | Earliest evidenced UTC time the alias/rename was known. A retrieval time must not substitute for an unknown announcement time. |
| `source_snapshot_id`, `source_hash` | Immutable source lineage. |
| `created_at` | Warehouse insertion time. |

### `universe_definitions`

| Field | Rule |
| --- | --- |
| `universe_id` | Stable key `sp500-pit-v1`. |
| `name`, `version`, `description` | Human-readable frozen methodology; `(name, version)` is unique. |
| `window_start`, `window_end` | Inclusive contract window, exactly `2014-01-01` and `2025-12-31` for v1. |
| `benchmark_security_id` | FK to the permanent SPY security. |
| `benchmark_excluded_from_rankings` | Must be true for v1. |
| `source_snapshot_id`, `source_hash` | Lineage to the exact membership-universe snapshot/config bytes. |
| `created_at` | Warehouse insertion time. |

### `universe_memberships`

| Field | Rule |
| --- | --- |
| `membership_id` | Immutable Quantfore UUID. |
| `universe_id` | Required FK to `sp500-pit-v1`. |
| `security_id` | Required FK resolving to exactly one permanent security. |
| `effective_from`, `effective_to` | Inclusive historical membership; `effective_to=NULL` is open-ended. Periods for one universe/security must not overlap. |
| `announced_at` | Earliest evidenced UTC publication/announcement time. It controls when the membership fact may be used; it is not silently replaced by `effective_from`. |
| `source_snapshot_id` | Required FK to the immutable raw membership snapshot. |
| `source_hash` | SHA-256 of that exact snapshot, matching `source_snapshots.source_hash`. |
| `created_at` | Warehouse insertion time. |

### `corporate_actions`

| Field | Rule |
| --- | --- |
| `corporate_action_id` | Immutable Quantfore UUID. |
| `security_id` | Security affected by the action. |
| `action_type` | Vendor value normalized to a documented class, including split, cash dividend, stock dividend, merger, spin-off, symbol change or liquidation. Unknown classes are retained, not discarded. |
| `effective_date` | Ex/effective market date; never the ingestion date. |
| `announced_at` | Earliest evidenced UTC availability time. |
| `cash_amount`, `currency` | Optional non-negative cash amount and ISO currency for cash actions. |
| `ratio_from`, `ratio_to` | Optional positive terms for ratio actions; both are required together by ingestion validation. |
| `related_security_id` | Optional permanent security produced by or involved in the action. |
| `details_json` | Source-specific terms that cannot be losslessly normalized. |
| `source_snapshot_id`, `source_hash`, `created_at` | Immutable lineage and insertion time. |

### `delisting_events`

| Field | Rule |
| --- | --- |
| `delisting_event_id` | Immutable Quantfore UUID. |
| `security_id` | Delisted security; its historical rows remain queryable. |
| `delisting_date` | Last source-defined listing/trading date, using the vendor definition recorded in the ingestion manifest. |
| `announced_at` | Earliest evidenced UTC time the delisting was known. |
| `delisting_return` | Optional decimal total return (for example `-1.0` for total loss), never assumed to be zero when missing. |
| `return_available_at` | UTC time the terminal return became available; required when a return is present. |
| `reason` | Non-empty vendor reason/status, including acquisition, bankruptcy or exchange removal. |
| `successor_security_id` | Optional permanent successor/acquirer security. |
| `source_snapshot_id`, `source_hash`, `created_at` | Immutable lineage and insertion time. |

### `prices`

| Field | Rule |
| --- | --- |
| `price_id` | Immutable Quantfore UUID for the normalized observation. |
| `security_id` | Permanent security FK; ticker joins are forbidden. |
| `date` | US market session date inside the inclusive contract window. |
| `open`, `high`, `low`, `close` | Optional raw vendor prices on a single consistent basis. Missing values remain null. |
| `adj_open`, `adj_high`, `adj_low`, `adj_close` | Optional vendor-adjusted prices. They may not be internally synthesized without a newly versioned methodology. |
| `volume`, `adj_volume` | Optional raw and vendor-adjusted volume; non-trading null is distinct from reported zero. |
| `source_snapshot_id` | Required immutable raw price snapshot FK. `(security_id, date, source_snapshot_id)` is unique. |
| `created_at`, `updated_at` | Warehouse metadata; normalized revisions use a new snapshot rather than overwriting business values. |

## Prices, adjustments and terminal outcomes

- Daily raw and vendor-adjusted open, high, low, close, volume and adjusted
  volume are required where supplied. Raw values are never overwritten by
  adjusted values or vice versa.
- Every normalized price references an immutable `source_snapshot_id`. The raw
  response hash, vendor identifier, dataset/table name, exact query, retrieval
  timestamp and storage URI are retained in the snapshot/ingestion manifest.
- Splits and dividends are stored independently in `corporate_actions`, even
  when adjustment factors are embedded in prices. Vendor adjustment methodology
  and restatement behaviour must be captured in the snapshot manifest.
- Delisted securities require the final available price, delisting date and
  delisting return where the licensed source supplies one. A missing delisting
  return remains null and becomes an audit finding; it is never imputed to zero.
- Prices before listing, after delisting, outside the frozen window, with
  impossible OHLC relationships, or inconsistent with a split/dividend are
  audit inputs and cannot be silently repaired.

## Availability, retrieval and revision rules

`effective_*` says when a fact applies. `announced_at` or
`return_available_at` says when it was knowable. `source_snapshots.retrieved_at`
says when Quantfore fetched the bytes. These are distinct and must never be
substituted for one another.

The canonical retrieval timestamp is the actual UTC completion time of each
vendor request/page and cannot be frozen before ingestion. It must be written
to `source_snapshots.retrieved_at`; placeholder, local-time and date-only
values fail ingestion. Each revised vendor delivery creates a new immutable
snapshot rather than updating an earlier one. Normalization from identical raw
bytes must produce identical business fields; warehouse UUIDs and insertion
timestamps are excluded from content hashes.

A model may consume a record only when its availability timestamp is no later
than the prediction timestamp. Later corrections and revised histories remain
separate snapshots and cannot rewrite an earlier as-known dataset.

## Hash and freeze rules

Raw bytes are hashed with SHA-256 without decoding, reformatting, decompression
or row reordering. Each request/page gets a source hash. A dataset-manifest hash
is SHA-256 over canonical UTF-8 JSON containing the ordered source hashes,
queries, vendor dataset versions, retrieval timestamps and normalization-code
commit. Membership and normalized rows copy the exact referenced source hash.

The source hashes are intentionally recorded as `assigned_during_ingestion` in
this pre-ingestion contract: inventing them would defeat the audit trail. Once
the first accepted ingestion completes, its immutable manifest URI and hash
must be added to a reviewed v1 freeze record before any backtest runs. Any
change to dates, membership rules, benchmark, source package, raw hashes,
identifier policy or adjustment methodology creates a new dataset version.

The accepted bundle also freezes exact row totals, one constituent count for
every month, and at least three independently sourced historical membership
samples. The S&P 500 audit rejects counts outside 450–550, vendor-total
mismatches, independent-sample mismatches, and missing expectation contracts.
Having merely one member on every exchange session is not completeness.

A passing audit contains a `dataset_binding`: the normalized membership hash
and exact snapshot IDs/source hashes for the universe, memberships and each
security's selected price history. The backtest must validate and use this
binding; it cannot silently select a newer or larger snapshot afterward.

## Licensing and publication boundary

Before credentials or data are used, Quantfore must retain written evidence of
rights for: internal storage; historical/backtesting use; derived data and
signals; ML training if later proposed; commercial/product use if later
proposed; audit retention after termination; and the treatment of identifiers
such as CUSIP. Procurement must also record user/seat limits, retention period,
geography, attribution, display, redistribution and termination/deletion terms.

Unless the executed agreement explicitly says otherwise:

- raw vendor responses, row-level normalized data, identifier mappings,
  constituent lists and vendor documentation are confidential, Git-ignored and
  must not be published, redistributed or exposed through a product;
- only code, this contract, aggregate non-reconstructable counts/metrics and
  manually reviewed audit summaries may enter the repository;
- derived-data rights are not assumed to include publishing constituent-level
  scores, trained weights, excerpts or reconstruction-capable outputs;
- audit retention and reproducibility copies are not assumed permitted after
  licence termination; and
- no model, performance or product claim may rely on this panel while
  `claims_eligible=false`.

An equivalent/open source is not automatically licence-compatible. Any source
substitution requires legal review and a new contract version.

## Validation and rejection policy

Ingestion is rejected if any membership fails its security FK, if membership
periods overlap for the same universe/security, or if a ticker or external
identifier maps to multiple securities on the same date. Invalid date ranges,
duplicate source records, blank hashes, mismatched row/snapshot hashes and
missing required timestamps are also hard failures. These gates are
implemented by schema constraints and
`quantfore_research.validation.validate_security_master`.

Review-only findings may not be silently dropped: unknown optional identifiers,
missing delisting returns, unfamiliar corporate-action classes and source
revisions must flow into the Sprint 7 dataset audit. All hard gates must pass
before a point-in-time backtest is allowed.

The accepted vendor-bundle shape, immutable storage behavior and executable
command are frozen in `docs/data/point-in-time-equity-ingestion-v1.md`.
