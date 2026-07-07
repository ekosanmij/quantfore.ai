# Quantfore AI

Quantfore AI is an evidence-centric investment research system for monitoring
thesis evolution and estimating forward risk/reward in public equities. It is
designed to maintain a point-in-time representation of an investment case,
reconcile new fundamental and market evidence against that prior state, and
identify changes that warrant analytical review.

The system combines structured fundamentals, market data, corporate
disclosures, expectations, macro regimes, and portfolio context within an
auditable decision architecture. Its mandate is analytical rather than
executional: Quantfore produces traceable research outputs, calibrated signals,
and explicit uncertainty for human evaluation. Personalised suitability,
discretionary portfolio management, and trade execution sit outside the current
system boundary.

The research programme is organised around a consequential question:

> Which new evidence changes the investment thesis, how material is that
> change, and how does it alter the distribution of forward outcomes?

## Research Programme Status

Status as of July 4, 2026: **Sprint 7 is closed; Sprint 8 is in progress.**
`claims_eligible=false` remains in force, so the repository does not support
public performance or alpha claims.

- Sprint 7 passed its reproducibility gate with two clean database rebuilds.
  The point-in-time S&P 500 baseline covers `2017-01-01` through `2025-06-30`,
  with 638 securities, 1,266,438 price rows, 41,024 predictions, 40,772
  evaluated outcomes, and minimum monthly full-universe price coverage of
  `0.962451`.
- The free-data acquisition is complete for its amended personal/internal-use
  contract: 673 of 673 planned Tiingo symbols, all 754 required OpenFIGI
  queries, SEC Companyfacts and submissions for 547 of 547 resolved CIKs, and
  all 28,919 planned filing accessions accounted for (28,917 verified filing
  indexes plus two explicitly unavailable orphan accessions).
- The SEC-primary fundamentals bundle contains 764,865 filing-bound facts and
  697 dated classification records. The mature evaluation cutoff is centrally
  fixed at `2025-06-30`.
- Sprint 8 now has database-derived report verification and rebuild-program
  SHA-256 binding. Its remaining gates are the fresh-database fundamentals
  audit, holdout lock, and two matching closure rebuilds.
- Raw Tiingo, SEC, OpenFIGI, membership, identifier, and licence evidence stays
  under Git-ignored `data/raw/`; Tiingo data is not redistributed.

See the [Sprint 7/Sprint 8 progress record](docs/research/sprint7-sprint8-free-data-progress-v1.md)
and [Sprint 7 closure evidence](reports/reproducibility/sprint7-closure-v1.md)
for the exact scope, hashes, limitations, and remaining work.

## Local Setup

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e 'packages/research[dev]'
python -m pytest
```

## Smoke Check

After local setup, run this end-to-end prediction and outcome-evaluation check
from the repository root:

```bash
rm -f quantfore_research.db

python pipelines/ingest_prices_csv.py \
  data/sample/msft_spy_outcome_prices.csv

python pipelines/build_baseline_features.py MSFT \
  --asof-date 2025-12-26

python pipelines/build_baseline_score.py MSFT \
  --asof-date 2025-12-26

python pipelines/evaluate_predictions.py --benchmark SPY

python -m pytest
```

The smoke check ingests aligned synthetic MSFT and SPY prices, freezes the
source CSV under `data/raw/`, calculates baseline price features, stores an
immutable 126-day prediction, evaluates it against SPY, records source-snapshot
lineage, and runs the test suite. The evaluation should report:

```text
evaluated prediction ticker=MSFT horizon=126d
entry_date=2025-12-29 exit_date=2026-06-23
realised_return=0.12 benchmark_return=0.07
excess_return=0.05 max_drawdown=-0.2
```

`data/sample/msft_spy_outcome_prices.csv` is entirely synthetic weekday-only
sample data. Its prices, volumes, returns, and drawdowns are fictional and must
not be used for performance claims.

## Investment Research Thesis

The binding constraint in fundamental investing is rarely access to
information. It is the ability to preserve the original investment thesis,
distinguish signal from narrative noise, and determine whether subsequent
evidence changes the probability or magnitude of the relevant outcomes.

Quantfore is being developed as the analytical layer for that process. It will
integrate company fundamentals, disclosures, earnings transcripts, consensus
expectations, valuation, price behaviour, market regimes, and portfolio
exposures into a longitudinal model of each security. Every material change
should be attributable to dated source evidence and evaluated relative to what
was knowable at the time.

The intended output is not a context-free score. It is a structured account of
what changed, why the change is decision-relevant, which assumptions are now
under pressure, how confidence has been revised, and whether prospective
asymmetry has improved or deteriorated.

## System Mandate

Quantfore's domain is investment intelligence and decision support:

- Maintain a versioned, evidence-linked representation of the company thesis.
- Detect fundamental, expectations, regime, and market-state changes.
- Rank forward opportunities using calibrated, benchmark-relative estimates.
- Decompose risk at security and portfolio level.
- Expose the evidence, model provenance, uncertainty, and counterarguments
  behind every analytical output.

The current mandate excludes personalised suitability determinations,
discretionary management, order generation, and trade execution. These are
architectural and governance boundaries, not merely marketing qualifications:
the research system is designed to inform accountable human judgement, with no
unsupported inference of certainty or investment performance.

## Initial Research Domain

The initial user is a research-intensive, self-directed investor who allocates
meaningful capital to individual equities or ETFs and requires a more rigorous
process for initiation, monitoring, sizing review, and thesis invalidation.

The initial scope is deliberately constrained:

- Market: US equities, initially the S&P 500 and subsequently the Russell 1000
  once data and pipeline controls are stable.
- User geography: UK/EU initially, with the US considered in a later product
  phase.
- Forecast horizon: approximately 3 to 12 months.
- Monitoring cadence: weekly signal review with a monthly portfolio-formation
  baseline.
- Product boundary: general research and analytical decision support, subject
  to explicit claims, licensing, and compliance controls.

## Differentiated Research Assets

The central research hypothesis is that modelling **thesis change** can add
information beyond static factor exposures, retrospective summaries, and
generic financial question answering. That hypothesis will be accepted only if
it demonstrates incremental predictive or workflow value under the validation
programme.

The intended proprietary system comprises:

- **Company Thesis Memory Graph** — a temporally versioned representation of
  thesis assertions, key performance indicators, management guidance, risks,
  catalysts, and contradictory evidence.
- **Thesis Drift Index** — a calibrated measure of the direction, magnitude,
  and stability of change in the investment case.
- **Regime Vulnerability Map** — security-specific sensitivity to rates,
  inflation, energy, currencies, credit, volatility, liquidity, and breadth.
- **Prediction Ledger** — an immutable record binding scores and explanations
  to model versions, feature values, universe membership, source snapshots, and
  subsequently realised outcomes.
- **User Decision Graph** — an interaction layer for studying how evidence,
  alerts, and model revisions affect research behaviour and decision quality.

## Planned Analytical Surfaces

The product interface is intended to make the research process inspectable
rather than obscure model complexity behind a single recommendation:

- **Opportunity Monitor** — cross-sectional rankings, calibrated confidence,
  score revisions, dominant factor contributions, and active risk flags.
- **Security Research View** — the current thesis state, competing bull and
  bear hypotheses, valuation context, Thesis Drift Index, invalidation
  conditions, and primary-source evidence.
- **Change Attribution Feed** — material weekly revisions with the observations
  and model components responsible for each change.
- **Portfolio Lens** — concentration, factor and regime exposure, correlation,
  overlap, liquidity, and marginal risk contribution.
- **Model Evidence** — versioned out-of-sample results, benchmark comparisons,
  regime and sector decomposition, calibration, failed tests, and known model
  limitations.
- **Research Copilot** — source-grounded retrieval and synthesis across filings,
  transcripts, and structured evidence, with citations and provenance.
- **Surveillance Alerts** — thesis drift, estimate revisions, factor migration,
  earnings evidence, and watchlist state changes.

## Research and Model Architecture

Quantfore is model-led, but the governing objective is epistemic traceability:
an output that cannot be reconstructed from its information set, feature
values, model version, and source evidence is not admissible.

The target architecture is layered:

1. Source ingestion for prices, corporate actions, fundamentals, filings,
   transcripts, estimates, macro series, and company events.
2. Point-in-time normalisation with event time, publication time, vendor
   availability time, ingestion time, source hashes, and licence metadata.
3. A survivorship-aware security master, historical universe membership, and
   immutable raw-data lineage.
4. Interpretable factor baselines spanning value, quality, momentum, revisions,
   risk, liquidity, and macro sensitivity.
5. Cross-sectional ranking models for benchmark-relative return and downside
   distributions, with calibration and ablation against simple baselines.
6. Regime inference and security-level vulnerability modelling.
7. Portfolio risk decomposition across volatility, drawdown, beta,
   correlation, concentration, factor exposure, and liquidity.
8. Retrieval and language-model components for evidence extraction,
   normalisation, contradiction detection, synthesis, and explanation.
9. A governance layer that records forecasts before outcomes are observable and
   binds research artefacts to data and code provenance.

Language models are therefore evidence-processing components, not independent
sources of investment truth. Their outputs remain subordinate to source
grounding, deterministic controls, quantitative validation, and model-risk
review.

## Validation Standard

Research claims graduate only through pre-specified evidence gates. The
validation framework requires:

- Point-in-time information sets and explicit availability semantics.
- Historical universe reconstruction and survivorship-bias controls.
- Purged or embargoed temporal splits where overlap creates leakage risk.
- Transaction-cost, turnover, liquidity, and capacity assumptions appropriate
  to the tested strategy.
- Locked data snapshots, experiment contracts, model versions, and holdouts.
- Reproducible results from clean, independent database rebuilds.
- Comparison with credible naïve, factor, and portfolio-construction baselines.
- Stability analysis across time, sector, market regime, and forecast horizon.
- Forward observation before any externally communicated performance claim.

The programme evaluates distinct propositions rather than collapsing them into
a single backtest:

- **Signal validity:** does the system contain incremental information about
  forward relative return, downside, or thesis deterioration?
- **Ranking efficacy:** does cross-sectional ordering remain useful out of
  sample and after controlling for conventional factor exposures?
- **Portfolio utility:** do implementable model-guided portfolios improve
  risk-adjusted outcomes after realistic costs and constraints?
- **Change detection:** are material thesis revisions identified earlier or
  more reliably than simpler price-, filing-, or analyst-revision baselines?
- **Decision quality:** does the workflow improve investor consistency and
  comprehension without inducing automation bias or false confidence?

## Claims Governance

The repository operates under an explicit claims-control regime. Research
milestones establish engineering integrity and empirical evidence separately;
successful ingestion, leakage control, or reproducibility does not by itself
establish predictive validity or investment efficacy.

While `claims_eligible=false`, permissible descriptions are limited to the
system's implemented research capabilities, dataset scope, and verified
engineering properties. Statements about alpha, market outperformance,
predictive certainty, personalised recommendations, or risk elimination require
the relevant validation, licensing, governance, and compliance gates to have
been passed and documented.

This distinction is enforced through experiment contracts, immutable prediction
records, reproducibility reports, and the project [claims
policy](docs/compliance/claims-policy.md), rather than relying on disclaimer
language to compensate for weak evidence.

## Data Strategy

Data is classified by the claims it is capable of supporting:

- **Development-grade data** supports pipeline engineering, schema validation,
  interface research, and controlled internal experiments.
- **Research-grade data** adds documented point-in-time semantics, historical
  membership, identifier continuity, corporate actions, and reproducible
  snapshots suitable for model development and internal evaluation.
- **Claims-grade data** must additionally satisfy commercial licensing,
  redistribution, auditability, and governance requirements appropriate to the
  intended external claim and product use.

The information model spans prices and corporate actions, financial statements,
analyst estimates and revisions, regulatory filings, earnings transcripts,
macro and regime variables, sector and peer context, and dated corporate events.
Every observation must retain enough temporal and legal provenance to answer
both *when could the model have known this?* and *what use does the licence
permit?*

Required lineage includes source event and publication timestamps, vendor
availability and retrieval timestamps, model availability time, stable security
and vendor identifiers, content hashes, transformation provenance, and licence
tags.

## Target Application Architecture

This repository is currently the research-system spine, containing data
contracts, pipelines, model code, experiment definitions, audits, and
reproducibility evidence. The prospective application stack is:

- **Services:** Python and FastAPI.
- **Interface:** Next.js and React.
- **Analytical store:** PostgreSQL, with TimescaleDB where its time-series
  primitives are operationally justified.
- **Raw evidence store:** S3-compatible, immutable object storage.
- **Feature registry:** PostgreSQL/Parquet with point-in-time retrieval and
  lineage; Feast only if operating complexity warrants it.
- **Orchestration:** Dagster, with explicit asset lineage and partitioned
  backfills.
- **Modelling:** scikit-learn, LightGBM/XGBoost, SHAP-based diagnostics,
  probabilistic calibration, and experiment tracking.
- **Evidence intelligence:** provider-agnostic retrieval and language-model
  services with versioned prompts, outputs, citations, evaluations, and source
  bindings.
- **Retrieval:** PostgreSQL and pgvector initially.
- **Product infrastructure:** managed identity and billing introduced only at
  the product deployment stage.

## Repository Structure

```text
docs/
  product/       Product positioning and product strategy.
  research/      Validation plan, research protocols, model hypotheses.
  compliance/    Claims policy, governance, and advice-boundary docs.
  data/          Data vendor matrix and licensing considerations.
  specs/         Local working specifications when approved for versioning.
apps/
  api/           Future FastAPI service.
  web/           Future Next.js application.
packages/
  research/      Shared research/modeling code.
pipelines/       Acquisition, ingestion, feature, audit, and closure pipelines.
infra/           Future infrastructure definitions.
reports/         Git-tracked audits, backtests, and reproducibility evidence.
experiments/     Versioned experiment contracts and holdout locks.
```

## Current Documentation

- [Product positioning](docs/product/product-positioning.md)
- [Claims policy](docs/compliance/claims-policy.md)
- [Data vendor matrix](docs/data/data-vendor-matrix.md)
- [Validation plan](docs/research/validation-plan.md)
- [Synthetic baseline backtest contract](docs/research/synthetic-backtest-contract-v0.md)
- [Sprint 7 and Sprint 8 free-data progress](docs/research/sprint7-sprint8-free-data-progress-v1.md)
- [Sprint 7 reproducibility closure](reports/reproducibility/sprint7-closure-v1.md)

## Current Build Priorities

The point-in-time research substrate and Sprint 7 price baseline are complete.
The immediate objective is to close Sprint 8's SEC-primary multifactor baseline
under the same reproducibility standard; application-layer work remains
deliberately downstream of that gate.

Current priorities:

- Execute the SEC-primary fundamentals audit against a fresh database.
- Freeze the Sprint 8 holdout contract and bind it to exact source and run
  lineage.
- Produce two independent Sprint 8 rebuilds whose canonical audits, ledgers,
  evaluations, and comparisons agree exactly before publishing closure.
- Preserve `claims_eligible=false` until the independent performance,
  licensing, governance, and compliance conditions are satisfied.
- Develop filing and transcript extraction for guidance, KPIs, risks,
  management assertions, and citation-ready evidence spans.
- Specify the security-research API across thesis state, score distribution,
  confidence, factor attribution, Thesis Drift Index, active risks, and source
  evidence.
- Build model-evidence reporting for out-of-sample results, calibration,
  negative findings, failure modes, and known limitations.

## Roadmap

| Phase | Focus | Proof Gate |
|---|---|---|
| 0. Research design and data procurement | Fix the universe, information-time semantics, vendor rights, schemas, and experiment registry. | Source contracts and timestamps support point-in-time reconstruction. |
| 1. Reproducible baseline | Ingest prices, fundamentals, macro data, and filings; establish interpretable S&P 500 baselines. | Leakage controls, clean rebuilds, and baseline-behaviour tests pass. |
| 2. Ranking and risk models | Develop cross-sectional rankers, regime inference, portfolio risk, prediction ledgers, and evidence reporting. | Pre-registered out-of-sample gates are met, or the model is rejected or revised. |
| 3. Thesis intelligence | Construct the Thesis Memory Graph, evidence extraction, contradiction analysis, and Thesis Drift Index. | Proprietary features demonstrate incremental predictive value, workflow value, or both. |
| 4. Private research beta | Deliver the research cockpit, security views, watchlists, portfolio analysis, and surveillance. | 50-100 research users demonstrate comprehension, calibrated trust, retention, and willingness to pay. |
| 5. Governed paid beta | Introduce billing, reporting, operational controls, support, and live paper observation. | Commercial use, retention, and every external claim satisfy the applicable evidence and governance gates. |

## Operating Principles

- Preserve the information set that existed at the decision timestamp.
- Bind every material output to evidence, data lineage, and model provenance.
- Treat thesis change as a longitudinal inference problem, not a news-summary
  task.
- Prefer calibrated distributions and explicit uncertainty to spurious
  precision.
- Benchmark proprietary complexity against simple, credible alternatives.
- Record negative results and failed hypotheses as first-class research
  artefacts.
- Separate engineering reproducibility, statistical validity, economic utility,
  and product claims.
- Keep consequential investment judgement accountable to a human decision
  process.

## Status

The implemented system comprises the Python research package, SQLAlchemy
warehouse, immutable source-snapshot lineage, historical S&P 500 universe
reconstruction, point-in-time equity and SEC-fundamentals ingestion,
feature/scoring/prediction ledgers, realised-outcome evaluation, leakage guards,
reproducibility validation, and Git-tracked Sprint 7 closure evidence. A
synthetic end-to-end fixture provides rapid local verification; the closed
Sprint 7 evidence is derived from the amended real-market dataset licensed for
personal/internal research described above.

Runtime APIs, production orchestration, trained machine-learning rankers,
commercially licensed claims-grade datasets, and end-user applications remain
prospective. Sprint 8 is open. No real-market performance or alpha claim is
currently authorized.
