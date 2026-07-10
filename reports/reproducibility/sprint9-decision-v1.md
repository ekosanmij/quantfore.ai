# Sprint 9 Decision Report v1

`claims_eligible=false`

```yaml
report_version: sprint9-decision-v1
generated_at: 2026-07-10T17:45:24Z
decision: EXPAND_DATA_AND_IMPLEMENT_MODEL_V2_BEFORE_SHADOW_TESTING
sprint8_signal_broad: false
sprint8_signal_reliable: false
sprint8_signal_investable: false
model_v2_implementation: GO_CONDITIONAL_ENGINEERING_ONLY
shadow_testing_now: NO_GO_NOT_READY
model_promotion: NO_GO
product_use: NO_GO
sprint9_status: COMPLETE
```

- Machine-readable decision: [`sprint9-decision-v1.json`](sprint9-decision-v1.json)
- Model V2 contract: [`multifactor-v2-hypothesis-contract.md`](../../experiments/multifactor-v2-hypothesis-contract.md)
- Shadow-ledger contract: [`shadow-ledger-v1.md`](../../docs/research/shadow-ledger-v1.md)

## Answer to the Sprint 9 question

> **No. Sprint 8's signal was not broad, reliable, or investable enough to justify
> promotion, tuning, shadow activation, or product use. Sprint 9 does justify one
> bounded next step: expand the point-in-time accounting data, implement the locked
> branch-aware Model V2 without using returns, and proceed to forward shadow testing
> only if the pre-outcome engineering gates pass.**

Sprint 8 remains an engineering reproducibility success. It proves that the warehouse,
point-in-time lineage, feature construction, predictions, outcomes, and reports can be
rebuilt identically. It does not prove a useful investment signal.

The positive reported Rank IC is not enough to reverse that conclusion. It is
calculable in only nine two-to-four-name pre-holdout months. The four represented
holdout months are singletons, so holdout Rank IC is not calculable. The selected
basket underperforms SPY before costs and underperforms the same eligible cohort held
equal weight.

## Evidence that determines the decision

| Question | Evidence | Verdict |
| --- | --- | --- |
| Is the process reproducible? | Two clean Sprint 8 rebuilds matched all ten closure invariants. | **YES** |
| Is final-score coverage broad? | 60 / 50,600 stock-months, 0.1186%; 59 / 102 months have no score; 0 / 102 months reach 90%. | **NO** |
| Are exclusions understood? | Sprint 9.2 assigns every expected stock-month one primary disposition and retains component reason codes. | **YES, ROOT CAUSE KNOWN** |
| Is the model genuinely five-family in practice? | Quality is available in 15 / 50,600 universe rows and 0 / 60 evaluated rows; every evaluated score has four families. | **NO** |
| Is efficacy established on untouched evidence? | Mean Rank IC 0.6889 exists in nine tiny pre-holdout cross-sections; no holdout month has calculable Rank IC and the HAC/non-overlap t-statistic is null. | **NO** |
| Is portfolio value established? | Gross excess -6.37%; 25 bps net excess -6.41%; benchmark hit rate 39.53%. | **NO** |
| Does selection beat the eligible cohort? | Selected minus eligible equal weight is -0.42% overall and -2.03% in multi-name months. | **NO** |
| Is the basket diversified? | One selected name and one sector in every period; no bottom bucket. | **NO** |
| Is liquidity the observed failure? | All 43 selected rows pass $25m trailing median daily dollar volume. | **NO** |
| Is the current Financials treatment valid? | 51 / 60 rows are SIC 6798 REITs and nine are a SIC 6331 insurer, but the current code groups them through broad Financials rules. | **NO** |
| Can forward evidence be recorded safely? | Sprint 9.7 implements a complete immutable cohort ledger and passes its integrity tests. | **YES, ONCE EXECUTABLE LOCK EXISTS** |

The observed failure is structural rather than a transaction-cost problem. The
universe and prediction-date prices are complete, but accounting history,
classification, applicability, and family coverage reduce 50,600 expected rows to
five securities and 60 evaluated rows. Costs add only 3.49 basis points of average drag
at the 25 bps setting; gross performance is already negative.

## Decision by possible path

| Path | Decision | Reason |
| --- | --- | --- |
| Promote Sprint 8 | **NO-GO** | Frozen engineering and model gates do not pass. |
| Tune weights or features against 2017–2025 returns | **NO-GO** | The period is exposed and the cohort is too narrow; tuning would contaminate the next hypothesis. |
| Start shadow testing immediately | **NO-GO — NOT READY** | Branch data, Model V2 formulas, coverage proof, and the executable lock do not exist yet. |
| Expand point-in-time data | **GO — REQUIRED** | `INSUFFICIENT_HISTORY`, sparse accounting families, and subtype treatment are the binding coverage failures. |
| Implement locked Model V2 | **GO — CONDITIONAL ENGINEERING ONLY** | The coverage/classification hypothesis is evidence-based and outcome-blind. It is not an alpha claim. |
| Pause return-driven modeling | **GO** | No more performance-guided choices may be made from Sprint 8 or other exposed historical outcomes. |
| Move quant output into product workflows | **NO-GO** | Ranking usefulness, portfolio construction, and claims eligibility are not established. |
| Develop thesis-memory product features in parallel | **OPTIONAL, SEPARATE SCOPE** | This may proceed only if it does not present the quant research score as investment evidence. |

## What Sprint 9 completed

| Sprint item | Deliverable | Result |
| --- | --- | --- |
| 9.1 | Evidence readout | Engineering reproducibility passed; model promotion failed. |
| 9.2 | Coverage and cohort audit | Every stock-month reconciled; breadth failure explained. |
| 9.3 | Factor-family diagnostic | Quality absent; fundamentals sparse; no family established as useful. |
| 9.4 | Investability diagnostic | `NOT_INVESTABLE_ON_OBSERVED_EVIDENCE`. |
| 9.5 | Model V2 hypothesis contract | Hypothesis, change envelope, windows, gates, and anti-overfitting rules locked. |
| 9.6 | Sector-specific treatment | Separate Financial/REIT subtype branches required; design not yet implemented. |
| 9.7 | Shadow prediction ledger | Schema, sealing API, maturity link, CLI, exact schedule, and tests implemented. |
| 9.8 | Decision report | Expand data and implement V2 before any shadow activation. |

Sprint 9 therefore closes with a research decision, not a promoted model.

## Sprint 10 definition

### Theme

> **Model V2 Pre-Shadow Readiness: implement point-in-time subtype branches and broad
> five-family coverage, prove them without return tuning, then create the executable
> lock.**

### Work packages

1. **Point-in-time subtype classification**
   - Implement the append-only `sec-sic-financial-subtype-v2` ledger.
   - Route SIC `6798` to unresolved REIT before broad Financials logic.
   - Resolve banks, P&C insurers, life/health insurers, broker-dealers, asset managers,
     equity REITs, and mortgage REITs from timestamped evidence.
   - Retain unknown subtypes as explicitly excluded research rows.

2. **Accounting data and history expansion**
   - Acquire and reconcile the branch-specific concepts allowed by the Sprint 9.6
     contract.
   - Build genuinely available prior-filing history needed for TTM and growth features.
   - Preserve source IDs, hashes, availability timestamps, revisions, units, and
     formula lineage.
   - Make no source or formula decision from future returns, Rank IC, quintiles, or
     ablations.

3. **Branch-aware Model V2 implementation**
   - Implement exact formulas, applicability, missingness, and branch-only
     normalization under the Sprint 9.5 envelope.
   - Require all five fixed 20% families, at least 80% component coverage, at least 60%
     of each family's required components, and no weight renormalization.
   - Reject cross-branch fallback and branches with fewer than 20 valid securities.

4. **Outcome-blind engineering proof**
   - Run two clean rebuilds from frozen inputs.
   - Reconcile every expected member to `SCORED` or a stable exclusion reason.
   - Demonstrate at least 98% known branch/subtype coverage and at least 90% final-score
     coverage in every pre-outcome readiness cohort.
   - Demonstrate at least 80% score coverage inside each active branch, at least 20
     eligible names per active branch, and at least five branches and five sectors.
   - Do not read retrospective V2 return aggregates while making implementation
     decisions.

5. **Executable lock and operations rehearsal**
   - Commit the complete implementation first.
   - Create the executable JSON lock with the implementation commit, formula,
     classification, source-manifest, evaluation-code, report-schema, prediction-date,
     and portfolio-notional hashes.
   - Commit only the lock file in the next commit; verify no executable source changed.
   - Rehearse the shadow CLI on synthetic/non-outcome fixtures and reproduce its batch
     hash.

6. **Conditional first shadow batch**
   - Seal the `2026-07-31` cohort only if every readiness condition above passes before
     its locked information timestamp.
   - If the scheduled batch is missed or fails, record the failure. Do not reconstruct
     it after outcomes become available and do not move the window silently.

## Entry gate for shadow testing

Shadow testing is authorized only when all conditions are true:

- the Model V2 implementation and exact formulas are committed;
- the classification, source, formula, prediction schedule, evaluation code, report
  schema, costs, and notional are hash-bound in an executable lock;
- two clean rebuilds agree;
- the point-in-time classification, coverage, branch-size, breadth, and exclusion gates
  pass without return access;
- every scored row contains all five fixed-weight families and every excluded row has a
  stable reason;
- the shadow ledger accounts for every expected member, with `product_label IS NULL`;
  and
- the lock-only commit is the only change after the locked implementation commit.

The existence of the Sprint 9.7 code is not itself authorization. Its current status is
`implementation_ready_not_prediction_authorized`.

## Stop conditions

Stop Model V2 before shadow activation if any of the following occurs:

- broad final-score coverage cannot reach 90% without weakening the locked rules;
- fewer than five viable branches or sectors can be represented;
- the score remains a momentum/risk model in practice because accounting families are
  unavailable;
- point-in-time subtype evidence cannot be established;
- leakage, revision, survivorship, or source-hash integrity fails;
- the implementation requires return-driven formula, weight, threshold, or eligibility
  changes; or
- the executable lock is not committed before the first scheduled prediction boundary.

Any such result is informative. It means pause the quant model or define a genuinely new
future hypothesis and window; it does not permit lowering the Sprint 9.5 thresholds.

## Claims and product boundary

`claims_eligible=false` remains mandatory. Sprint 9 does not establish alpha,
outperformance, ranking usefulness, investability, suitability, capacity, or investment
advice. Model V2 implementation and shadow collection remain internal research.

Even a future pass of the Model V2 promotion gates would require separate data-rights,
compliance, product-governance, and communication review before any external claim or
product-facing label. The shadow ledger therefore stores internal `research_score` and
`research_label` fields while structurally withholding `product_label`.

## Evidence binding

The machine-readable companion binds the Sprint 8 closure, Sprint 9.1–9.7 evidence,
Model V2 design lock, shadow-ledger implementation, and claims policy by SHA-256. The
historical warehouse remains derived evidence bound through the existing reproducibility
and report hashes; it is not copied into this decision report.
