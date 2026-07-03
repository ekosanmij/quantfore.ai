# Sprint 7 and Sprint 8 Free-Data Progress v1

Status: **IN PROGRESS — NOT CLOSURE EVIDENCE**

This document records the zero-cost, personal/internal-research data work
completed as of July 3, 2026. It deliberately does not claim that Sprint 7 or
Sprint 8 is closed. `claims_eligible=false` remains in force, the strict Sprint
7 bundle has not been created, and no final bundle manifest SHA-256 exists.

## Accepted scope

- Use scope: personal/internal research only.
- Commercial use: false.
- Redistribution of Tiingo data: prohibited and not intended.
- Raw Tiingo, SEC, OpenFIGI, membership, identifier, and licence-evidence bytes
  remain beneath `data/raw/` and are Git-ignored.
- Sprint 8 uses an amended zero-cost SEC EDGAR route rather than silently
  claiming that the original Sharadar vendor contract was fulfilled.
- The mature holdout cutoff is `2025-06-30`. This is the latest month-end whose
  252-session outcome is available through `2026-07-02`.

## Completed implementation

### Sprint 7 source and price acquisition

- Historical S&P 500 membership preflight covers `2014-01-01` through
  `2025-06-30`, with 761 membership episodes across 753 ticker labels.
- Monthly constituent counts range from 497 to 506.
- The current private acquisition plan is content-addressed and split into
  resumable Tiingo batches.
- Tiingo acquisition validates the exact plan hash, freezes exact response
  bytes, verifies every page hash on reuse, checkpoints after every symbol,
  and never persists the API key.
- Frozen price rows retain raw and adjusted OHLCV. Exact raw Tiingo responses
  also retain `divCash` and `splitFactor` for later corporate-action extraction.
- Current frozen progress is 292 of 673 safe symbols, comprising 870,365 daily
  rows. Batch 1 is 292/500; batch 2 is 0/173.

### Permanent identifiers

- All 754 required ticker labels were queried through frozen OpenFIGI v3
  responses.
- Pinned SEC ticker/CIK data and Wikipedia S&P revision `1295035732` provide
  deterministic name evidence for safe candidate selection.
- Name-based OpenFIGI evidence handles historical symbols that no longer map
  directly by ticker.
- 671 labels have resolved permanent identifiers.
- 83 labels are explicitly routed to dated corporate-lineage review.
- No label remains silently ambiguous or unresolved.

### Licence evidence

- The user confirmed personal/internal research use with no commercial use or
  redistribution.
- The confirmation and exact Tiingo terms bytes are privately frozen and
  hash-verified by the readiness audit.

### SEC fundamentals sources

- SEC Companyfacts and submissions metadata are frozen for 547 of 547 resolved
  CIKs.
- Each company completion record binds exact Companyfacts and submissions
  bytes, URLs, retrieval timestamp, CIK, ticker labels, share-class FIGIs, and
  SHA-256 hashes.
- The complete private SEC source tree is approximately 2.1 GiB.
- A deterministic filing-evidence plan contains 28,919 unique annual and
  quarterly filings across the 547 CIKs for `2012-01-01` through `2025-12-31`.
  That plan is the input for filing acceptance timestamps and dated SIC
  classification evidence.

### Holdout contract

- Lock preparation, lock validation, evaluation filtering, tests, and research
  documentation now consistently use `2025-06-30` as the holdout end.
- Late-2025 cohorts are excluded because their 252-session outcomes are not
  fully mature as of the evidence cutoff.

## Private source bindings

| Artifact | Private local path | SHA-256 |
| --- | --- | --- |
| Acquisition plan | `data/raw/free-point-in-time/acquisition-plan-v1-27755aa00a59a111.json` | `27755aa00a59a111745b2a7e4d517278328798751dfdaaf35f5b63ff19221075` |
| Resolved identifier registry | `data/raw/free-point-in-time/resolved-identifiers-v1.json` | `a047f00054e0ebc5f506a439fd3eea3fbaf9b35bbb7ac665f742cecc6fa06876` |
| Tiingo batch 1 registry | `data/raw/free-point-in-time/tiingo-prices-v1/batch-001/batch-registry.json` | `a7fb1b49271fb71d44f124c759e2e0da32aa5535779b9ecc97dcc1ace0b4a804` |
| SEC source registry | `data/raw/free-point-in-time/sec-pit-v1/registry.json` | `f80206b5178b3029c83f8af49e3a06c19c93a8f29ae865bf6669ac059e7e314e` |
| SEC filing-evidence plan | `data/raw/free-point-in-time/sec-filing-evidence-plan-v1.json` | `a7398f620c21a66510c66065252bdbed92094a50b1ae9a6c06167c9478882a63` |
| Personal-use confirmation | `data/raw/free-point-in-time/license-evidence/personal-internal-use-v1.json` | `6e1127c547e75a5aa6a015576584b2bf63c7337cd8d3e0a012a2ebc8f4ceb8ed` |

The Git-tracked readiness report is
`reports/data-audits/free-pit-bundle-readiness-v1.json`, SHA-256
`25d0c4fa45c06d600409ab2c6361d8865a4c0476557cfa6e61ff8a57323005da`.

## Validation completed

- Full research test suite: 269 tests passed.
- Python compilation passed for pipelines and the research package.
- `git diff --check` passed.
- Resumability, plan-hash rejection, frozen-page tamper rejection, OpenFIGI
  response validation, SEC source resumability, and deterministic filing-plan
  construction have focused regression coverage.

## Remaining work and hard blockers

1. Freeze the remaining 381 safe Tiingo symbols. The final 173-symbol batch
   cannot start until the free monthly symbol allowance resets.
2. Resolve the 83 dated corporate-lineage cases and acquire any required alias
   price series without querying recycled ticker identities blindly.
3. Freeze delisting dates and any source-supplied terminal returns; missing
   returns must remain explicit rather than imputed.
4. Reconcile the three independent membership samples or retain the differences
   as a blocking finding.
5. Acquire and parse the 28,919 filing index records for SEC acceptance
   timestamps and dated SIC classifications.
6. Normalize the SEC facts with revisions, availability precision, source
   hashes, permanent identifiers, and the amended free-source contract.
7. Build the strict Sprint 7 equity bundle and amended Sprint 8 fundamentals
   bundle, then run their audits.
8. Generate the holdout lock and real reports, run two clean rebuilds, and
   publish closure only if every hard gate passes.

Until those steps finish, the planned Sprint 7 bundle path is
`data/raw/free-point-in-time/composite-equity-bundle-v1`, its `manifest.json`
does not exist, and its manifest SHA-256 is unavailable.

## Reproduction commands

```bash
PYTHONPATH="$PWD:$PWD/packages/research" .venv/bin/python \
  pipelines/acquire_free_point_in_time_prices.py \
  --plan data/raw/free-point-in-time/acquisition-plan-v1-27755aa00a59a111.json \
  --expected-plan-hash 27755aa00a59a111745b2a7e4d517278328798751dfdaaf35f5b63ff19221075 \
  --batch-number 1 --start-date 2013-01-01 --end-date 2026-07-02

PYTHONPATH="$PWD:$PWD/packages/research" .venv/bin/python \
  pipelines/acquire_sec_point_in_time_fundamentals.py \
  --expected-identifier-hash \
  a047f00054e0ebc5f506a439fd3eea3fbaf9b35bbb7ac665f742cecc6fa06876

PYTHONPATH="$PWD:$PWD/packages/research" .venv/bin/python \
  pipelines/plan_sec_filing_evidence.py \
  --expected-registry-hash \
  f80206b5178b3029c83f8af49e3a06c19c93a8f29ae865bf6669ac059e7e314e

PYTHONPATH="$PWD:$PWD/packages/research" .venv/bin/python \
  pipelines/audit_free_point_in_time_bundle_readiness.py \
  --plan data/raw/free-point-in-time/acquisition-plan-v1-27755aa00a59a111.json \
  --expected-plan-hash \
  27755aa00a59a111745b2a7e4d517278328798751dfdaaf35f5b63ff19221075
```
