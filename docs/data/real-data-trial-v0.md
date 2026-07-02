# Real-Data Trial v0 Contract

**Product:** Quantfore AI
**Dataset ID:** `us-equity-trial-v0`
**Version:** v0
**Contract date:** 2026-07-01
**Owner:** Product / Data / Quant Research
**Status:** Prototype snapshot retrieved; audit conditional pass

## Purpose and claims boundary

This contract defines the first fixed real-market price panel used to exercise
Quantfore's existing feature, scoring and outcome pipeline. It is a prototype
engineering and plausibility trial, not point-in-time universe validation and
not evidence of model performance.

```yaml
dataset_id: us-equity-trial-v0
dataset_kind: prototype_real
claims_eligible: false
publication_status: derived_reports_approved_for_public_repository_by_user
universe_file: config/universes/us-equity-trial-v0.csv
universe_file_sha256: 0a1ec9667fa4f4378f9c1c6bb010d03585690558069d04286a8320e9d02dd584
benchmark: SPY
history_start: 2020-01-01
dataset_cutoff_date: 2025-12-31
cutoff_inclusive: true
vendor: Tiingo
vendor_product: End-of-Day Prices API
vendor_source_url: https://api.tiingo.com/tiingo/daily/{ticker}/prices
licence_tag: tiingo_internal_research_trial_v0
retrieval_timestamp: 2026-07-02T06:32:40.222657Z
```

`retrieval_timestamp` is the UTC time of the final successful response in the
fresh-database trial retrieval. The 26 per-security snapshots retain their own
actual response times, spanning `2026-07-02T06:32:21.883182Z` through the value
above, plus source URLs and content hashes.

`licence_tag` is an internal lineage identifier, not a legal conclusion. The
user confirmed the required internal-use permission decision before retrieval.
Public redistribution and performance claims remain prohibited by this
contract.

## Vendor and licence gate

The selected implementation target is the Tiingo End-of-Day Prices API. It was
chosen for the prototype because its daily price response supports raw and
adjusted price/volume fields through one vendor adapter. WP6.2 must request all
available raw and adjusted daily OHLCV fields.

The following decisions must be supported by the executed account terms or
written vendor confirmation before retrieval:

| Permission | Required decision for this trial | Current evidence |
| --- | --- | --- |
| Internal research use | Allowed | User-confirmed for this trial on 2026-07-02 |
| Persistent storage of frozen raw responses | Allowed | User-confirmed for this trial on 2026-07-02 |
| Storage of normalised prices and hashes | Allowed | User-confirmed for this trial on 2026-07-02 |
| Creation and storage of derived features/scores | Allowed | User-confirmed for this trial on 2026-07-02 |
| Public redistribution or display of vendor data | Not required and prohibited by this contract | No approval assumed |
| Use for performance or marketing claims | Prohibited | `claims_eligible=false` |

Credentials must be supplied only through the environment variable selected by
the WP6.2 adapter. Secrets must never be written to this document, the universe
file, raw payloads, source URLs, logs or version control.

## Frozen universe

The canonical universe is
`config/universes/us-equity-trial-v0.csv`. It contains 25 ranked US-listed
equities and one non-ranked benchmark, SPY. The equities span all 11 sector
labels used by this pilot: Communication Services, Consumer Discretionary,
Consumer Staples, Energy, Financials, Health Care, Industrials, Information
Technology, Materials, Real Estate and Utilities.

This is a fixed pilot universe selected retrospectively on 2026-07-01 from
large, liquid, well-known securities with continuous listings across the trial
window. It is **not historical S&P 500 membership**, and it must never be
described or interpreted as such. The selection is exposed to survivorship,
large-cap, familiarity and hindsight biases. Results cannot establish how a
strategy would have performed on the investable universe known at each date.

The CSV fields have these meanings:

| Field | Meaning |
| --- | --- |
| `ticker` | Vendor request symbol and canonical trial symbol. |
| `company_name` | Security or trust name frozen by this contract. |
| `cik` | Ten-digit, zero-padded SEC Central Index Key. |
| `exchange` | Primary listing venue label used by this contract. |
| `sector` | Pilot sector label; `Benchmark ETF` for SPY. |
| `active_from` | First date on which the security is eligible in this trial, inclusive. |
| `active_to` | Last date on which the security is eligible in this trial, inclusive. |
| `is_benchmark` | Lowercase `true` only for SPY; otherwise `false`. |
| `selection_reason` | Retrospective reason for inclusion in the pilot. |

`active_from` and `active_to` are trial eligibility boundaries, not listing,
incorporation or index-membership dates. Every row is fixed to the same
2020-01-01 through 2025-12-31 observation window. SPY must be present for
benchmark outcomes but excluded from cross-sectional rankings.

## Price-panel contract

- At least five years of daily observations are required for every row; this
  contract requests six calendar years, from 2020-01-01 through 2025-12-31.
- The cut-off is fixed and inclusive. Later prices must be rejected, even if
  available when retrieval occurs.
- Raw and adjusted open, high, low, close and volume must be retained where the
  vendor supplies them. Missing adjusted fields must remain missing and be
  surfaced by the audit; they must not be silently reconstructed.
- Dates and timestamps must be normalised without inventing sessions. WP6.3
  will determine expected sessions using a proven US exchange calendar.
- Raw vendor responses must be immutable and content-addressed. Normalised rows
  must retain vendor, source URL, actual UTC retrieval timestamp, licence tag
  and raw-payload hash lineage.
- A partial security, partial page set, duplicate conflict or out-of-window row
  must not be silently accepted.
- Ticker changes and corporate-action discontinuities must be reported for
  review rather than repaired during ingestion.

## Change control and readiness

The universe file is part of the dataset identity. Any row, field or ordering
change requires a new reviewed contract version and hash. The frozen CSV's
SHA-256 is recorded above and must be verified from the exact checked-in bytes
and stored with each experiment that consumes it.

The retrieval was executed after the following gates were confirmed:

1. The vendor/account and permission evidence is recorded.
2. WP6.2 names the credential environment variable and implements the Tiingo
   adapter without committing a secret.
3. The ingestion manifest can record the actual retrieval timestamp, request
   source URLs, vendor, licence tag and hashes.
4. The adapter requests the exact frozen universe and inclusive date window.

The resulting WP6.3 quality decision is `review` because six securities contain
split-like discontinuities. WP6.4 reconciled 100 deterministic sample rows
against Yahoo Finance's Chart API and produced `conditional_pass`; the
independent source's redistribution rights and unlike raw-price basis remain
manual-review items. Canonical audit and WP6.6 reports are stored under
`reports/data-audits/` and `reports/backtests/`. The user explicitly authorised
publishing these derived reports to the public repository on 2026-07-02. Raw
responses, normalised vendor rows, local databases and credentials remain
excluded from Git.

WP6.7 concluded `requires_revision_before_model_claims`. The execution and
feature pipeline is complete, but quintile behaviour is non-monotonic, Rank IC
is unstable across years, and results are sensitive to mega-cap and outcome
outlier diagnostics. The reproducible review is stored beside the backtest as
`real_price_baseline_trial_v0_1-plausibility-review.json` and `.md`.

Under all circumstances, `dataset_kind=prototype_real` and
`claims_eligible=false` remain mandatory.
