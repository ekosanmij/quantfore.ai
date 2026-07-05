# Sprint 7 and Sprint 8 Free-Data Progress v1

Status: **SPRINT 7 CLOSED; SPRINT 8 IN PROGRESS**

This document records the zero-cost, personal/internal-research data work
completed as of July 3, 2026. Sprint 7 has passing two-rebuild closure evidence.
Sprint 8 remains in progress. `claims_eligible=false` remains in force.

## Accepted scope

- Use scope: personal/internal research only.
- Commercial use: false.
- Redistribution of Tiingo data: prohibited and not intended.
- Raw Tiingo, SEC, OpenFIGI, membership, identifier, and licence-evidence bytes
  remain beneath `data/raw/` and are Git-ignored.
- Sprint 8 uses an amended zero-cost SEC EDGAR route rather than silently
  claiming that the original Sharadar vendor contract was fulfilled.
- The zero-cost composite evidence window is `2017-01-01` through
  `2025-06-30`. The earlier 2014–2016 preflight remains frozen as negative
  coverage evidence but is outside this amended experiment contract.
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
- Both batches are complete: 673 of 673 planned symbols and 2,036,866 frozen
  daily rows (batch 1 is 500/500; batch 2 is 173/173).
- A post-download episode audit found 16 planned symbols whose now-recycled
  Tiingo endpoints return only a later issuer. Those downloads remain frozen
  as negative evidence and are never treated as historical prices.

### Permanent identifiers

- All 754 required ticker labels were queried through frozen OpenFIGI v3
  responses.
- Pinned SEC ticker/CIK data and Wikipedia S&P revision `1295035732` provide
  deterministic name evidence for safe candidate selection.
- Name-based OpenFIGI evidence handles historical symbols that no longer map
  directly by ticker.
- 671 labels have resolved permanent identifiers.
- 83 labels are explicitly routed to dated corporate-lineage review.
- All 83 historical labels now have SEC/Wikidata-backed identities. Fifty-four
  of the 81 price-lineage episodes have full identity-safe price chains,
  including SEC-verified predecessor/current aliases and split alias series.
- The remaining historical lineage gaps plus the recycled-endpoint episodes
  are explicit, reason-coded price exclusions. No label remains silently
  ambiguous.

### Membership, exclusions, and listing endpoints

- Three revision-pinned Wikipedia samples reconcile by permanent identity:
  2018-12-31, 2022-12-31, and 2025-06-30. The sole raw-label difference is the
  verified `KORS` to `CPRI` rename.
- Tiingo inventory endpoints and exact terminal price bytes are frozen for 109
  ended listings. Tiingo does not supply a separate delisting-return field, so
  those returns remain null and are never inferred from an ordinary last-day
  return.
- The amended 2017–2025 contract intersects 23 reason-coded price exclusions.
  Minimum monthly full-universe coverage is `0.962451`, with zero months below
  the frozen Sprint 7 `0.95` gate. The denominator includes every historical
  membership episode, including excluded episodes.
- The deterministic strict bundle contains 638 securities, 645 coalesced
  membership episodes, 1,266,438 price rows, 15,399 corporate actions, and 57
  ended listings. Its candidate manifest SHA-256 is
  `8b39fe268b3414495f7a2f95fe00e7b76f4afc1f33cec961ef095f4495a90a6e`;
  closure acceptance remains contingent on the fresh audit and two-rebuild
  gate.

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
- The filing-index registry accounts for all 28,919 planned accessions: 28,917
  hash-verified filing indexes and two explicit orphan-accession records. The
  two orphans consistently return HTTP 503 and are absent from SEC submissions
  and full-text indexes; facts tied to them are prohibited from use.
- Comparative facts repeated by later filings retain one canonical fiscal year
  per issuer-period identity; later SEC filing-year labels cannot relabel the
  historical period or create false fiscal-period audit failures.

### Holdout contract

- Lock preparation, lock validation, evaluation filtering, tests, and research
  documentation import one frozen `2025-06-30` holdout end.
- Late-2025 cohorts are excluded because their 252-session outcomes are not
  fully mature as of the evidence cutoff, and warehouse evaluation rejects
  them rather than treating them as ordinary out-of-window observations.

### Closure verification

- Sprint 8 closure now rebuilds the verified evaluation and comparison ledgers
  from each fresh closure database, recalculates their metrics, and requires
  exact canonical equality with the supplied reports.
- The fundamentals audit is also recalculated, including deterministic SEC
  reconciliation, and compared in full with the supplied audit document.
- The external rebuild program requires an expected SHA-256, is rechecked
  immediately before each execution, and records that digest in closure output.

## Private source bindings

| Artifact | Private local path | SHA-256 |
| --- | --- | --- |
| Acquisition plan | `data/raw/free-point-in-time/acquisition-plan-v1-27755aa00a59a111.json` | `27755aa00a59a111745b2a7e4d517278328798751dfdaaf35f5b63ff19221075` |
| Resolved identifier registry | `data/raw/free-point-in-time/resolved-identifiers-v1.json` | `a047f00054e0ebc5f506a439fd3eea3fbaf9b35bbb7ac665f742cecc6fa06876` |
| Tiingo batch 1 registry | `data/raw/free-point-in-time/tiingo-prices-v1/batch-001/batch-registry.json` | `ed47ec459e05e90802ef5396fc82e1f3b19dfe2cf64d4859c762b76e77f3cfe3` |
| Tiingo batch 2 registry | `data/raw/free-point-in-time/tiingo-prices-v1/batch-002/batch-registry.json` | `6d25377ef35d33ae038f8b1827486e79ccd57580540b33e4fd48b912460cc767` |
| Reconciled lineage | `data/raw/free-point-in-time/reconciled-lineage-v1.json` | `39f039174612010e24d6659276711271427dca10929e230ce612c6ca591e556d` |
| Price exclusions | `data/raw/free-point-in-time/price-exclusions-v1.json` | `78da4b5509122b336597441d06df388c9fdec5ff9618a6785f291fcfe698fbba` |
| Delisting evidence | `data/raw/free-point-in-time/delisting-evidence-v1.json` | `547d2063d33af403731c39e0348b6b0d95e441866a588717bf948ce5389ec2f4` |
| Strict equity bundle manifest | `data/raw/free-point-in-time/composite-equity-bundle-v1/manifest.json` | `8b39fe268b3414495f7a2f95fe00e7b76f4afc1f33cec961ef095f4495a90a6e` |
| Membership reconciliation | `data/raw/free-point-in-time/wikipedia-membership-samples-v1/registry.json` | `e5ee52e2ce8b01740632e5138b5bb10c89395e77ccdbde790df56ec84b627277` |
| SEC source registry | `data/raw/free-point-in-time/sec-pit-v1/registry.json` | `f80206b5178b3029c83f8af49e3a06c19c93a8f29ae865bf6669ac059e7e314e` |
| SEC filing-evidence plan | `data/raw/free-point-in-time/sec-filing-evidence-plan-v1.json` | `a7398f620c21a66510c66065252bdbed92094a50b1ae9a6c06167c9478882a63` |
| SEC filing-evidence registry | `data/raw/free-point-in-time/sec-filing-evidence-v1/registry.json` | `cf61c9a2af535d5a9380616cf98b61f18a78fb63c96b121f5d630db67fb12f68` |
| SEC fundamental bundle manifest | `data/raw/free-point-in-time/sec-fundamentals-bundle-v1/manifest.json` | `0a1aef3b2527672ff3febb2702479fe86ddebd51f393ae5eede26007f805985f` |
| Personal-use confirmation | `data/raw/free-point-in-time/license-evidence/personal-internal-use-v1.json` | `6e1127c547e75a5aa6a015576584b2bf63c7337cd8d3e0a012a2ebc8f4ceb8ed` |

The Git-tracked readiness report is
`reports/data-audits/free-pit-bundle-readiness-v1.json`, SHA-256
`0347dd53f9b8a16132b0e1681cf05d2fc378f30a3ab768dcbeed015e8d516039`.

## Validation completed

- Full research test suite: 299 tests passed.
- Python compilation passed for pipelines and the research package.
- `git diff --check` passed.
- Resumability, plan-hash rejection, frozen-page tamper rejection, OpenFIGI
  response validation, SEC source resumability, and deterministic filing-plan
  construction have focused regression coverage.

## Remaining work and hard blockers

1. Run the fresh-database audit of the normalized SEC-primary fundamentals and
   dated classifications.
2. Generate the holdout lock and real reports, run two clean rebuilds, and
   publish closure only if every hard gate passes.

The earlier provisional equity manifest was rejected after adapter validation
exposed recycled-symbol contamination. The corrected 2017 contract passed two
independent rebuilds, with exact canonical report and audit hashes. Sprint 8
still requires its separate holdout lock and reproducibility gate.

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
