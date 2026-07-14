# Raw Data Storage and Recovery v1

Status: **SPRINT 10.1 READY**
Inventory date: `2026-07-14`
Scope: private raw research data; `claims_eligible=false`

## Decision

Use **both** an encrypted external drive and encrypted, versioned cloud object
storage. The laptop copy remains the working copy. The external drive is the
fast local recovery copy; cloud storage is the off-site disaster copy. A copy
is not accepted as a backup until the repository-tracked SHA-256 manifest
verifies every restored byte.

The complete backup scope is `data/raw/`, not only the largest databases. It is
currently 62,011 files, 67,203,994,284 logical bytes, and 62.74 GiB of allocated
space. `data/raw/free-point-in-time/` accounts for 61,896 files and 62.64 GiB.
Raw bytes remain Git-ignored. Git tracks only the inventory, integrity hashes,
recovery procedure, and compressed checksum manifest.

Tracked recovery artifacts:

- `reports/data-audits/raw-data-storage-inventory-v1.json`
- `reports/data-audits/raw-data-sha256-v1.jsonl.gz`
- `pipelines/verify_raw_data_manifest.py`

The compressed checksum manifest has SHA-256
`818a4aadea17a317010223913feeaa1d72c0898166cee0b547c23f4efe1898ed`
and binds all 62,011 files by portable path, logical size, and content SHA-256.
It does not contain raw payload bytes or absolute machine paths.

## Storage map

All paths below are relative to the repository unless `QUANTFORE_DATA_ROOT` is
set. Sizes are allocated sizes measured on the local APFS volume on the
inventory date; they are operational estimates rather than contractual hashes.

| Path | Size | Files | Purpose and rebuild role |
| --- | ---: | ---: | --- |
| `data/raw/free-point-in-time/` | 62.64 GiB | 61,896 | Canonical point-in-time source evidence, deterministic bundles, and Sprint 8/9/10 rebuild workspaces. Full backup required. |
| `data/raw/prices/` | 19.6 MiB | 105 | Legacy market-price source snapshots used by early baseline and trial rebuilds. |
| `data/raw/reconciliation/` | 92 KiB | 7 | Independent Yahoo price-reconciliation evidence. |
| `data/raw/real_trial_v0.sqlite` | 26.9 MiB | 1 | Legacy real-trial working database; reproducible but retained for audit continuity. |
| `data/raw/real_trial_v0_69bbcc7.sqlite` | 26.9 MiB | 1 | Legacy trial database snapshot. |
| `data/raw/real_trial_v0_8c2e391.sqlite` | 26.9 MiB | 1 | Legacy trial database snapshot. |

### Point-in-time source evidence and controls

| Path beneath `data/raw/free-point-in-time/` | Size | Files | Purpose and rebuild role |
| --- | ---: | ---: | --- |
| `primary-b792557e915703398ef9a67e4b583a37c6ec80d5.csv` | 5.3 MiB | 1 | Primary historical S&P 500 membership source; irreplaceable pinned source input. |
| `secondary-a91ef88fad5ace83bed1f3452f451247295bcd18.csv` | 6.5 MiB | 1 | Secondary membership cross-check. |
| `primary-license-*.txt`, `secondary-license-*.txt` | 8 KiB | 2 | Exact licence texts for the membership sources. |
| `acquisition-plan-v1-*.json` | 80 KiB | 2 | Historical and current content-addressed acquisition plans; the `27755...` plan is canonical. |
| `wikipedia-sp500-1295035732.json` | 728 KiB | 1 | Revision-pinned Wikipedia source response used in identifier resolution. |
| `wikipedia-membership-samples-v1/` | 636 KiB | 4 | Revision-pinned membership samples and reconciliation registry. |
| `wikipedia-subtype-samples-v1/` | 556 KiB | 4 | Pre-window and mid-window revision-pinned GICS subtype evidence used by Sprint 10.2. |
| `tiingo-supported-tickers.zip` | 776 KiB | 1 | Frozen listing inventory used for delisting evidence. |
| `tiingo-prices-v1/` | 486 MiB | 1,348 | Primary raw adjusted/unadjusted price responses and batch registries. |
| `lineage-prices-v1/` | 8.8 MiB | 37 | Direct historical-lineage price responses. |
| `lineage-alias-prices-v1/` through `v5/` | 7.2 MiB | 29 | Alias and predecessor price responses used to close identity-safe chains. |
| `openfigi-v3/` | 2.8 MiB | 605 | Frozen OpenFIGI mappings and registry. |
| `openfigi-name-search-v3/` | 224 KiB | 14 | Frozen name-search evidence for ambiguous historical tickers. |
| `lineage-evidence-v1/` | 428 KiB | 87 | Dated metadata, lineage decisions, and registry. |
| `wikidata-lineage-v1/` | 204 KiB | 2 | Dated Wikidata query response and registry. |
| `resolved-identifiers-v1.json` | 240 KiB | 1 | Resolved permanent-identifier ledger. |
| `reconciled-lineage-v1.json` | 156 KiB | 1 | Canonical identity and usable-price reconciliation. |
| `price-exclusions-v1.json` | 48 KiB | 1 | Explicit, reason-coded price exclusions. |
| `delisting-evidence-v1.json` | 328 KiB | 1 | Frozen listing endpoints and unavailable delisting outcomes. |
| `sec/company_tickers.json` | 780 KiB | 1 | Pinned SEC ticker/CIK lookup used in identifier resolution. |
| `sec-pit-v1/` | 2.1 GiB | 1,642 | Canonical SEC Companyfacts/submissions source tree and registry. |
| `sec-lineage-v1/` | 108 MiB | 150 | Supplemental SEC evidence for historical issuer lineage. |
| `sec-filing-evidence-plan-v1.json` | 7.0 MiB | 1 | Deterministic filing-index acquisition plan. |
| `sec-filing-evidence-v1/` | 539 MiB | 57,837 | Filing index pages, availability/SIC evidence, completion records, and registry. |
| `license-evidence/` | 652 KiB | 2 | Personal/internal-use confirmation and exact Tiingo terms bytes. |

These source paths are the highest-priority recovery set. API responses must
not be silently reacquired as substitutes: a later vendor response can differ
even when the URL is unchanged.

### Deterministic derived bundles

| Path beneath `data/raw/free-point-in-time/` | Size | Files | Purpose and rebuild role |
| --- | ---: | ---: | --- |
| `composite-equity-bundle-v1/` | 385 MiB | 6 | Canonical securities, memberships, prices, actions, delistings, and manifest used to rebuild the warehouse. |
| `sec-fundamentals-bundle-v1/` | 415 MiB | 3 | Canonical classifications, point-in-time fundamentals, and manifest. |
| `sec-fundamentals-bundle-v2/` | 1.0 GiB | 3 | Sprint 10.3 accounting-history expansion, derived discrete quarters, and content-addressed formula lineage. |

The bundles can be rebuilt from frozen source evidence, but backing them up
avoids a long reconstruction and preserves the exact manifest-bound inputs.

### Rebuild workspaces and closure evidence

| Path beneath `data/raw/free-point-in-time/` | Size | Files | Purpose and rebuild role |
| --- | ---: | ---: | --- |
| `sprint8-prelock-v1/` | 991 MiB | 7 | Early Sprint 8 rebuild workspace; retained as research process evidence. |
| `sprint8-prelock-v2/` | 1.4 GiB | 10 | Developmental rebuild workspace. |
| `sprint8-prelock-v3/` | 385 MiB | 8 | Developmental rebuild workspace. |
| `sprint8-prelock-v4/` | 1.4 GiB | 11 | Developmental rebuild workspace. |
| `sprint8-prelock-v5/` | 2.3 GiB | 14 | Developmental rebuild and audit diagnostics. |
| `sprint8-prelock-v6/` | 2.6 GiB | 15 | Developmental rebuild workspace. |
| `sprint8-prelock-v7/` | 5.9 GiB | 14 | Near-closure rebuild workspace. |
| `sprint8-prelock-v8/` | 21 GiB | 14 | First large closure-speed rebuild workspace. |
| `sprint8-prelock-v9/` | 21 GiB | 14 | Authoritative Sprint 9 diagnostic warehouse and rebuild evidence. |
| `sprint8-closure.log`, `sprint8-closure-generated-at.txt` | 8 KiB | 2 | Closure execution trace and frozen timestamp. |

Versions `v1` through `v8` are reproducible intermediates and lower recovery
priority than source evidence. `v9` is high priority because the frozen Sprint
9 reports read its `research.db`. All are included in the chosen full backup,
so no deletion or deduplication is authorized by this document.

## Backup policy

### Copies and security

1. **Working copy:** the laptop's configured `data/raw/` tree.
2. **Fast recovery copy:** a dedicated encrypted external volume, mounted only
   for backup or restore. Use a dated snapshot directory; do not edit files in
   place on the backup.
3. **Off-site copy:** a private, encrypted, versioned object-storage bucket.
   Upload to a new immutable date/version prefix. Enable provider versioning,
   deny public access, and keep credentials outside the repository.

The external drive and cloud bucket must each contain the raw snapshot and the
exact checksum manifest used to validate it. Encryption may change stored
ciphertext, but decrypting a restore must reproduce the original bytes and pass
the SHA-256 verifier.

### Schedule and retention

- Run a new backup after every source acquisition, manifest change, bundle
  rebuild, or evidence-sealing event.
- If no data changes, run at least weekly while research is active.
- Keep the current snapshot, the immediately preceding snapshot, and all
  evidence-bound milestone snapshots. Never overwrite a Sprint closure or
  shadow-lock snapshot.
- Run a restore drill from alternating external/cloud copies at least
  quarterly and before any executable lock.
- Recovery-point objective: the last completed data-changing research session.
- Recovery-time objective: one working day for the full 63 GiB tree, subject to
  cloud download speed.

## Backup runbook

Start from a clean repository checkout. The default is repository-local data;
an external working location can be selected as follows:

```bash
export QUANTFORE_DATA_ROOT="$PWD/data"
export SNAPSHOT="$(date -u +%Y%m%dT%H%M%SZ)"
export MANIFEST="$PWD/reports/data-audits/raw-data-$SNAPSHOT.jsonl.gz"
```

Create the byte-level manifest before copying. This command reads every raw
file and writes no raw data:

```bash
.venv/bin/python pipelines/verify_raw_data_manifest.py create \
  --raw-root "$QUANTFORE_DATA_ROOT/raw" \
  --output "$MANIFEST"
```

Copy to an encrypted external drive using a new snapshot directory, then
verify the copy against the source manifest:

```bash
export EXTERNAL_SNAPSHOT="/Volumes/QuantforeBackup/quantfore-data/$SNAPSHOT"
mkdir -p "$EXTERNAL_SNAPSHOT/raw" "$EXTERNAL_SNAPSHOT/manifests"
rsync -a "$QUANTFORE_DATA_ROOT/raw/" "$EXTERNAL_SNAPSHOT/raw/"
rsync -a "$MANIFEST" "$EXTERNAL_SNAPSHOT/manifests/"
.venv/bin/python pipelines/verify_raw_data_manifest.py verify \
  --raw-root "$EXTERNAL_SNAPSHOT/raw" \
  --manifest "$EXTERNAL_SNAPSHOT/manifests/$(basename "$MANIFEST")"
```

For cloud storage, use the provider's encrypted client to copy the same raw
tree and manifest to a new `$SNAPSHOT` prefix with public access disabled and
versioning enabled. Do not use a mode that rewrites text, exports databases, or
deletes older prefixes. Download the completed prefix to an empty temporary
directory and run the same `verify` command before recording the cloud backup
as successful.

Record the snapshot timestamp, manifest SHA-256, external verification result,
cloud verification result, and object version/prefix in the private backup
operator log. Credentials, bucket secrets, and encryption keys must never be
written to Git or the raw tree.

## Laptop-loss restore runbook

1. Replace the laptop and clone the repository at the required evidence
   commit. Install the research environment from `packages/research`.
2. Choose the newest backup whose checksum manifest is committed or otherwise
   bound to the required milestone. Prefer the external copy for speed; fall
   back to the cloud copy if it is unavailable or fails validation.
3. Restore into an empty directory. Never merge an unverified restore with a
   partial working tree.
4. Set `QUANTFORE_DATA_ROOT` to the restored directory containing `raw/`.
5. Run the full manifest verifier:

```bash
export QUANTFORE_DATA_ROOT="/path/to/restored/quantfore-data"
.venv/bin/python pipelines/verify_raw_data_manifest.py verify \
  --raw-root "$QUANTFORE_DATA_ROOT/raw" \
  --manifest reports/data-audits/raw-data-sha256-v1.jsonl.gz
```

The restore is accepted only when the result reports `"verified": true`,
`"file_count": 62011`, and `"total_size_bytes": 67203994284`. Any missing,
extra, resized, or hash-mismatched file rejects the entire restore. Do not
repair mismatches by reacquiring current vendor data; restore the correct
snapshot instead.

6. Run the full test suite. For Sprint 9 evidence, run the existing audit and
   diagnostics read-only against
   `$QUANTFORE_DATA_ROOT/raw/free-point-in-time/sprint8-prelock-v9/research.db`.
7. Confirm the canonical integrity anchors in
   `raw-data-storage-inventory-v1.json` still match and keep Sprint 9 reports
   unchanged. The restored raw location may differ; content hashes may not.

## Configurable data location

Shared ingestion scripts now resolve their default raw directory as
`$QUANTFORE_DATA_ROOT/raw`; when the variable is absent they use the repository
`data/raw/` directory. The Sprint 9 cohort, factor-family, and investability
diagnostics use the same setting for their default frozen warehouse and raw
manifests.

Every point-in-time acquisition and bundle pipeline also exposes explicit path
arguments such as `--output-root`, `--price-root`, `--sec-root`,
`--equity-bundle`, or `--database-path`. Use those flags for one-off restores
that do not follow the standard `<data-root>/raw/` layout. Report paths remain
repository-relative so generated evidence stays reviewable and trackable.

Changing the physical data root is a storage operation only. It does not
authorize changing source manifests, formula inputs, evidence timestamps, or
any frozen Sprint 9 report.

## Acceptance checklist

- [x] Every current raw folder is inventoried; the full file manifest covers
  all descendants.
- [x] Size, purpose, and rebuild role are recorded for every major folder.
- [x] Backup choice is both encrypted external and encrypted versioned cloud.
- [x] Exact-byte create/verify tooling exists and has regression tests.
- [x] Raw payloads remain outside Git; tracked artifacts contain hashes and
  paths only.
- [x] A restored tree can live outside the repository without changing hashes.
- [ ] First external snapshot copied and verified by the backup operator.
- [ ] First cloud snapshot copied, downloaded to an empty directory, and
  verified by the backup operator.

The final two operational checks require the user's backup volume, cloud
account, and encryption credentials. They are deliberately not inferred or
stored by repository code.
