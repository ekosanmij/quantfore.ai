# Model V3 Expanded-Universe Structural Feasibility Contract

Status: `SPECIFIED_NOT_RUN`
Claims eligible: `false`
Shadow executable: `false`

## Purpose

This is the mandatory, low-cost audit before any Model V3 accounting acquisition or
score rebuild. It answers one question only: can the proposed broader point-in-time
universe make the unchanged branch-size gates mathematically attainable?

It does not measure score coverage, feature quality, returns, alpha, liquidity,
portfolio performance, or shadow readiness.

## Fixed input population

The candidate universe is `us-listed-common-equity-pit-v1` as defined by the Model V3
hypothesis contract. The audit evaluates every monthly information boundary from
2017-01 through 2025-06 using information available at that boundary.

An expected member is included in the denominator when its point-in-time evidence
shows all of the following:

1. US domicile;
2. active primary listing on a US national securities exchange;
3. operating-company common stock or REIT common stock;
4. not the SPY benchmark and not an excluded instrument type;
5. an identity episode valid at the monthly boundary.

The expected denominator is fixed before inspecting prices, filings, features,
scores, or outcomes. A missing downstream input creates a later exclusion reason; it
does not erase an expected member.

## Required ledger

The audit output must contain one row for every expected security-month with:

- information boundary and stable security/issuer IDs;
- historical ticker, domicile, primary exchange, and security type;
- membership and identity effective dates plus availability timestamp;
- point-in-time accounting branch and GICS sector;
- source snapshot IDs and hashes;
- final structural disposition and stable reason code.

Unknown or conflicting identity, listing, type, domicile, branch, or sector evidence
must remain visible as an explicit disposition. Historical delistings are preserved.

## Branch activation and floor

A branch is structurally active in a month when at least one expected member is
assigned to it. It may not be deactivated because it is small or because downstream
data are absent.

The minimum expected membership is derived, not tuned:

```text
required eligible names = 20
minimum permitted branch coverage = 0.80
minimum expected names = ceil(20 / 0.80) = 25
```

Therefore every structurally active branch must have at least 25 expected names in
every month. Twenty expected names would leave no allowance for the unchanged 80%
coverage gate and is not sufficient for this feasibility audit.

## Outcome-blind gates

| ID | Gate |
| --- | --- |
| F0 | Zero outcome, return, Rank IC, spread, portfolio, or post-boundary access. |
| F1 | Every structurally active branch has at least 25 expected names every month. |
| F2 | The theoretical eligible count at 80% coverage is at least 20 for every active branch-month. |
| F3 | At least five structurally active branches are represented every month. |
| F4 | At least five GICS sectors are represented every month. |
| F5 | Every expected security-month has exactly one final structural disposition and stable reason code. |
| F6 | At least 98% of expected members have a known point-in-time branch/subtype every month. |
| F7 | Two clean rebuilds of ledgers, counts, dispositions, and report hashes are identical. |

All gates are conjunctive. `NOT EVALUABLE` is a failure. There is no warm-up or
small-branch exception.

## Permitted evidence

- historical listing and delisting records;
- point-in-time security master and identifier history;
- security type, domicile, primary exchange, SIC/NAICS, GICS, and REIT subtype
  evidence available at the information boundary;
- source revisions and availability timestamps needed to prove lineage.

## Prohibited evidence

- prices, returns, forward returns, benchmark returns, or corporate performance;
- SEC fact availability or completeness;
- feature, family, eligibility, or score coverage;
- market-cap, liquidity, profitability, survival, or outcome filters;
- current constituents substituted for historical identities;
- any rule chosen after viewing a branch's return or scoring result.

## Decision states

- `PASS_STRUCTURALLY_FEASIBLE`: F0-F7 all pass. Proceed to a separately locked data
  readiness and acquisition step; scoring remains unauthorized.
- `FAIL_UNIVERSE_STILL_TOO_SMALL`: any F1-F4 gate fails. Stop before accounting
  acquisition. A different universe or newly versioned normalization design is
  required.
- `FAIL_LINEAGE_OR_REPRODUCIBILITY`: any F0 or F5-F7 gate fails. Repair the evidence
  or implementation without outcomes, then rerun the same locked audit.

The complete report, including failed months and branches, must be retained. Passing
this audit cannot be used to rewrite the frozen Model V2 failure.

## Next-lock boundary

Only `PASS_STRUCTURALLY_FEASIBLE` permits a data-acquisition plan. A future executable
V3 lock still requires the original coverage gates, two identical full rebuilds,
zero fallback and outcome access, and a prospective prediction schedule. July 2026
remains non-backfillable.
