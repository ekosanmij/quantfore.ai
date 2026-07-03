# Free Point-in-Time Bundle Readiness v1

Status: **IN_PROGRESS**

- Acquisition plan SHA-256: `27755aa00a59a111745b2a7e4d517278328798751dfdaaf35f5b63ff19221075`
- Frozen prices: 292 / 673 symbols (870365 rows)
- Unique permanent-ID mappings: 671 / 754
- Planned bundle path: `/Users/ekosanmi.j/Documents/quantfore.ai/data/raw/free-point-in-time/composite-equity-bundle-v1`
- Final manifest SHA-256: not created

## Blocking findings

- `incomplete_price_acquisition`: 292 of 673 safe symbols are frozen.
- `incomplete_permanent_identity_lineage`: 83 ticker labels still require identity resolution.
- `unresolved_price_alias_episodes`: 81 membership episodes require price/corporate-action lineage.
- `delisting_evidence_pending`: delisting dates and any available terminal returns are not yet frozen.
- `independent_membership_reconciliation_pending`: the three secondary membership samples do not yet agree exactly.
