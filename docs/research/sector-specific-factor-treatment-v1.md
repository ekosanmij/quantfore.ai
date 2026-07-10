# Sector-Specific Factor Treatment v1

`claims_eligible=false`

- Contract status: `DESIGN_LOCKED_NOT_IMPLEMENTED`
- Decision: `SEPARATE_SUBTYPE_BRANCHES_NOT_ONE_MONOLITHIC_FINANCIALS_BRANCH`
- Machine-readable companion: [`sector-specific-factor-treatment-v1.json`](sector-specific-factor-treatment-v1.json)
- Governing evidence: Sprint 9.2, 9.3, and 9.4

## Decision

> **Financials and REITs must not use the generic industrial accounting model. They
> also must not share one broad Financials model. Future scoring requires separate
> point-in-time branches for banks, P&C insurers, life/health insurers,
> broker-dealers, asset managers, equity REITs, and mortgage REITs. An unresolved
> subtype receives no eligible score.**

This contract formally handles the Sprint 8 defect without rewriting Sprint 8.
The existing ledger remains immutable. New classification records, features,
weights, and predictions must use a new experiment and version.

The immediate operational rule is simple:

1. Exclude every Financials-labelled or REIT security from the generic industrial
   final score.
2. Continue storing universal momentum and risk diagnostics where inputs are valid.
3. Emit no eligible multi-factor score until the security has a point-in-time subtype,
   the branch has a validated feature set, and the branch-specific cohort is broad
   enough to normalize.

## What Sprint 8 actually evaluated

Sprint 8 did not evaluate a broad Financials model. It evaluated 60 stock-months
from five names:

| Stored subtype evidence | Stock-months | Unique evaluated names | Meaning |
| --- | ---: | ---: | --- |
| SIC `6798` | 51 | 4 (`AMT`, `EXR`, `REG`, `VNO`) | REIT observations routed through the broad Financials mask. |
| SIC `6331` | 9 | 1 (`HIG`) | P&C insurance observations routed through the same mask. |
| Banks, brokers, asset managers, other insurers | 0 | 0 | No evaluated evidence. |

The full warehouse contains 8,586 Financials-labelled stock-months from 101
securities. Of these, 2,920 stock-months from 32 securities carry SIC `6798`.
The SEC defines SIC `6798` as **Real Estate Investment Trusts**, while the current
coarse mapper sends the entire `6700–6799` range to Financials. The current feature
mask then looks for GICS-like industry `601010` or text containing `REIT`, even
though the stored `industry` field is the SIC string `6798`.

The resulting path is:

```text
SEC SIC 6798
  -> SEC_SIC_TO_GICS_V1 broad range 6700-6799
  -> sector = Financials, industry = "6798"
  -> REIT test for industry "601010" does not match
  -> nine-component Financials mask applies
  -> applicable denominator falls from 19 to 10
  -> value + growth + momentum + risk can pass; quality is absent
```

Every one of the 60 evaluated scores has exactly four families and zero quality
coverage. Therefore the Sprint 8 result cannot be interpreted as evidence for banks,
insurers generally, Financials generally, or a properly specified REIT model.

## Current masks and why they are insufficient

The frozen `multifactor-v1` implementation contains 19 components.

| Existing rule | Components marked `NOT_APPLICABLE` |
| --- | --- |
| Broad Financials mask | `fcf_yield`, `ebit_ev`, `roic`, `gross_profitability`, `fcf_conversion`, `inverse_accruals`, `inverse_leverage`, `fcf_growth`, `margin_change` |
| REIT mask | `fcf_yield`, `ebit_ev`, `roic`, `fcf_conversion`, `fcf_growth` |

The broad Financials mask correctly recognizes that industrial debt, working
capital, enterprise value, and free cash flow have different meanings for regulated
financial companies. It is still not a valid model because it treats very different
businesses alike and lets denominator shrinkage create score eligibility.

The REIT rule is also too permissive even if correctly routed. GAAP net income and
EPS are affected by real-estate depreciation, and acquisition-driven revenue growth
is not equivalent to same-store operating growth. Nareit created FFO specifically as
a supplemental REIT operating-performance measure to address limitations caused by
real-estate depreciation. FFO/AFFO, NOI, occupancy, property leverage, and payout
coverage must be modeled explicitly rather than inferred from the retained generic
features.

## Applicability status semantics

Future feature generation must use these meanings:

| Status | Meaning | Eligibility treatment |
| --- | --- | --- |
| `APPLICABLE_UNIVERSAL` | Price-derived feature has the same definition for every branch. | May enter branch score if history is valid. |
| `APPLICABLE_WITHIN_BRANCH` | Existing formula is meaningful only within one subtype branch. | Normalize only against that branch; never against industrials or another subtype. |
| `NOT_APPLICABLE_STRUCTURAL` | Economic concept is inappropriate for the subtype. | Audit the exclusion; do not count it as missing or silently replace it. |
| `REPLACE_WITH_BRANCH_FEATURE` | Generic concept is insufficient and a specified branch measure is required. | Generic component cannot enter eligibility. Missing replacement counts as branch-required missing. |
| `RESEARCH_ONLY_NOT_ELIGIBILITY` | Diagnostic may be calculated but cannot produce a final score. | Store with lineage; weight is zero and no prediction is emitted. |
| `EXCLUDE_PENDING_SUBTYPE` | The issuer cannot be routed reliably. | No final score. |

`NOT_APPLICABLE` must never be a way to make a sparse issuer easier to score. The
industrial feature is outside the branch schema, while every required branch feature
stays in the branch denominator. A missing bank CET1 ratio, insurer combined ratio,
or REIT FFO measure is `BRANCH_REQUIRED_FEATURE_MISSING`, not
`NOT_APPLICABLE`.

## Treatment of the current 19 components

Momentum and risk are universal when their price history is valid:

- `momentum_6_1`
- `momentum_12_1`
- `volatility_126d`
- `beta_252d`
- `downside_volatility_126d`
- `maximum_drawdown_252d`

They must still be normalized inside the active branch. A branch may not fall back
to the industrial universe merely because its own cross-section is small.

For banks, insurers, broker-dealers, and asset managers, only `earnings_yield` and
`eps_growth` may be retained, and only within the correct subtype. Generic
`revenue_growth` must be replaced with the relevant loan, premium, net-revenue,
fee-revenue, or AUM measure. The remaining ten generic accounting components are
structurally inappropriate.

For equity and mortgage REITs, all 13 generic accounting components require
replacement. Price momentum and risk may remain, but a market-only REIT score is
not eligible.

The complete branch-by-branch partition of all 19 current features is locked in the
JSON companion. Each branch partitions every feature into exactly one status.

## Explicit classification rules

### Routing precedence

1. Use a point-in-time explicit regulated-entity or REIT subtype when supported by a
   source snapshot available at the prediction timestamp.
2. Otherwise apply an exact SIC rule.
3. Use a broad sector label only to identify that specialized routing is required,
   never to select a Financials accounting model.

Classification conflicts or unavailable subtype evidence produce no eligible score.
Corrections are append-only and versioned; they do not mutate prior classifications.

### Exact SIC routing floor

| SIC evidence | Provisional branch | Behavior before explicit subtype/features |
| --- | --- | --- |
| `6798` | `REIT_SUBTYPE_UNRESOLVED` | Route before Financials; distinguish equity vs mortgage REIT or exclude. |
| `6020–6099` | `BANK` | Exclude until bank branch is implemented. |
| `6200`, `6211` | `BROKER_DEALER` | Exclude until broker branch is implemented. |
| `6282` | `ASSET_MANAGER` | Exclude until asset-manager branch is implemented. |
| `6311`, `6321`, `6324` | `INSURER_LIFE_HEALTH` | Exclude until life/health branch is implemented. |
| `6331`, `6351`, `6361` | `INSURER_P_AND_C` | Exclude until P&C branch is implemented. |
| `6199`, `6399`, `6411`, `6792`, `6799` | `OTHER_FINANCIAL_UNRESOLVED` | Require explicit subtype; do not guess. |

SIC is a routing floor, not sufficient proof of comparability. For example, SIC
`6798` establishes REIT status but does not reliably distinguish an equity REIT from
a mortgage REIT. The explicit subtype source must be stored with effective dates,
availability timestamp, snapshot ID, and hash.

## Required model branches

### Banks

Industrial FCF, EBIT/EV, ROIC, accrual, and debt-to-assets factors are invalid because
deposits, borrowings, credit losses, regulatory capital, and net interest income are
operating fundamentals. The candidate branch is:

| Family | Candidate features |
| --- | --- |
| Value | Earnings yield; inverse price/tangible book. |
| Quality | ROA; ROE; net interest margin; inverse efficiency ratio; CET1 ratio; inverse nonperforming-loan ratio; inverse net-charge-off ratio. |
| Growth | Loan growth; deposit growth; tangible book value/share growth; EPS growth. |
| Momentum/risk | The six universal price components. |

The FDIC Quarterly Banking Profile organizes bank analysis around earnings, net
interest margin, loan and deposit activity, asset quality, capital, and liquidity.
These concepts—not industrial free cash flow—form the branch feature basis.

### P&C insurers

Premiums, underwriting losses, expenses, reserves, investment income, and statutory
capital drive the business. The NAIC defines the combined ratio as the sum of loss
and expense ratios and an indicator of insurance-company profitability.

| Family | Candidate features |
| --- | --- |
| Value | Earnings yield; inverse price/book. |
| Quality | Inverse combined ratio; inverse loss ratio; favorable reserve development; ROE; risk-based capital ratio. |
| Growth | Net premiums written growth; book value/share growth; EPS growth. |
| Momentum/risk | Universal price components. |

### Life and health insurers

Do not apply a P&C combined ratio. Use a distinct branch with premium growth,
book-value growth, ROE, investment yield, reserve adequacy, and risk-based capital.

### Broker-dealers

Client financing, trading inventories, compensation, net capital, and volatile
trading revenue make the industrial debt and cash-flow framework inappropriate.
Candidate measures include earnings yield, tangible-book valuation, ROE, inverse
compensation ratio, net capital ratio, client-asset growth, and net-revenue growth.

### Asset managers

Balance-sheet assets are not client AUM. The SEC's Form ADV separately reports
regulatory assets under management. Candidate measures include RAUM/AUM growth,
net flows, fee-revenue growth, operating margin, compensation ratio, ROE, and
fee-related earnings yield. Form ADV lineage must be mapped to the public issuer and
made point-in-time before the measure can be used.

### Equity REITs

Equity REITs belong in a Real Estate branch, not a generic Financials branch. GICS
describes the Real Estate sector as including equity REITs. Candidate features are:

| Family | Candidate features |
| --- | --- |
| Value | FFO yield; AFFO yield; NAV discount; dividend yield. |
| Quality | Occupancy; interest coverage; inverse net debt/EBITDAre; AFFO payout headroom; fixed-rate debt share. |
| Growth | Same-store NOI growth; FFO/share growth; AFFO/share growth. |
| Momentum/risk | Universal price components. |

FFO must follow a reproducible definition and remain supplemental to GAAP. AFFO is
less standardized, so the issuer definition and reconciliation must be stored for
every observation.

### Mortgage REITs

Mortgage REITs must not be normalized with property-owning equity REITs. Candidate
features include inverse price/book, earnings yield, book-value/share growth, net
interest spread, inverse economic leverage, liquidity, credit-loss coverage, and
inverse duration gap.

## Scoring and normalization rules

The following design constraints apply before any branch may emit predictions:

- One and only one branch per security-month.
- No accounting-feature normalization across branches.
- No fallback from a small specialized branch to the industrial universe.
- Minimum branch cross-section: 20 securities for a monthly normalized score.
- Market-only final scores are prohibited.
- At least two accounting families are required.
- Value, quality, momentum, and risk are provisionally mandatory; growth is optional.
- Branch weights must be defined and locked before outcome access. Generic 20% family
  weights are not inherited automatically.
- The provisional family and cohort thresholds must be adopted or replaced explicitly
  in the Sprint 9.5 Model V2 hypothesis lock.

The branch cross-section rule prevents a repeat of Sprint 8, where one-to-four-name
cohorts produced no bottom quintile and complete single-name concentration.

## Required reason codes

Every excluded component or security-month must retain at least one stable code:

- `CLASSIFICATION_CONFLICT`
- `CLASSIFICATION_SOURCE_UNAVAILABLE`
- `FINANCIAL_SUBTYPE_UNKNOWN`
- `REIT_SUBTYPE_UNKNOWN`
- `FEATURE_STRUCTURALLY_NOT_APPLICABLE`
- `BRANCH_FEATURE_NOT_DEFINED`
- `BRANCH_REQUIRED_FEATURE_MISSING`
- `BRANCH_NORMALIZATION_COHORT_TOO_SMALL`
- `SECTOR_BRANCH_EXCLUDED`
- `RESEARCH_ONLY_NOT_ELIGIBILITY`

The reason must include branch, feature, rule version, classification ID, and source
lineage. A bare `NOT_APPLICABLE` string is insufficient for the next model version.

## Implementation boundary and migration

This contract intentionally does not edit the frozen Sprint 8 feature code or
warehouse. Implementing these rules changes model inputs and eligibility and therefore
belongs to a new experiment.

Required sequence:

1. Create an append-only classification version with `sector_branch` and `subtype`.
2. Route SIC `6798` to unresolved REIT before any broad Financials rule.
3. Audit all 32 SIC `6798` securities and every other Financials subtype point in time.
4. Acquire and reconcile branch-specific facts. SEC Companyfacts alone is not expected
   to provide standardized FFO/AFFO, statutory insurance, Call Report, or AUM data.
5. Lock Model V2 features, weights, eligibility, evaluation window, and thresholds in
   Sprint 9.5 before evaluating outcomes.
6. Rebuild under a new model and classification version.
7. Rerun Sprint 9.2 coverage, 9.3 family diagnostics, and 9.4 investability diagnostics.

Until these steps pass, Financials and REITs remain research-only and excluded from a
generic multi-factor prediction.

## Acceptance decision

| Sprint 9.6 criterion | Result |
| --- | --- |
| Invalid factors identified for banks, insurers, brokers, asset managers, and REITs | **PASS** |
| Industrial and financial-sector factors separated | **PASS** |
| Explicit applicability and exclusion rules defined | **PASS** |
| Financial-specific candidate families defined | **PASS** |
| Decision on separate branch made | **PASS — separate subtype branches required** |
| `NOT_APPLICABLE` made intentional rather than accidental | **PASS for contract; implementation pending** |
| Current model corrected retroactively | **NO — prohibited to preserve frozen evidence** |

## Primary references

- [SEC Standard Industrial Classification list](https://www.sec.gov/search-filings/standard-industrial-classification-sic-code-list) — SIC `6798` is Real Estate Investment Trusts.
- [S&P Global Industry Classification Standard](https://www.spglobal.com/spdji/en/landing/topic/gics/) — the Real Estate sector includes equity REITs and is distinct from Financials.
- [Nareit FFO definition](https://www.reit.com/glossary/funds-operation-ffo) — purpose and construction of FFO as a supplemental REIT measure.
- [FDIC Quarterly Banking Profile](https://www.fdic.gov/analysis/quarterly-banking-profile/) — bank earnings, margin, loan/deposit, asset-quality, capital, and liquidity framework.
- [NAIC insurance glossary](https://content.naic.org/glossary-insurance-terms) — combined ratio definition.
- [SEC Form ADV](https://www.sec.gov/about/forms/formadv-part1a.pdf) — regulatory assets under management reporting for investment advisers.

## Claims boundary

This is a research design and data-treatment contract. It does not establish signal
efficacy, outperformance, investability, suitability, executable capacity, or
investment advice. `claims_eligible=false` remains mandatory.
