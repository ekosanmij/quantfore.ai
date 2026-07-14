# Point-in-Time Subtype Classification v1

`claims_eligible=false`

## Decision

Sprint 10.2 passes. Classification version
`sec-sic-financial-subtype-v2` assigns a known point-in-time subtype to 50,217 of
50,600 intended stock-months (99.2431%). Every one of the 102 monthly cohorts
meets the stricter 98% coverage floor; the weakest month is 98.1670%.

The immutable row ledger is
[`experiments/model-v2-point-in-time-subtype-classification-v1.jsonl.gz`](../../experiments/model-v2-point-in-time-subtype-classification-v1.jsonl.gz).
The machine-readable audit, including every monthly denominator and every unresolved
identity record, is
[`reports/data-audits/model-v2-subtype-classification-coverage-v1.json`](../../reports/data-audits/model-v2-subtype-classification-coverage-v1.json).

This is a classification pass only. `classification_eligible=true` means that the
row has an active, known Model V2 branch; it does not assert that branch-specific
accounting features exist or that a final score may already be emitted.

## Frozen scope and outcome blinding

The denominator is the 50,600 distinct `security_id` / `asof_date` keys already
present in `multifactor_scores`, spanning 2017-01-31 through 2025-06-30. The builder
reads only those two key columns from that table. It does not read final scores,
returns, prediction outcomes, rank IC, portfolio results, or any other performance
field. The audit binds the sorted denominator keys with a SHA-256 digest.

Classification inputs are the read-only Sprint 8 pre-lock warehouse, exact SEC SIC
records that were model-available by the prediction date, and revision-pinned
Wikipedia constituent snapshots. Later explicit evidence never applies to an earlier
prediction. A dated label persists only until a later dated snapshot supersedes it.

## Evidence precedence

For each security-month, routing is deterministic and follows this order:

1. A conflict among equally current records produces `CLASSIFICATION_CONFLICT` and
   exclusion.
2. The latest explicit GICS sector and sub-industry snapshot dated on or before the
   prediction date controls.
3. If explicit evidence is absent, an exact SEC SIC rule may supply a conservative
   floor.
4. A known non-financial broad sector may enter `INDUSTRIAL_GENERAL`.
5. A broad Financials or Unknown label without subtype evidence remains unknown and
   excluded.

The explicit records are mapped to the permanent security by normalized CIK and the
warehouse's dated ticker aliases. An exact warehouse ticker is an identity bridge
only when dated aliases or CIK are unavailable; it never supplies classification
content. CIK/ticker disagreement is retained as an unresolved identity record rather
than guessed. The subtype-only pre-window snapshots do not claim exact reconciliation
to the separate membership source; their registry records that limitation.

## Routing policy

| Evidence known at prediction time | Branch | Classification state |
| --- | --- | --- |
| Non-financial GICS sector | `INDUSTRIAL_GENERAL` | Known, active branch |
| GICS bank sub-industry, or SIC 6020–6099 | `BANK` | Known, active branch |
| GICS investment banking/brokerage, or SIC 6200/6211 | `BROKER_DEALER` | Known, active branch |
| GICS asset management, or SIC 6282 | `ASSET_MANAGER` | Known, active branch |
| GICS life/health insurance, or SIC 6311/6321/6324 | `INSURER_LIFE_HEALTH` | Known, active branch |
| GICS P&C, reinsurance, or multi-line insurance, or SIC 6331/6351/6361 | `INSURER_P_AND_C` | Known, active branch |
| GICS REIT sub-industry other than mortgage | `EQUITY_REIT` | Known, active branch |
| GICS mortgage REIT sub-industry | `MORTGAGE_REIT` | Known, active branch |
| Other explicit Financials label, or SIC 6199/6399/6411/6792/6799 | `OTHER_FINANCIAL` | Known, excluded pending a locked branch model |
| SIC 6798 without dated explicit REIT subtype | `UNKNOWN` / `REIT_SUBTYPE_UNRESOLVED` | Unknown, excluded |
| Broad Financials without a safe exact rule | `UNKNOWN` / `FINANCIAL_SUBTYPE_UNRESOLVED` | Unknown, excluded |
| No point-in-time source | `UNKNOWN` / `CLASSIFICATION_SOURCE_UNAVAILABLE` | Unknown, excluded |

SIC 6798 establishes REIT status but does not distinguish equity from mortgage REIT.
It therefore cannot by itself activate either REIT branch. This prevents the old
defect that treated all SIC 6798 issuers as generic Financials. Insurers likewise
enter an insurance branch and are never forced through the industrial formula.

Insurance brokers are `OTHER_FINANCIAL`, not broker-dealers. The phrase “Investment
Banking & Brokerage” routes to `BROKER_DEALER`; “Asset Management & Custody Banks”
routes to `ASSET_MANAGER` before the generic bank keyword is considered.

## Revision-pinned explicit evidence

| Evidence date | Wikipedia revision | Raw response SHA-256 | Membership identity use |
| --- | ---: | --- | --- |
| 2016-12-30 | 757478916 | `a2229a46f7bcfcd7deddc10085b0c2d10ce31ec4bff5cf8169e44b48d3c2afff` | Subtype evidence only |
| 2017-08-29 | 797887606 | `752865c9a868729134484e1fcc24f16c49d3f10f3604bfd109210f9e7a6f0ec7` | Subtype evidence only |
| 2018-06-28 | 847904975 | `b116278210f1cd6d8b77ad590bc6619b574f25faa69b0e4afca078d655f5e356` | Subtype evidence only |
| 2018-12-31 | 876091698 | `6edeca4b312bcd62af278d17e76fbc0f295b9c6e7bf5d28f22f0611e53f8db44` | Independently membership-reconciled |
| 2022-12-31 | 1130173030 | `5b4b766707cf44b799634539136b177c2b10fef53a85376a062f6bf9df375c9d` | Independently membership-reconciled |
| 2025-06-30 | 1295035732 | `c4b30c22d0b2eb63810e572676c4cb1c6daec3ea29a7d5abb1e0ba6effbadc45` | Independently membership-reconciled |

Each ledger row carries the evidence date, revision ID, raw path and hash, registry
path and hash, observed ticker and CIK, GICS sector/sub-industry, and identity matching
method. SEC fallback rows carry classification ID/system, effective interval,
`model_available_at`, source snapshot ID/path, and source hash.

## Coverage and exclusions

| Branch/state | Stock-months |
| --- | ---: |
| `INDUSTRIAL_GENERAL` | 40,673 |
| `BANK` | 1,503 |
| `INSURER_P_AND_C` | 1,170 |
| `INSURER_LIFE_HEALTH` | 575 |
| `BROKER_DEALER` | 498 |
| `ASSET_MANAGER` | 872 |
| `EQUITY_REIT` | 2,996 |
| `MORTGAGE_REIT` | 0 |
| `OTHER_FINANCIAL` | 1,930 |
| `UNKNOWN` | 383 |
| **Total** | **50,600** |

No mortgage REIT occurs in this intended S&P 500 scoring universe under evidence
available at the relevant dates. The branch remains implemented and tested rather
than being collapsed into equity REITs.

The 2,313 classification-excluded rows consist of 1,930 known
`OTHER_FINANCIAL` rows plus 383 unknown rows. Unknown reasons are 342 source-unavailable
stock-months, 35 unresolved SIC 6798 stock-months, and six explicit conflicts. The
ledger sets `classification_eligible=false` on every one. Nothing falls back to a
generic Financials or industrial formula.

## Rebuild and verification

Run from the repository root:

```text
.venv/bin/python pipelines/build_point_in_time_subtype_ledger.py \
  --generated-at 2026-07-14T12:00:00Z
PYTHONPATH=. .venv/bin/pytest -q \
  packages/research/tests/test_point_in_time_subtype_classification.py \
  packages/research/tests/test_wikipedia_membership_samples.py
```

The ledger uses deterministic gzip metadata (`mtime=0`) and sorted compact JSON rows.
The builder verifies every raw response against its registry hash before parsing and
opens the warehouse in SQLite read-only/query-only mode.
