# validation-plan.md

# Quantfore AI Validation Plan

## 1. Purpose

This document defines how Quantfore AI will test whether its model actually works.

Quantfore AI is not being validated as an “AI stock picker”. It is being validated as a **thesis-change and risk/reward decisioning platform**.

The central question is:

> Does Quantfore AI improve an investor’s ability to identify, monitor, and act on changes in company thesis, risk/reward, and forward return opportunity — after realistic data constraints, transaction costs, delays, and uncertainty?

This validation plan exists to prevent fake alpha, backtest theatre, leakage, survivorship bias, overfitting, and unsupported marketing claims.

---

## 2. Validation Principles

### 2.1 No claim before proof

Quantfore AI may not claim that it “beats the market”, “predicts winners”, “generates alpha”, or “knows what to buy or sell” until the relevant test gates in this document are passed.

Before proof, the product may only claim:

- research support
- evidence-backed monitoring
- thesis-change detection
- risk/reward analysis
- decision support
- structured investment workflow
- not financial advice

### 2.2 Separate model performance from product usefulness

The platform must be tested on two dimensions:

1. **Model validity**
   Does the signal predict future risk-adjusted return, downside risk, or thesis deterioration?

2. **Workflow utility**
   Does the product help users make better, faster, more disciplined investment decisions?

Both matter. A weak model with a nice interface is not enough. A strong signal hidden in an unusable product is also not enough.

### 2.3 Evidence hierarchy

Validation evidence is ranked as follows:

| Evidence Type | Strength | Notes |
|---|---:|---|
| Live capital performance | Very high | Only valid after regulatory/compliance review |
| Forward paper-trading | High | Best pre-launch proof |
| Walk-forward out-of-sample backtest | High | Must be point-in-time and cost-adjusted |
| Holdout-period historical test | Medium | Useful but easier to overfit |
| Cross-sectional Rank IC | Medium | Useful signal-quality measure |
| User research and workflow tests | Medium | Validates usefulness, not alpha |
| In-sample backtest | Low | Diagnostic only |
| Anecdotal stock examples | Very low | Not proof |

---

## 3. What Must Be Proven

Quantfore AI has five validation objects.

## 3.1 Signal Validity

The platform must show that its signals have predictive information about future outcomes.

Primary signals:

1. **Thesis Drift Index**
2. **Risk/Reward Score**
3. **Regime Vulnerability Score**
4. **Forward Return Opportunity Score**
5. **Downside Risk Score**
6. **Conviction Change Score**

The key question:

> When Quantfore AI says a company thesis has improved or deteriorated, does the stock’s future risk/reward actually tend to improve or deteriorate?

## 3.2 Ranking Validity

The model must show that higher-ranked securities outperform lower-ranked securities on a risk-adjusted basis.

Primary question:

> Does the model rank the investment universe in a way that has predictive value over 1, 3, 6, and 12-month horizons?

## 3.3 Portfolio Validity

The model must show that model-guided portfolios improve risk-adjusted outcomes versus sensible baselines.

Primary question:

> Do portfolios built from Quantfore AI rankings generate better after-cost risk-adjusted returns than benchmark, equal-weight, and standard factor baselines?

## 3.4 Thesis Monitoring Validity

The platform must show that it detects meaningful thesis changes faster or more reliably than a human-only workflow.

Primary question:

> Does Quantfore AI identify important company-level thesis changes before they are obvious in price performance alone?

## 3.5 User Workflow Validity

The product must show that users understand, trust, and use the outputs correctly.

Primary question:

> Does Quantfore AI improve user decision quality without encouraging overconfidence, excessive trading, or advice-like reliance?

---

## 4. Initial Scope

Validation must start narrow.

## 4.1 Asset Universe

Initial proof universe:

- US listed equities
- S&P 500 current and historical constituents
- Russell 1000 / large-mid cap expansion after first validation
- Exclude microcaps, illiquid names, OTC securities, SPACs at launch

Reason:

Large and mid-cap US equities have better data coverage, lower manipulation risk, better liquidity, and enough institutional relevance.

## 4.2 Investment Horizon

Primary horizon:

- 3 to 12 months

Secondary horizons:

- 1 month for thesis-change reaction
- 6 months for core model ranking
- 12 months for fundamental thesis validation

Non-goal:

- intraday prediction
- high-frequency trading
- options market-making
- day-trading signals

## 4.3 Rebalancing Frequency

Initial validation:

- Monthly ranking snapshots
- Weekly monitoring snapshots
- No intraday signals

Reason:

The product is a thesis-change and risk/reward platform, not a latency-sensitive trading engine.

---

## 5. Data Requirements

## 5.1 Required Data

The validation dataset must include:

| Category | Required Fields |
|---|---|
| Prices | Adjusted OHLCV, corporate actions, delistings, total return |
| Fundamentals | Income statement, balance sheet, cash flow, ratios, filing dates |
| Estimates | EPS, revenue, EBITDA, target-price revisions, analyst count, revision timestamps |
| Filings | 10-K, 10-Q, 8-K, risk factors, MD&A, guidance language |
| Transcripts | Earnings calls, prepared remarks, Q&A, timestamps |
| Macro | Rates, inflation, unemployment, credit spreads, yield curve, liquidity proxies |
| Sector Data | GICS/NAICS classification, industry peers, sector ETFs |
| Events | Earnings dates, guidance updates, restatements, major corporate actions |

## 5.2 Point-in-Time Requirement

All model inputs must be point-in-time.

For each feature, the system must store:

- `source_timestamp`
- `public_release_timestamp`
- `vendor_available_timestamp`
- `model_available_timestamp`
- `as_of_date`
- `revision_version`
- `data_vendor`
- `ingestion_job_id`

Hard rule:

> If the system cannot prove that a data item was available before the prediction timestamp, that data item cannot be used in validation.

## 5.3 Survivorship Bias Control

Validation must include:

- historical index membership
- delisted securities
- bankruptcies
- acquisitions
- spin-offs
- ticker changes
- corporate action adjustments

Hard rule:

> Testing only today’s surviving S&P 500 constituents is invalid.

## 5.4 Vendor Suitability

Prototype data may be used for UI and workflow testing.

Proof-grade validation must use vendors that can support:

- point-in-time fundamentals
- historical delisting coverage
- corporate actions
- estimates revision timestamps
- transcript availability timestamps
- commercial licensing for derived analytics
- audit retention

Cheap API data is acceptable for prototyping but not for alpha claims unless it passes the same audit tests.

---

## 6. Feature Families to Test

The first model should test clearly defined feature families, not vague “AI insights”.

## 6.1 Baseline Quant Features

Baseline factor families:

| Feature Family | Example Definitions |
|---|---|
| Value | FCF yield, earnings yield, EV/EBITDA vs sector, sales yield |
| Quality | ROIC, gross margin stability, accruals, leverage, FCF conversion |
| Momentum | 6-1 month price momentum, 12-1 month momentum, residual momentum |
| Growth | Revenue growth, EPS growth, forward estimate growth |
| Revisions | 1m/3m EPS revisions, breadth of analyst upgrades, estimate dispersion |
| Risk | Volatility, beta, downside beta, max drawdown, leverage |
| Sentiment | Transcript tone, news tone, management confidence, analyst language shifts |
| Liquidity | Dollar volume, spread proxy, turnover, market-cap bucket |

These are not proprietary edge by themselves. They are the baseline.

## 6.2 Proprietary Quantfore Features

The proprietary candidates are:

### A. Thesis Drift Index

Measures whether the company’s investment thesis is improving, deteriorating, or becoming unstable.

Inputs:

- management guidance changes
- KPI changes
- margin language
- demand commentary
- pricing power commentary
- risk-factor changes
- Q&A pressure
- analyst estimate revisions
- segment-level deterioration/improvement
- customer/end-market commentary

Output:

- `thesis_drift_score`: -100 to +100
- `thesis_drift_direction`: improving / stable / deteriorating
- `thesis_drift_confidence`: 0 to 1
- `drivers`: structured evidence list

### B. Company Thesis Memory Graph

A structured memory of what the market believed about a company before each new event.

Stores:

- key thesis claims
- supporting evidence
- risk assumptions
- management commitments
- relevant KPIs
- prior guidance
- prior model interpretation
- unresolved contradictions

Purpose:

> Detect whether new information confirms, weakens, or invalidates the previous thesis.

### C. Regime Vulnerability Score

Measures how exposed a company is to the current macro/market regime.

Inputs:

- rate sensitivity
- cyclicality
- margin pressure
- credit sensitivity
- dollar exposure
- commodity sensitivity
- valuation duration
- refinancing needs
- customer demand sensitivity

Output:

- `regime_vulnerability_score`: 0 to 100
- `regime_sensitivity_vector`
- regime-specific downside scenarios

### D. Prediction Ledger

Every model call creates a timestamped forecast record.

Stores:

- model version
- input data version
- feature vector hash
- prediction timestamp
- predicted rank
- expected return band
- downside risk estimate
- confidence
- explanation
- realised outcome after 1m/3m/6m/12m

Purpose:

> Force accountability. No retroactive editing of predictions.

---

## 7. Prediction Targets

The platform should not only predict raw return. It should predict multiple investment outcomes.

## 7.1 Primary Targets

| Target | Definition |
|---|---|
| Forward excess return | Stock return minus benchmark/sector return over horizon |
| Forward risk-adjusted return | Excess return divided by realised volatility |
| Downside event | Stock underperforms benchmark by more than threshold |
| Thesis deterioration | Fundamental/estimate/transcript deterioration after event |
| Rank bucket performance | Top decile vs bottom decile forward performance |

## 7.2 Horizons

Targets must be computed at:

- 1 month
- 3 months
- 6 months
- 12 months

Primary model-selection horizon:

- 6 months

Reason:

Six months is long enough for fundamental signal decay to matter and short enough for product feedback loops.

---

## 8. Baselines

Quantfore AI must beat simple, credible baselines.

## 8.1 Market Baselines

- S&P 500 total return
- Equal-weight S&P 500
- Sector-neutral equal-weight universe
- Nasdaq 100 where relevant

## 8.2 Factor Baselines

- Value-only model
- Momentum-only model
- Quality-only model
- Revisions-only model
- Equal-weight composite factor model

## 8.3 Commercial Proxy Baselines

Where legally and practically available:

- public quant-rating snapshots
- analyst consensus rating changes
- simple screeners
- ETF/sector allocation proxies

## 8.4 Naive AI Baseline

A naive LLM summary score should be tested separately.

Purpose:

> Prove that Quantfore AI’s structured decision system beats a generic AI summary workflow.

---

## 9. Backtest Design

## 9.1 Walk-Forward Testing

Use rolling walk-forward validation.

Example design:

| Period | Use |
|---|---|
| 2010-2016 | Initial training |
| 2017 | Validation |
| 2018 | Test |
| 2011-2017 | Retrain |
| 2018 | Validation |
| 2019 | Test |
| Continue rolling | Through latest available period |

The exact windows may change based on data availability, but the principle is fixed:

> Train only on data available before the prediction date.

## 9.2 Embargo Period

Use an embargo between training and test periods where needed to avoid leakage from overlapping return labels.

Minimum embargo:

- 1 month for 1-month target
- 3 months for 3-month target
- 6 months for 6-month target
- 12 months for 12-month target

## 9.3 Event-Based Tests

For earnings/transcript thesis-change tests:

- Prediction timestamp must be after transcript availability
- Price reaction window must be clearly defined
- Use same timestamp logic for all stocks
- Do not use revisions or articles published after prediction timestamp

Event windows:

- T+1 trading day
- T+5 trading days
- T+21 trading days
- T+63 trading days
- T+126 trading days

## 9.4 Rebalancing Assumptions

Backtest portfolios:

- rebalance monthly
- rank universe monthly
- form top-decile and top-quintile portfolios
- optionally short/avoid bottom-decile for diagnostic long-short tests
- long-only version required for product relevance

## 9.5 Position Sizing

Test at least three portfolio construction rules:

1. Equal-weight top decile
2. Score-weighted top decile
3. Risk-parity adjusted top decile

Maximum position size:

- 5% single-name cap for model portfolio test
- 10% single-name cap for aggressive diagnostic test

Sector constraint:

- unconstrained diagnostic portfolio
- sector-neutral portfolio
- sector-capped portfolio

All three should be reported.

---

## 10. Transaction Costs and Frictions

Backtests must include costs.

## 10.1 Minimum Cost Assumptions

| Cost Type | Assumption |
|---|---|
| Commission | 0 bps for base retail test, but reported separately |
| Spread/slippage | 5-25 bps depending on liquidity bucket |
| Market impact | 0 for small retail simulation; non-zero for AUM-scaled test |
| Borrow cost | Required for any short test |
| Turnover penalty | Report monthly and annualised turnover |

## 10.2 Liquidity Filters

Initial validation universe must exclude names that fail:

- minimum market cap
- minimum median daily dollar volume
- stale price checks
- abnormal corporate-action periods where data is unreliable

## 10.3 AUM Capacity Test

If the model later targets funds/advisers, run a capacity test:

- £1m
- £10m
- £100m
- £500m

For each AUM level, estimate:

- implementation cost
- average participation rate
- slippage sensitivity
- turnover drag
- liquidity bottlenecks

---

## 11. Core Metrics

## 11.1 Signal Metrics

| Metric | Definition |
|---|---|
| Rank IC | Spearman correlation between model score and future return |
| IC t-stat | Statistical significance of Rank IC |
| Decile spread | Top decile return minus bottom decile return |
| Hit rate | Percentage of periods top bucket beats benchmark |
| Monotonicity | Whether higher score buckets produce better outcomes |
| Signal decay | How fast signal predictive power fades |
| Coverage | Percentage of universe with usable score |
| Stability | Month-to-month score turnover and noise |

## 11.2 Portfolio Metrics

| Metric | Definition |
|---|---|
| Excess return | Portfolio return minus benchmark return |
| Tracking error | Volatility of excess return |
| Information ratio | Excess return divided by tracking error |
| Sharpe ratio | Return per unit total volatility |
| Sortino ratio | Return per unit downside volatility |
| Max drawdown | Largest peak-to-trough loss |
| Calmar ratio | Annual return divided by max drawdown |
| Turnover | Portfolio turnover per rebalance/annualised |
| Beta | Market beta |
| Factor exposure | Exposure to value, momentum, quality, size, sector |

## 11.3 Risk Metrics

| Metric | Definition |
|---|---|
| Downside capture | Performance during market declines |
| Upside capture | Performance during rising markets |
| CVaR | Expected loss in worst tail outcomes |
| Stress loss | Loss under defined macro/market shocks |
| Sector concentration | Exposure by sector |
| Single-name concentration | Position-level exposure |
| Drawdown recovery time | Time required to recover from drawdown |

## 11.4 Workflow Metrics

| Metric | Definition |
|---|---|
| Decision time reduction | Time saved in research workflow |
| Correct interpretation rate | User understands output correctly |
| Overconfidence rate | User misuses model as advice |
| Alert precision | Percentage of alerts judged useful |
| Alert recall | Percentage of important thesis changes caught |
| Repeat usage | Weekly active usage among target users |
| Save-to-watchlist rate | Whether signal triggers real workflow action |

---

## 12. Acceptance Thresholds

These are initial proof thresholds. They can be tightened after more data.

## 12.1 Signal Acceptance Thresholds

To pass signal validation, a signal must show:

- positive average Rank IC over test period
- Rank IC statistically distinguishable from zero
- top quintile beats bottom quintile after costs
- monotonic or near-monotonic decile behaviour
- signal survives at least two major market regimes
- signal remains positive after sector-neutralisation
- signal does not depend on one sector, year, or mega-cap cluster

Minimum target:

| Metric | Pass Threshold |
|---|---:|
| Mean monthly Rank IC | > 0.02 |
| IC t-stat | > 2.0 |
| Top-bottom quintile spread | > 3% annualised after costs |
| Positive IC months | > 55% |
| Coverage | > 80% of target universe |

Strong target:

| Metric | Strong Threshold |
|---|---:|
| Mean monthly Rank IC | > 0.04 |
| IC t-stat | > 3.0 |
| Top-bottom quintile spread | > 6% annualised after costs |
| Positive IC months | > 60% |
| Coverage | > 90% of target universe |

## 12.2 Portfolio Acceptance Thresholds

To pass portfolio validation, the long-only model portfolio must show:

- positive excess return vs benchmark after costs
- higher risk-adjusted return than equal-weight baseline
- acceptable drawdown profile
- turnover that does not destroy returns
- no hidden sector-only explanation

Minimum target:

| Metric | Pass Threshold |
|---|---:|
| Annualised excess return | > 2% after costs |
| Information ratio | > 0.30 |
| Sharpe uplift vs benchmark | > 0.10 |
| Max drawdown vs benchmark | Not worse by more than 5 percentage points |
| Annual turnover | < 300% unless excess return justifies it |
| Outperformance years | > 55% |

Strong target:

| Metric | Strong Threshold |
|---|---:|
| Annualised excess return | > 4% after costs |
| Information ratio | > 0.50 |
| Sharpe uplift vs benchmark | > 0.20 |
| Max drawdown vs benchmark | Better or no worse |
| Annual turnover | < 200% |
| Outperformance years | > 60% |

## 12.3 Thesis Drift Acceptance Thresholds

The Thesis Drift Index must show:

| Metric | Pass Threshold |
|---|---:|
| Deterioration alert precision | > 60% |
| Deterioration alert recall | > 40% |
| Improving-thesis forward excess return | Positive after costs |
| Deteriorating-thesis forward excess return | Negative vs sector |
| Alert noise rate | Low enough for weekly use |
| Human reviewer usefulness score | > 7/10 |

For launch as monitoring tool, high precision matters more than high recall.

Reason:

> A product that misses some changes is acceptable. A product that constantly cries wolf is not.

---

## 13. Robustness Tests

A model does not pass until it survives robustness checks.

## 13.1 Regime Robustness

Test separately across:

- bull markets
- bear markets
- high-rate periods
- low-rate periods
- inflationary periods
- disinflationary periods
- recession scares
- sector bubbles
- liquidity stress periods

Required output:

- performance by regime
- failure modes by regime
- when the model should reduce confidence

## 13.2 Sector Robustness

Test by sector:

- technology
- healthcare
- financials
- industrials
- consumer discretionary
- consumer staples
- energy
- communication services
- utilities
- materials
- real estate

Hard rule:

> If the signal only works because it overweighted one hot sector, it is not validated.

## 13.3 Market-Cap Robustness

Test by market-cap bucket:

- mega-cap
- large-cap
- mid-cap

Later:

- small-cap only if data quality permits

## 13.4 Feature Ablation

Run ablation tests:

- baseline factors only
- baseline factors + thesis drift
- baseline factors + regime vulnerability
- baseline factors + LLM-derived features
- full model

The proprietary layer must improve the model beyond baseline factors.

Required proof:

> Quantfore proprietary features must add incremental predictive value after controlling for standard value, quality, momentum, and revisions features.

## 13.5 Randomisation Tests

Run tests against:

- random scores
- shuffled labels
- permuted timestamps
- random sector-neutral portfolios

Purpose:

> Confirm that observed performance is not a backtest artefact.

---

## 14. Leakage Controls

Leakage is the biggest risk in this product.

## 14.1 Common Leakage Sources

Watch for:

- using revised fundamentals instead of originally reported fundamentals
- using filing data before filing release
- using transcript text before transcript availability
- using analyst revisions timestamped after the prediction date
- using current index membership historically
- using delisting-adjusted datasets incorrectly
- using sector classifications that changed later
- using future corporate action data
- using cleaned datasets that embed future corrections

## 14.2 Leakage Audit

Before any result is accepted, run a leakage audit:

| Check | Required? |
|---|---:|
| Point-in-time timestamp audit | Yes |
| Survivorship audit | Yes |
| Corporate-action audit | Yes |
| Feature availability audit | Yes |
| Vendor revision audit | Yes |
| Prediction ledger audit | Yes |
| Manual sample inspection | Yes |

Manual inspection:

- Select 50 random predictions
- Verify all inputs were available before prediction timestamp
- Verify target return window starts after prediction timestamp
- Verify no future data enters feature vector

---

## 15. Model Selection Rules

## 15.1 Approved First Models

Start with interpretable or semi-interpretable models:

- linear/ridge regression
- logistic regression for downside event prediction
- random forest
- gradient boosted trees
- LightGBM/XGBoost
- regularised factor composite
- Bayesian regime model
- hidden Markov model for regime classification

Avoid initially:

- deep reinforcement learning
- black-box neural networks for direct stock prediction
- autonomous trading policies
- LLM-only stock scoring
- overly complex ensembles without interpretability

Reason:

The first goal is proof, not sophistication.

## 15.2 Model Complexity Gate

A more complex model is allowed only if it improves:

- out-of-sample performance
- robustness
- stability
- interpretability
- operational reliability

A complex model that only improves in-sample results is rejected.

---

## 16. LLM Validation

The LLM layer must be tested separately from the quant model.

## 16.1 LLM Responsibilities

Approved LLM tasks:

- summarise filings/transcripts
- extract thesis claims
- detect guidance changes
- identify risk-factor changes
- produce evidence-linked explanations
- generate bull/bear case summaries
- explain model drivers

Not approved:

- final investment recommendation without quant/risk model
- unsupported price targets
- hallucinated financial facts
- advice-like personalised instructions
- claiming certainty about future returns

## 16.2 LLM Evaluation

Evaluate:

| Metric | Target |
|---|---:|
| Factual accuracy | > 95% on sampled financial facts |
| Citation support | > 95% of claims source-backed |
| Extraction consistency | > 90% agreement with human labels |
| Hallucination rate | < 2% material hallucinations |
| Unsupported recommendation rate | 0% |
| Compliance violation rate | 0% in sampled outputs |

## 16.3 Human Review Dataset

Create labelled datasets for:

- transcript KPI extraction
- guidance change detection
- thesis claim extraction
- risk-factor change detection
- management tone changes
- Q&A pressure classification
- bull/bear argument quality

At least 500 labelled company-event examples before relying on LLM-derived thesis drift in production.

---

## 17. User Research Validation

## 17.1 Target Users

Initial research users:

1. Serious self-directed investors
2. Finance creators/newsletter writers
3. Investment club organisers
4. Independent advisers/research analysts
5. Small fund analysts

Exclude initially:

- casual gamblers
- day traders
- options speculators
- users seeking guaranteed picks
- users asking for personalised financial advice

## 17.2 User Research Questions

Test:

- Do users understand “thesis-change and risk/reward decisioning”?
- Do users misinterpret outputs as financial advice?
- Which signal explanations feel credible?
- Which alerts are useful vs noisy?
- How much evidence do users need before trusting a score?
- What workflows does this replace?
- What would users pay for?
- Which competitors do they already use?
- What would make them switch?

## 17.3 Usability Tasks

Users must complete:

1. Search a stock
2. Interpret thesis-change status
3. Identify top evidence behind score
4. Add a stock to watchlist
5. Review an alert
6. Compare two stocks
7. Understand risk/reward profile
8. Export or save investment note

Pass criteria:

| Metric | Threshold |
|---|---:|
| Task completion | > 80% |
| Correct interpretation | > 80% |
| Advice-risk misunderstanding | < 10% |
| Alert usefulness score | > 7/10 |
| Weekly retention in beta | > 35% |

---

## 18. Paper-Trading Validation

Historical backtests are not enough.

## 18.1 Paper Portfolio

Before public claims, run at least one forward paper portfolio.

Minimum setup:

- universe: S&P 500 / Russell 1000
- rebalance: monthly
- horizon: 6 months
- minimum duration: 6 months
- preferred duration: 12 months
- all predictions logged before outcomes
- no retroactive model edits
- results published internally monthly

## 18.2 Paper Portfolio Variants

Run:

1. Long-only top 25 model portfolio
2. Long-only top 50 model portfolio
3. Sector-capped top 50 portfolio
4. Avoid-list / bottom-decile diagnostic
5. Benchmark-only control

## 18.3 Paper-Trading Gate

To move from beta to commercial launch, paper portfolio should show:

- prediction ledger integrity
- stable model outputs
- acceptable turnover
- no major unexplained failure
- user-facing explanations accurate enough for research workflow
- at least neutral-to-positive evidence of ranking value

Do not require full proof of market-beating performance before launching research support.

Do require proof before making performance/alpha claims.

---

## 19. Decision Gates

## Gate 0: Data Readiness

Pass when:

- point-in-time dataset is live
- ingestion timestamps are stored
- universe history is validated
- corporate actions are handled
- delistings are included
- data licensing checked

Fail if:

- data cannot support audit
- derived-signal rights are unclear
- vendor timestamps are insufficient

## Gate 1: Baseline Model

Pass when:

- baseline factors reproduce known sensible behaviour
- factor signs are economically plausible
- no obvious leakage
- top/bottom bucket spread exists before costs

Fail if:

- baseline model is unstable
- signs are nonsensical
- results collapse under basic controls

## Gate 2: Proprietary Signal Incrementality

Pass when:

- Thesis Drift or Regime Vulnerability improves baseline model
- improvement holds out-of-sample
- improvement survives sector and regime checks
- ablation confirms incremental value

Fail if:

- proprietary features add no out-of-sample value
- effect only appears in one sector/year
- results depend on one vendor artefact

## Gate 3: Product Workflow

Pass when:

- users understand outputs
- alerts are useful
- product reduces research burden
- no widespread advice misunderstanding

Fail if:

- users treat it as guaranteed stock advice
- alerts are too noisy
- explanations are not trusted

## Gate 4: Beta Launch

Pass when:

- validation evidence supports research-support positioning
- paper-trading ledger is active
- compliance copy is approved
- claims policy is enforced
- model limitations are visible in product

Fail if:

- product language implies advice
- model outputs cannot be explained
- evidence is insufficient even for monitoring claims

## Gate 5: Performance Claims

Pass only when:

- out-of-sample and forward paper evidence is strong
- results are after costs
- benchmarks are fair
- drawdowns and failures are disclosed
- legal/compliance review approves claims

Fail by default until proven.

---

## 20. Kill Criteria

The project should be killed, narrowed, or repositioned if the following occur.

## 20.1 Model Kill Criteria

Kill or materially redesign model if:

- mean Rank IC is near zero across horizons
- top/bottom spreads disappear after costs
- proprietary features add no incremental value
- performance is entirely sector-driven
- signal works only in one historical regime
- model requires unrealistic turnover
- model degrades badly in forward paper-trading
- explanations are not faithful to model drivers

## 20.2 Data Kill Criteria

Kill or change data strategy if:

- point-in-time data cannot be obtained affordably
- transcript/estimate timestamps are unreliable
- vendor rights prohibit derived analytics
- data coverage is too sparse for target universe
- alternative data creates MNPI or licensing risk

## 20.3 Product Kill Criteria

Kill or reposition if:

- users only want explicit buy/sell tips
- users do not understand thesis-change framing
- users refuse to pay for monitoring/research workflow
- alerts do not create repeat usage
- regulatory posture becomes too advice-like too early

## 20.4 Claims Kill Criteria

Do not make performance claims if:

- results are in-sample only
- results exclude costs
- results exclude delisted names
- results use today’s index constituents
- results rely on unavailable future data
- results cannot be reproduced from prediction ledger

---

## 21. Experiment Tracking

Every experiment must be reproducible.

## 21.1 Required Metadata

For each experiment:

- experiment ID
- model version
- feature version
- data snapshot ID
- vendor versions
- universe definition
- training period
- validation period
- test period
- target horizon
- cost assumptions
- benchmark
- code commit hash
- researcher
- date run
- notes
- pass/fail result

## 21.2 Required Outputs

Each experiment must produce:

- metrics table
- decile return chart
- IC time series
- drawdown chart
- turnover report
- sector exposure report
- feature importance report
- ablation comparison
- leakage audit result
- conclusion

---

## 22. Reporting Format

Each model validation report should answer:

1. What was tested?
2. Why should the signal plausibly work?
3. What data was used?
4. What data was excluded?
5. What was the prediction timestamp?
6. What benchmark was used?
7. What costs were assumed?
8. What were the results?
9. Did the result survive robustness checks?
10. What failed?
11. What are the limitations?
12. What decision does this support?

Required decision labels:

- `PASS`
- `PASS WITH LIMITATIONS`
- `RETEST`
- `FAIL`
- `KILL`

---

## 23. Initial Validation Roadmap

## Phase 1: Data Audit

Duration: 2-4 weeks

Deliverables:

- vendor dataset audit
- point-in-time test
- universe reconstruction
- corporate-action validation
- sample manual audit

Exit:

- Gate 0 pass/fail

## Phase 2: Baseline Factor Backtest

Duration: 3-6 weeks

Deliverables:

- value/quality/momentum/revisions/risk baseline
- decile spreads
- IC time series
- long-only portfolio tests
- cost sensitivity

Exit:

- Gate 1 pass/fail

## Phase 3: Thesis Drift Prototype

Duration: 4-8 weeks

Deliverables:

- transcript/filing extraction pipeline
- thesis memory schema
- 500 labelled event dataset
- first Thesis Drift Index
- event-study test

Exit:

- early Gate 2 evidence

## Phase 4: Incrementality Test

Duration: 4-6 weeks

Deliverables:

- baseline vs baseline + thesis drift
- ablation tests
- sector/regime robustness
- downside alert precision/recall

Exit:

- Gate 2 pass/fail

## Phase 5: User Beta and Paper Portfolio

Duration: 6-12 months

Deliverables:

- prediction ledger
- forward paper portfolios
- alert usefulness metrics
- user workflow validation
- claims-review evidence file

Exit:

- Gate 3 and Gate 4 decision

---

## 24. Final Standard

Quantfore AI works only if it can prove at least one of the following:

1. Its proprietary thesis-change signals improve future risk/reward prediction beyond standard factors.
2. Its monitoring workflow helps users identify important thesis changes earlier and more reliably than existing tools.
3. Its decisioning interface materially improves investor research speed, discipline, and risk awareness.
4. Its paper-traded rankings show credible forward evidence of risk-adjusted value.

The ideal outcome is all four.

The minimum viable proof for launch is:

> Quantfore AI demonstrably helps investors monitor thesis change and risk/reward more effectively, without making unsupported buy/sell or performance claims.

The minimum proof for alpha claims is much higher:

> Quantfore AI shows robust, point-in-time, out-of-sample, after-cost, benchmark-adjusted evidence that its model rankings add predictive value across regimes.

Until then, Quantfore AI remains a research-support and decisioning platform — not an AI stock picker.
