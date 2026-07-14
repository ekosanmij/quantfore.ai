# Model V3 Data-Coverage Remediation Contract

Status: `BLOCKED_STRUCTURAL_FEASIBILITY_INPUT_MISSING`
Claims eligible: `false`
Data acquisition executable: `false`
Score rebuild executable: `false`

## Decision

The Model V3 data-repair work is specified but cannot start. The structural audit
found no qualifying historical expanded-universe membership ledger, so the branch
population denominator is unknown. Acquiring prices and accounting data before that
denominator exists would create an availability-selected universe and violate the
Model V3 design lock.

The machine-readable plan is
[`model-v3-data-coverage-remediation-plan-v1.json`](../../experiments/model-v3-data-coverage-remediation-plan-v1.json).

## Existing evidence baseline

The frozen V2 evidence remains useful for prioritization, not for proving V3
readiness:

- 50,600 expected S&P 500 security-months;
- 16,349 scored security-months and 34,251 exclusions;
- 32.3103% aggregate score coverage;
- complete-family readiness of 18.5119% for quality, 40.6285% for growth, and
  37.4091% for value;
- 246,986 normalized SEC facts without filing evidence;
- 106 missing company sources and 25 denominator securities without a V2 identity;
- the weakest industrial components are gross profitability at 32.4822%, ROIC at
  53.4960%, and free-cash-flow conversion at 58.3636% readiness;
- the existing S&P price requests begin in 2013, but that says nothing about price
  coverage for the future expanded-universe denominator.

No V2 threshold or result may be rewritten as part of remediation.

## Ordered work packages

### W0 — Expanded-universe denominator

Acquire or produce two independently rebuilt, identical monthly ledgers for
`us-listed-common-equity-pit-v1` covering every information boundary from 2017-01
through 2025-06. The evidence must establish historical identity, domicile, security
type, primary exchange, listing and delisting episodes, accounting branch, sector,
availability time, and source hashes.

W0 passes only when the structural feasibility audit returns
`PASS_STRUCTURALLY_FEASIBLE`. It is the sole blocking predecessor for W1-W6.

### W1 — Identity and classification completion

Map every expected security episode to a stable security ID, issuer ID, historical
ticker, CIK where applicable, branch, sector, and point-in-time source. Preserve
delisted securities and unresolved evidence as explicit dispositions. Do not replace
historical names with current tickers or constituents.

Acceptance requires 100% final identity/classification dispositions and at least 98%
known point-in-time branch/subtype in every month.

### W2 — Price and corporate-action history

Acquire adjusted and raw daily price evidence for every expected identity episode
and SPY. Each security must have at least 252 valid pre-boundary sessions wherever its
listing history permits, including at least 252 sessions before the first January
2017 cohort for securities already listed then. Preserve ticker changes, splits,
dividends, mergers, and delistings with point-in-time lineage.

No security may leave the expected denominator because a price is missing. Missing
history receives an explicit reason and counts against later coverage gates.

### W3 — Filing and SEC concept evidence

Extend company submissions, filing indexes, and fact evidence far enough before the
first cohort to support prior-quarter, prior-TTM, and balance-sheet history. Every
selected fact must bind accession, accepted time, period, unit, revision, tag, and
source snapshot. Resolve filing evidence before adding new formulas.

Tag expansion is based only on accounting equivalence and point-in-time coverage.
Returns, Rank IC, spreads, and portfolio results are prohibited.

### W4 — Industrial quality repair

Prioritize gross profit, total assets, operating income/EBIT, cash from operations,
capital expenditure, debt, cash, tax, interest, and share history. The first target is
the quality family because its V2 complete-family readiness was only 18.5119%.

Gross profitability, ROIC, and free-cash-flow conversion must retain explicit
numerator, denominator, period alignment, stale-filing, and missing-source reasons.

### W5 — Specialist branch repair

Acquire and reconcile loans, deposits, credit-loss provisions, net interest income,
premiums, claims, investment income, REIT property values, real-estate sale gains,
cash flow, and diluted share history. Subtype-specific formulas remain inherited from
V2; cross-branch normalization and industrial fallback remain prohibited.

### W6 — Readiness and reproducibility

Rebuild classifications, facts, features, eligibility, and dispositions twice from
clean inputs. Before scoring is authorized, the readiness evidence must demonstrate:

- at least 90% overall monthly score coverage;
- at least 80% score coverage in every active branch every month;
- at least 20 eligible names in every active branch every month;
- at least five represented branches and sectors every month;
- 100% final dispositions with stable reason codes;
- two identical rebuilds;
- zero fallback, outcome, or post-boundary access.

## Stop conditions

Stop and preserve evidence if structural feasibility fails, a source cannot prove
historical membership, a populated branch is below 25 expected names, the two
rebuilds differ, or any outcome field is accessed. Do not lower gates, shrink the
denominator, consolidate branches, or change formulas inside this version.

## Activation boundary

This contract is a blocked work specification, not an acquisition authorization. A
new, hash-bound data-acquisition authorization may be created only after W0 passes.
Score rebuilding requires a later readiness authorization. Any shadow schedule must
be prospective after an executable lock; July 2026 remains non-backfillable.
