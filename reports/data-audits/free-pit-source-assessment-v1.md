# Free Point-in-Time Source Assessment v1

Decision: **BLOCKED**

This is a source-coverage preflight, not Sprint 7 closure evidence. `claims_eligible=false` remains in force.

## Measured coverage

- Window: `2014-01-01` through `2025-06-30`.
- Historical membership episodes: 761 across 753 ticker labels.
- Monthly constituent range: 497–506.
- Required Tiingo symbols: 754; configured free monthly allowance: 500.
- Safe unchanged-ticker symbols staged for download: 673 across 2 free-tier batches.
- Fully resolved same-ticker episodes: 680 of 761.

## Blocking findings

- `tiingo_free_monthly_symbol_limit`: 754 unique symbols are required but the configured free allowance is 500 per month.
- `incomplete_tiingo_episode_resolution`: only 680 of 761 membership episodes are fully covered by an unambiguous same-ticker Tiingo listing.
- `secondary_membership_disagreement`: one or more historical membership samples disagree.

## Interpretation

The free route is technically viable as a staged acquisition, but it cannot truthfully close Sprint 7 in one free-tier month. Unresolved episodes require explicit rename/acquisition lineage or another price source; blindly querying recycled ticker labels is prohibited.

The companion JSON contains aggregate source hashes and reconciliation counts. The symbol-level acquisition and unresolved-episode plan is content-addressed under `data/raw/` and is deliberately Git-ignored.
