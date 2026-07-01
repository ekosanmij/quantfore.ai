# Quantfore AI

Quantfore AI is a thesis-change and risk/reward decisioning platform for investors.

It is not an AI stock picker, a trading bot, a robo-adviser, or a system that tells users what to buy or sell. The product vision is to help serious investors monitor how an investment thesis changes over time, understand whether forward risk/reward has improved or deteriorated, and make more disciplined research decisions with evidence.

The core question Quantfore AI is built to answer is:

> Has the thesis changed, does it matter, and what should be reviewed before the next decision?

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

## Product Vision

Investors do not lack information. They lack a disciplined system for detecting when the facts behind an investment case have changed.

Quantfore AI is intended to become a research and decisioning layer that tracks company fundamentals, filings, earnings transcripts, analyst expectations, market regime, valuation, sentiment, price behavior, and portfolio exposure. The product should surface what changed, why it matters, how confidence shifted, and how the current risk/reward compares with the original thesis.

The first product should focus on research support and general investment guidance. It should avoid personalized suitability, discretionary management, live trade execution, and unsupported performance claims until the necessary validation, compliance review, and operating controls exist.

## Core Positioning

Quantfore AI should be positioned as:

- A thesis-change monitoring platform.
- A risk/reward decisioning engine.
- A portfolio-aware research layer.
- A structured evidence system for investment decisions.
- A model-assisted analyst that explains its reasoning.

Quantfore AI should not be positioned as:

- An AI stock picker.
- A black-box buy/sell signal service.
- A guaranteed alpha product.
- A replacement for investor judgment.
- A financial adviser by default.
- A live execution or automated trading system.

## Initial Market Focus

The first target user is the serious self-directed investor who already researches individual equities or ETFs, manages meaningful personal capital, and wants a more systematic way to decide when to buy, hold, trim, add, avoid, or review.

The initial product assumptions are:

- Primary user: UK/EU and later US prosumer investors who buy US equities.
- Initial universe: S&P 500, expanding to Russell 1000 after pipeline stability.
- Investment horizon: 3 to 12 months.
- Score cadence: weekly monitoring and monthly rebalance baseline.
- Product stance: research and decision support first, not regulated personalized advice.

## Product Wedge

The proprietary wedge is thesis-change decisioning.

Most tools help users find stocks, read news, view charts, or ask finance questions. Quantfore AI should focus on the more defensible workflow: tracking whether the investment case has changed.

The core proprietary assets are expected to be:

- Company Thesis Memory Graph: a structured history of each company's thesis, KPIs, risks, guidance, management claims, catalysts, and prior evidence.
- Thesis Drift Index: a measurable signal for whether the thesis is improving, weakening, or breaking.
- Regime Vulnerability Map: a stock-specific view of sensitivity to rates, inflation, oil, USD, credit spreads, volatility, liquidity, and market breadth.
- Prediction Ledger: an immutable record of scores, evidence, model versions, data snapshots, and realized outcomes.
- User Decision Graph: a product-feedback layer showing how users interact with alerts, watchlists, decisions, and reviews.

## MVP Product Surfaces

The MVP should be useful before it is beautiful. It should make the model, evidence, and decision context inspectable.

Planned product surfaces:

- Opportunity List: ranked stocks with score, confidence, score change, action label, top drivers, and risk flags.
- Stock Decision Page: thesis summary, buy/watch/avoid style classification, bull and bear case, valuation, risk, Thesis Drift Index, and source evidence.
- What Changed Feed: the largest weekly score moves and the exact evidence behind each change.
- Portfolio Lens: CSV or manual holdings analysis for concentration, factor exposure, correlation, overlap, and risk contribution.
- Model Evidence Page: current model version, backtest summary, known weaknesses, performance by year, sector, and regime.
- Research Copilot: source-grounded Q&A over filings, transcripts, and evidence, without unsupported buy/sell claims.
- Alerts: thesis drift, estimate revisions, score upgrades/downgrades, earnings summaries, and watchlist changes.

## Model And Research Direction

Quantfore AI should be built model-first, but not black-box-first. The model must be auditable, reproducible, and explainable.

The intended decision system is layered:

1. Raw data ingestion for prices, fundamentals, filings, transcripts, estimates, macro, and events.
2. Point-in-time feature store with availability timestamps, source hashes, and vendor metadata.
3. Factor baseline for value, quality, momentum, revisions, risk, liquidity, and macro exposure.
4. ML ranking model, likely LightGBM or XGBoost, for forward benchmark-relative risk/reward ranking.
5. Regime engine to adjust factor relevance by macro and market state.
6. Risk engine for volatility, drawdown, beta, correlation, liquidity, and portfolio concentration.
7. LLM/RAG layer for extraction and explanation, not autonomous investment decisions.
8. Governance layer that stores every prediction before outcomes are known.

LLMs should extract, structure, summarize, and explain evidence. They should not be the sole source of buy/sell decisions.

## Validation Standard

Quantfore AI should not claim alpha, market-beating performance, or predictive certainty before proof.

Validation must be:

- Point-in-time.
- Survivorship-bias controlled.
- Cost-adjusted.
- Reproducible from locked data snapshots.
- Compared against simple baselines.
- Logged in an experiment registry.
- Forward-tested before broad public performance claims.

The validation work should test both model validity and workflow utility:

- Does the signal predict future risk-adjusted return, downside risk, or thesis deterioration?
- Does the ranking model separate stronger and weaker opportunities?
- Do model-guided portfolios improve risk-adjusted outcomes after costs?
- Does thesis monitoring detect meaningful changes faster or more reliably than human-only workflows?
- Do users understand the output without becoming overconfident or treating it as personalized advice?

## Claims And Compliance Position

Before proof exists, Quantfore AI may say it provides:

- Research support.
- Evidence-backed monitoring.
- Thesis-change detection.
- Risk/reward analysis.
- Structured investment workflow support.
- Decision support for human review.

Before proof exists, Quantfore AI must not say it:

- Beats the market.
- Generates guaranteed alpha.
- Predicts winning stocks.
- Tells users what to buy or sell.
- Provides personalized financial advice.
- Eliminates investment risk.

Default public framing:

> Quantfore AI provides research support and evidence-backed monitoring. It does not provide personalized financial advice, guarantee returns, or tell users what they should buy or sell.

## Data Strategy

The data strategy has two tracks:

- Prototype data: affordable sources for demos, UX testing, ingestion pipelines, and workflow validation.
- Proof-grade data: point-in-time, survivorship-bias-aware, commercially licensable sources for backtests, paper trading, paid-product claims, and future audit.

Prototype data may be good enough for user experience and ingestion tests. It is not automatically good enough for model-performance claims.

Initial data categories:

- Prices and corporate actions.
- Fundamentals and financial statements.
- Analyst estimates and revisions.
- SEC filings.
- Earnings transcripts.
- Macro and regime data.
- Sector and peer data.
- Events such as earnings, guidance, restatements, and corporate actions.

Data quality requirements include source timestamps, public release timestamps, vendor availability timestamps, model availability timestamps, data vendor IDs, source hashes, and license tags.

## Proposed Technical Architecture

This repository is currently the project spine and working documentation. The intended application architecture is:

- Backend: Python and FastAPI.
- Frontend: Next.js and React.
- Warehouse: PostgreSQL, with TimescaleDB optional.
- Raw storage: S3-compatible object storage.
- Feature store: custom Postgres/Parquet registry first, Feast later if needed.
- Orchestration: Dagster preferred, Airflow acceptable.
- Models: scikit-learn, LightGBM/XGBoost, SHAP, Bayesian calibration.
- LLM/RAG: provider-agnostic extraction and explanation layer with stored prompts, outputs, model versions, and source evidence.
- Vector search: pgvector first.
- Auth and payments: Clerk/Auth0 and Stripe when productization begins.

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
pipelines/       Future ingestion, feature, and validation pipelines.
infra/           Future infrastructure definitions.
```

## Current Documentation

- [Product positioning](docs/product/product-positioning.md)
- [Claims policy](docs/compliance/claims-policy.md)
- [Data vendor matrix](docs/data/data-vendor-matrix.md)
- [Validation plan](docs/research/validation-plan.md)
- [Synthetic baseline backtest contract](docs/research/synthetic-backtest-contract-v0.md)

## First Build Priorities

The first engineering milestone should be a reproducible research system, not a polished frontend.

Initial build tickets:

- Data snapshot registry for vendor, dataset, retrieval time, hash, storage URI, and license tag.
- Point-in-time universe loader for S&P 500 membership by as-of date.
- Feature registry with as-of date, availability date, version, and source hash.
- Baseline factor scorer for sector-neutral value, quality, momentum, revision, and risk factors.
- Backtest engine with weekly decisions, monthly rebalance, benchmark comparison, transaction costs, and metrics by year/regime.
- Prediction ledger that stores model output before outcomes and prevents silent edits.
- Transcript and filing extractor for guidance, KPIs, risks, management claims, and evidence snippets.
- Stock decision endpoint returning action label, score, confidence, drivers, Thesis Drift Index, risk flags, and evidence.
- Model evidence dashboard showing out-of-sample performance, failed tests, and known weaknesses.

## Roadmap

| Phase | Focus | Proof Gate |
|---|---|---|
| 0. Design and data procurement | Finalize universe, vendors, legal posture, data schemas, and experiment registry. | Vendor contracts and timestamps support point-in-time testing. |
| 1. Research engine prototype | Ingest prices, fundamentals, macro, filings, baseline factors, and first S&P 500 backtest. | Leakage tests pass and simple factors behave plausibly. |
| 2. ML ranker and validation | Build ranker, regime engine, risk engine, score ledger, and validation dashboard. | Out-of-sample metrics exceed thresholds or model is killed/refined. |
| 3. Thesis Memory Graph | Ingest filings/transcripts, build extraction templates, TDI v0, and evidence links. | TDI shows predictive value, workflow value, or both. |
| 4. Private beta product | Build cockpit, stock pages, watchlists, portfolio CSV, and alerts. | 50-100 beta users with measured trust and willingness to pay. |
| 5. Paid beta | Add billing, reporting, stronger governance, support, and live paper tracking. | Paid conversion and retention are acceptable; claims remain substantiated. |

## Operating Principles

- Evidence over prediction.
- Thesis change over stock picking.
- Human judgment over autonomous advice.
- Auditability over hype.
- Point-in-time truth over convenient backtests.
- Clear uncertainty over false precision.
- Compliance-aware language from day one.

## Status

This repository is at the foundation stage. It now includes the working documentation, a first `packages/research` Python package, SQLAlchemy research tables, initial ingestion scripts for FRED macro data, SEC companyfacts, and sample prices, plus baseline feature, scoring, and outcome-evaluation pipelines.

Runtime API services, production orchestration, trained ML models, backtesting infrastructure, and frontend applications are not implemented yet.
