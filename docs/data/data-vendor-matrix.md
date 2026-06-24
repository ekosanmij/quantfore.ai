# data-vendor-matrix.md

**Product:** Quantfore AI
**Document owner:** Product / Data / Quant Research
**Version:** v0.1
**Date:** 2026-06-24
**Status:** Working decision document

---

## 1. Purpose

Quantfore AI needs a data stack that supports two separate jobs:

1. **Prototype product UX** — fast, affordable, good enough for demos, user interviews, ingestion tests, and workflow validation.
2. **Proof-grade model validation** — point-in-time, survivorship-bias-aware, commercially licensable data suitable for backtesting, paper trading, paid-product claims, and future audit.

These jobs should not use the same quality standard. Cheap APIs are acceptable for early product exploration, but they are not automatically acceptable for proving alpha or powering commercial claims.

The core vendor question is:

> Which sources allow Quantfore AI to prove thesis-change and risk/reward signals without hidden lookahead bias, survivorship bias, licensing risk, or unsupported redistribution?

---

## 2. Executive decision

### 2.1 Recommended prototype stack

Use this stack for early UI, ingestion pipelines, internal demos, and workflow tests.

| Data category | Recommended prototype source | Reason | Caveat |
|---|---|---|---|
| US prices / OHLCV | **Massive.com, formerly Polygon.io**, or **Tiingo** | Developer-friendly market APIs; useful for charts, screening, and daily refreshes. | Confirm commercial/display rights before showing to paid users. |
| Fundamentals | **Financial Modeling Prep (FMP)** + **SEC EDGAR** | FMP gives fast structured financials; SEC EDGAR gives source-of-truth filings and XBRL. | FMP data must be checked against filings; not proof-grade by default. |
| Transcripts | **FMP** or **Finnhub** | Easy transcript APIs for early thesis-memory prototypes. | Coverage, timestamping, redistribution, and NLP storage rights must be verified. |
| Analyst estimates | **FMP** or **Finnhub** | Cheap way to prototype estimate-revision workflows. | Not sufficient for institutional-grade estimate-revision research until validated against premium sources. |
| Filings | **SEC EDGAR APIs** | Free official source for submissions and XBRL company facts. | Requires internal parsing, cleaning, accession tracking, and CIK/ticker mapping. |
| Macro data | **FRED** | Free, reliable macro time series. | Not a company-level alpha source by itself. |

**Prototype verdict:** FMP + SEC EDGAR + Massive/Tiingo is the fastest route to a working product. Use this for UX and workflow proof, not for final model-performance claims.

### 2.2 Recommended proof-grade stack

Use this stack for model validation, backtesting, paper trading, investor diligence, and claims substantiation.

| Data category | Recommended proof-grade source | Reason | Decision |
|---|---|---|---|
| Point-in-time fundamentals + prices + delisted coverage | **Nasdaq Data Link / Sharadar Core US Equities Bundle** | Designed for systematic research; includes fundamentals, equity prices, corporate actions, insiders, institutional data, and delisted coverage depending on package. | **Primary proof-grade candidate.** |
| Additional US market prices | **Massive.com flat files/API** or **Tiingo** | Strong for market-data delivery, product charting, and possible intraday/lower-latency use cases. | Use alongside Sharadar if intraday or richer chart UX is needed. |
| Analyst estimates | **LSEG I/B/E/S**, **FactSet Estimates**, or **Visible Alpha** | Premium estimate datasets with deeper coverage, history, contributor detail, and point-in-time characteristics. | **Use one premium estimate source before making estimate-revision alpha claims.** |
| Transcripts | **FactSet Near Real-Time Transcripts**, **AlphaSense**, or another licensed commercial transcript feed | Better coverage and commercial terms for paid product workflows. | Must negotiate rights for storage, embeddings, summaries, excerpts, and display. |
| Filings | **SEC EDGAR direct** + optional commercial parser such as **sec-api.io** | EDGAR is source-of-truth; third-party parser accelerates search and normalized extraction. | Keep EDGAR accession IDs as canonical document keys. |
| Broker research / expert calls | **AlphaSense**, **FactSet**, **Visible Alpha / S&P**, or equivalent | Useful for institutional-grade thesis memory, but expensive and licensing-heavy. | Defer until B2B/pro tier. |

**Proof-grade verdict:** Start proof with Sharadar for fundamentals/prices, EDGAR for filings, and one premium estimate/transcript provider when budget allows. Do not validate estimate-revision or transcript-alpha claims entirely on cheap APIs.

---

## 3. Vendor matrix

Scoring key:

- **5 = strong fit**
- **3 = usable with caveats**
- **1 = weak fit / not recommended for that use**
- **N/A = not a core offering**

| Vendor / Source | Prices | Fundamentals | Estimates | Transcripts | Filings | Licensing / commercial posture | Quantfore fit | Decision |
|---|---:|---:|---:|---:|---:|---|---|---|
| **SEC EDGAR direct** | N/A | 4 | N/A | N/A | 5 | Public official source; no API key; still follow SEC access policies and attribution norms. | Canonical source for filings, accession IDs, XBRL facts, and thesis-memory grounding. | **Use always.** |
| **Nasdaq Data Link / Sharadar** | 4 | 5 | 2 | N/A | 2 | Paid dataset through Nasdaq Data Link; confirm professional/commercial use rights. | Strong early proof-grade source for US systematic equity research. | **Primary validation candidate.** |
| **Massive.com / formerly Polygon.io** | 5 | 2 | N/A | N/A | 1 | Strong market-data API; business plans may involve exchange licensing and display rules. | Best for price APIs, charting, intraday/real-time product UX, and historical market data. | **Use for prices; not core fundamentals.** |
| **Tiingo** | 4 | 3 | N/A | N/A | 1 | Simple individual/commercial pricing; confirm redistribution/display restrictions. | Affordable EOD price/news/fundamentals option. | **Good prototype/backup vendor.** |
| **Financial Modeling Prep (FMP)** | 3 | 3 | 3 | 3 | 2 | Display or redistribution requires a specific Data Display and Licensing Agreement. | Excellent early prototype API; weaker as proof-grade source unless audited. | **Use for prototype, not final proof.** |
| **Finnhub** | 3 | 3 | 3 | 3 | 2 | Free and paid tiers; commercial/startup terms need confirmation. | Good early API for estimates, transcripts, fundamentals, and market data. | **Prototype / secondary validation only.** |
| **Intrinio** | 3 | 4 | 3 | 2 | 2 | Commercial fintech-oriented licensing; vendor consultation likely needed. | Stronger than budget APIs for commercial fintech builds; useful middle tier. | **Consider for production if pricing fits.** |
| **Alpha Vantage** | 3 | 2 | 1 | N/A | 1 | Good developer API; commercial use requires plan review. | Useful for prototypes, economic data, indicators, and simple demos. | **Do not use for proof-grade backtests.** |
| **FactSet** | 4 | 5 | 5 | 5 | 4 | Enterprise-grade licensing; expensive; strong compliance and redistribution controls. | Strong production source for estimates, transcripts, fundamentals, and research workflows. | **Target for B2B/pro tier.** |
| **LSEG / I/B/E/S** | 4 | 4 | 5 | 3 | 3 | Enterprise-grade; licensing and redistribution constraints likely significant. | Premium source for analyst-estimate revision signals. | **Use if estimate-revision edge becomes central.** |
| **Visible Alpha / S&P Global** | 2 | 4 | 5 | 1 | 2 | Enterprise-grade; deep sell-side model dataset; API/feed/cloud delivery. | Best for granular KPI and consensus model assumptions. | **High-value later-stage vendor.** |
| **AlphaSense** | 1 | 3 | 3 | 5 | 5 | Platform/content licensing; check API, derived-data, and embedding rights. | Strong for qualitative research, transcripts, broker research, filings, and thesis-memory workflows. | **Evaluate for pro research layer.** |
| **Yahoo Finance / yfinance** | 2 | 1 | 1 | N/A | N/A | Unofficial for many production/API uses; licensing unclear for commercial products. | Useful only for throwaway notebooks. | **Do not use in product or proof.** |

---

## 4. Category comparison

## 4.1 Prices and corporate actions

### What Quantfore needs

Minimum required fields:

- adjusted daily OHLCV
- raw OHLCV
- dividends
- splits
- symbol changes
- delisting dates
- exchange/listing status
- share class identifiers
- market cap where available
- corporate-action effective dates

For model validation, Quantfore also needs:

- point-in-time index membership or a robust investable-universe definition
- delisted securities
- survivorship-bias controls
- split/dividend adjustment methodology
- timestamped availability dates

### Vendor view

| Vendor | Strength | Weakness | Best use |
|---|---|---|---|
| **Sharadar** | Strong daily prices, corporate actions, delisted coverage in research-oriented bundle. | Less suitable for low-latency intraday UX. | Backtesting and systematic research. |
| **Massive.com** | Strong real-time/historical market data, WebSockets, flat files, broad US exchange coverage. | Commercial/display licensing must be managed carefully. | Product charts, market data, intraday workflows. |
| **Tiingo** | Affordable EOD, news, some fundamentals; simple pricing. | Not as institutionally deep as FactSet/LSEG/Bloomberg. | Prototype and fallback price source. |
| **FMP/Finnhub** | Fast API access and broad endpoint coverage. | Must verify adjustment quality, delisted coverage, and commercial usage. | Prototype dashboards. |
| **FactSet/LSEG/Bloomberg** | Enterprise-grade data quality and coverage. | Expensive and heavier procurement. | Institutional product or B2B tier. |

### Decision

- Use **Sharadar** for proof-grade historical prices and corporate actions.
- Use **Massive.com** for product-grade market charts and potential intraday data.
- Do not prove model edge on Yahoo/yfinance or a provider without delisted coverage and corporate-action auditability.

---

## 4.2 Fundamentals

### What Quantfore needs

Minimum required fields:

- income statement
- balance sheet
- cash flow statement
- fiscal period metadata
- filing date
- report period
- accepted timestamp / availability date
- restated vs originally reported values where possible
- standardized fields
- as-reported fields
- sector/industry classification
- CIK/ticker/security master mapping

The critical requirement is **point-in-time availability**. A model must not use a value before it was available to the market.

### Vendor view

| Vendor | Strength | Weakness | Best use |
|---|---|---|---|
| **SEC EDGAR** | Official source for filings, submissions, and XBRL company facts. | Requires parsing, standardization, restatement handling, ticker mapping, and QA. | Canonical document source. |
| **Sharadar** | Curated US public-company fundamentals with long history and survivorship-bias controls. | US-focused; paid licensing. | Proof-grade US equity research. |
| **FMP** | Fast, broad structured fundamentals and ratios. | Errors possible; display/redistribution requires agreement; point-in-time limits must be checked. | Prototype UI and initial factor engineering. |
| **Intrinio** | Commercial API with standardized fundamentals and fintech-focused packaging. | Pricing/licensing may exceed prototype budget. | Production candidate if Sharadar is insufficient. |
| **FactSet/LSEG/S&P Capital IQ** | Enterprise-grade global fundamentals and identifiers. | Expensive and heavy procurement. | Institutional tier. |

### Decision

- Use **SEC EDGAR** as canonical raw/document truth.
- Use **Sharadar** as the first proof-grade structured fundamentals source.
- Use **FMP** only for fast prototyping and non-claim demos until data quality is audited.

---

## 4.3 Analyst estimates

### What Quantfore needs

Minimum required fields:

- EPS estimates
- revenue estimates
- EBITDA / EBIT / FCF estimates where available
- contributor count
- consensus mean / median / high / low
- estimate date
- revision date
- fiscal period
- comparable actuals
- analyst-level or broker-level detail where licensed
- guidance where available
- point-in-time snapshots

Estimate revisions are one of the most plausible near-term alpha inputs, but only if timestamps and contributor histories are correct.

### Vendor view

| Vendor | Strength | Weakness | Best use |
|---|---|---|---|
| **LSEG I/B/E/S** | Industry-standard estimates, long history, global coverage, contributor detail, guidance and analytics. | Expensive and license-heavy. | Serious estimate-revision alpha validation. |
| **FactSet Estimates** | Strong API and institutional coverage; broad statement-line estimates. | Enterprise pricing and licensing. | Production-quality estimates. |
| **Visible Alpha** | Deep consensus from sell-side models; granular KPIs, segment assumptions, point-in-time data. | Expensive; history begins later than I/B/E/S for many use cases. | Differentiated KPI-level thesis-change signals. |
| **FMP/Finnhub** | Cheap and easy estimate endpoints. | Coverage and historical point-in-time quality must be validated. | Prototype workflow only. |
| **Intrinio** | Middle-tier commercial estimates and analyst data. | Requires vendor-specific coverage validation. | Possible mid-market compromise. |

### Decision

- Do not make estimate-revision alpha claims until tested on **LSEG I/B/E/S**, **FactSet**, **Visible Alpha**, or an equivalent source.
- Use **FMP/Finnhub** only to design the UX and factor schema.
- If Quantfore’s wedge becomes “thesis-change from expectations drift,” prioritise **Visible Alpha** because it contains granular KPI and sell-side model assumptions that generic estimates do not.

---

## 4.4 Transcripts and qualitative documents

### What Quantfore needs

Minimum required fields:

- transcript text
- speaker names
- speaker roles
- prepared remarks vs Q&A
- company ticker / CIK mapping
- event date/time
- publication timestamp
- fiscal quarter mapping
- revision/correction handling
- permission to store full text
- permission to store embeddings
- permission to display excerpts/summaries
- permission to create derived signals

The product’s proprietary data wedge depends on creating structured thesis-change features from qualitative documents. Licensing must explicitly allow the workflows used.

### Vendor view

| Vendor | Strength | Weakness | Best use |
|---|---|---|---|
| **FactSet Transcripts** | Near-real-time transcript APIs; institutional vendor. | Enterprise procurement and licensing. | Production-grade transcript ingestion. |
| **AlphaSense** | Strong library across transcripts, filings, broker research, expert calls, and search. | May be platform-first; API/derived-data/embedding rights must be negotiated. | Research workflow and qualitative corpus. |
| **FMP** | Easy transcript endpoints. | Coverage and rights must be verified. | Prototype thesis-memory graph. |
| **Finnhub** | Earnings call transcript API available. | Coverage and commercial rights require review. | Prototype/secondary source. |
| **Intrinio** | Corporate communications/earnings-related data. | Confirm whether full transcript text coverage meets requirements. | Possible commercial middle tier. |

### Decision

- Use **FMP/Finnhub** for the first thesis-memory prototype.
- Before storing embeddings or generated thesis signals commercially, obtain written confirmation that the licence allows: full-text storage, NLP processing, embedding storage, derived features, summaries, excerpts, and paid-user display.
- For production, evaluate **FactSet** and **AlphaSense** first.

---

## 4.5 Filings

### What Quantfore needs

Minimum required fields:

- accession number
- CIK
- form type
- filing date
- accepted timestamp
- report period
- HTML filing document
- XBRL facts
- exhibit metadata
- amended filings
- section extraction for 10-K/10-Q/8-K/S-1
- risk factor changes
- management discussion changes
- guidance/change language
- footnotes and accounting policy changes

### Vendor view

| Vendor | Strength | Weakness | Best use |
|---|---|---|---|
| **SEC EDGAR direct** | Official source, free, real-time JSON APIs, XBRL data. | Requires internal parsing and cleaning. | Canonical source. |
| **sec-api.io** | Convenient search/parsing layer over EDGAR. | Paid third-party; must confirm licence and accuracy. | Faster filing search/extraction. |
| **AlphaSense / FactSet / LSEG** | Strong search, document libraries, workflow tooling. | Expensive; redistribution constraints. | Pro research layer. |
| **FMP/Finnhub** | Can provide filing endpoints/links. | Not enough as canonical corpus alone. | Convenience layer. |

### Decision

- Keep **SEC EDGAR accession number** as the canonical document ID for all filing-derived features.
- Build internal parsers for the specific forms that matter most: 10-K, 10-Q, 8-K, S-1, 20-F, 6-K.
- Use third-party filing APIs only to accelerate retrieval/search, not as the sole source of truth.

---

## 5. Licensing matrix

| Licensing issue | Why it matters | Required action |
|---|---|---|
| Display rights | Paid users may see numbers, charts, transcript excerpts, or filings in the UI. | Confirm explicit rights for display inside a commercial SaaS product. |
| Redistribution rights | Some vendors allow internal use but prohibit redistribution to end users. | Negotiate redistribution or user-facing display terms. |
| Derived data rights | Quantfore will generate scores, embeddings, thesis-drift metrics, and summaries. | Confirm whether derived works/signals can be stored and commercialised. |
| Embedding rights | Transcript/filing text may be converted into vectors. | Confirm whether embeddings count as derived data and whether they can persist after licence termination. |
| AI/ML training rights | Vendor data may be used for model features or fine-tuning. | Explicitly ask whether ML training, feature generation, and backtesting are permitted. |
| Historical archive rights | Backtesting requires retaining old data states. | Confirm historical retention and post-termination usage. |
| Exchange fees | Real-time market data may require exchange-specific licensing. | Avoid real-time display until exchange fee obligations are clear. |
| User type classification | Non-professional vs professional users affect data permissions. | Build user classification if real-time market data is displayed. |
| Audit rights | Claims and compliance require reconstructing historical model decisions. | Ensure contract permits retaining audit logs and model inputs. |
| Content excerpts | Transcript/research snippets may be copyrighted/licensed. | Define maximum excerpt rules and summary-only policy where needed. |

---

## 6. Minimum data requirements by product feature

| Product feature | Required data | Acceptable prototype source | Required proof/production source |
|---|---|---|---|
| Company dashboard | Prices, fundamentals, filings, transcripts | FMP + SEC + Massive/Tiingo | Sharadar + SEC + licensed transcript provider |
| Quant score | Prices, fundamentals, revisions, risk metrics | FMP/Finnhub + price API | Sharadar + premium estimates |
| Thesis Memory Graph | Filings, transcripts, guidance, KPIs | SEC + FMP/Finnhub transcripts | SEC + FactSet/AlphaSense/Visible Alpha |
| Thesis Drift Index | Historical thesis state + transcript/filing deltas + estimate changes | SEC + FMP/Finnhub | SEC + premium transcripts + premium estimates |
| Portfolio risk/reward | Prices, vol, beta, correlations, fundamentals | Massive/Tiingo + FMP | Sharadar + market data vendor |
| Backtest engine | Point-in-time fundamentals, prices, delisted coverage, corporate actions | Not acceptable except for engineering tests | Sharadar or equivalent PIT dataset |
| Claims dashboard | Model outputs, historical predictions, realised outcomes | Internal ledger | Internal immutable prediction ledger + proof-grade source data |

---

## 7. Data quality tests before alpha validation

Every candidate vendor must pass these tests before its data can support model-performance claims.

### 7.1 Prices

- Compare split-adjusted prices against at least one independent source.
- Test dividend adjustment methodology.
- Verify delisted ticker availability.
- Check symbol-change handling.
- Validate stale/zero-volume days.
- Validate timezone and market calendar handling.

### 7.2 Fundamentals

- Compare selected income statement, balance sheet, and cash flow fields to SEC filings.
- Verify filing date vs report period vs availability date.
- Test restatement handling.
- Check negative/zero/null anomalies.
- Confirm standardisation across sectors.
- Confirm delisted-company fundamentals are retained.

### 7.3 Estimates

- Verify timestamp granularity.
- Confirm whether data is point-in-time or latest snapshot only.
- Check contributor count history.
- Validate actuals alignment to consensus basis.
- Check fiscal-period roll logic.
- Compare revisions around known earnings events.

### 7.4 Transcripts

- Verify transcript publication lag.
- Check speaker tagging quality.
- Check prepared remarks vs Q&A segmentation.
- Compare sample transcripts against company IR sources where available.
- Validate event-to-fiscal-quarter mapping.
- Confirm right to store, process, summarise, and embed text.

### 7.5 Filings

- Verify accession numbers.
- Confirm accepted timestamp.
- Validate amended filing handling.
- Test section extraction accuracy.
- Check XBRL tag mapping.
- Preserve original document link and raw text.

---

## 8. Vendor RFP questions

Before signing any vendor contract, ask these questions in writing.

### 8.1 Coverage and history

1. What is the exact coverage universe by country, exchange, security type, and date?
2. Are delisted securities included?
3. Is historical data point-in-time or revised to latest values?
4. How are restatements handled?
5. How are ticker changes, mergers, spin-offs, and bankruptcies represented?

### 8.2 Usage rights

1. Can Quantfore use the data in commercial SaaS dashboards?
2. Can Quantfore display raw values to paying users?
3. Can Quantfore redistribute charts or exported reports containing vendor data?
4. Can Quantfore generate and commercialise derived scores/signals?
5. Can Quantfore store embeddings or vector representations of text?
6. Can Quantfore train ML models on the data?
7. What rights survive contract termination?

### 8.3 Operational details

1. What is the API rate limit?
2. Is bulk delivery available?
3. Is Snowflake/S3/FTP delivery available?
4. What is the SLA?
5. Are there data correction notifications?
6. Are historical snapshots versioned?
7. Is there a data dictionary with field definitions?
8. Are support engineers available during onboarding?

### 8.4 Compliance and audit

1. Can Quantfore retain historical model inputs for compliance/audit logs?
2. Can Quantfore reproduce historical recommendations after licence expiration?
3. Are data corrections timestamped?
4. Are there restrictions on using the data for investment advice, recommendations, or portfolio analytics?
5. Are there separate restrictions for retail vs professional users?

---

## 9. Data architecture implication

The vendor decision should shape the database from day one. Quantfore should not build around one vendor’s field names.

### 9.1 Canonical entities

Required tables:

```text
security_master
company_master
exchange_calendar
price_daily
price_intraday
corporate_action
fundamental_fact
fundamental_statement
estimate_snapshot
estimate_revision
filing_document
filing_section
transcript_document
transcript_segment
macro_series
vendor_source_log
model_feature_snapshot
prediction_ledger
```

### 9.2 Required metadata fields

Every fact ingested should include:

```text
source_vendor
source_dataset
source_document_id
source_url_or_accession
as_of_date
period_end_date
filing_date
accepted_timestamp
vendor_created_at
vendor_updated_at
ingested_at
license_tier
is_revised
is_point_in_time
quality_flag
```

This is non-negotiable. Without these fields, Quantfore cannot reconstruct what the model knew at the time of a historical prediction.

---

## 10. Build sequence

### Phase 0 — Vendor trials and ingestion proof

Duration: 2–4 weeks

Tasks:

- Open trial accounts with FMP, Finnhub, Massive/Tiingo, Nasdaq Data Link/Sharadar if budget allows.
- Build ingestion adapters for prices, fundamentals, filings, transcripts, and estimates.
- Store all data into canonical schema, not vendor-shaped schema.
- Run 50-company data QA against SEC filings.
- Produce vendor quality report.

Exit criteria:

- 95%+ successful ingestion across S&P 500 sample.
- Filing date and report period correctly mapped.
- Corporate actions correctly represented for known split/dividend cases.
- Transcript/filing documents linked to correct ticker, CIK, fiscal period, and event date.

### Phase 1 — Prototype stack

Duration: 4–8 weeks

Tasks:

- Use FMP/Finnhub + SEC + Massive/Tiingo to build product workflows.
- Build company dashboard, thesis-memory graph, transcript summarisation, and initial scores.
- No public claims about alpha or outperformance.

Exit criteria:

- Users can inspect a company’s thesis changes over time.
- Product can explain score changes using source-linked evidence.
- User interviews confirm the workflow is valuable.

### Phase 2 — Proof-grade validation stack

Duration: 8–12 weeks

Tasks:

- Move model backtests to Sharadar or equivalent point-in-time source.
- Add premium estimates trial if estimate revisions are core to the signal.
- Run point-in-time backtests with transaction costs, benchmark comparisons, and walk-forward validation.
- Build prediction ledger.

Exit criteria:

- Backtest passes predefined rank IC, excess return, drawdown, turnover, and robustness thresholds.
- Model survives out-of-sample and regime-split tests.
- Every prediction can be reconstructed from stored inputs.

### Phase 3 — Production licensing

Duration: 4–12 weeks depending on vendor procurement.

Tasks:

- Negotiate data display/redistribution/derived-data rights.
- Add user-type classification for market-data display if required.
- Create data licence register.
- Add compliance review for every user-facing metric and chart.

Exit criteria:

- Product can be commercially launched without hidden data-rights risk.
- Terms allow every displayed, summarised, embedded, and derived-data workflow used in product.

---

## 11. Hard trade-offs

### 11.1 Cheap API speed vs proof quality

**Decision:** use cheap APIs for product discovery only.
**Reason:** the first product risk is workflow desirability, but the later company risk is fake alpha. Separate those risks.

### 11.2 Broad global coverage vs clean US proof

**Decision:** start with US equities.
**Reason:** EDGAR, US fundamentals, corporate actions, and US price history are easier to validate. Global coverage increases mapping, accounting, timezone, and licensing complexity.

### 11.3 Real-time market data vs thesis-change product

**Decision:** do not prioritise real-time data for v0.1.
**Reason:** Quantfore is not an execution or day-trading product. Daily/weekly thesis-change monitoring is enough for the positioning.

### 11.4 Premium estimates now vs later

**Decision:** prototype estimates cheaply, but validate with premium estimates before making claims.
**Reason:** estimate-revision alpha is plausible but extremely timestamp-sensitive. Bad historical snapshots can create fake signal.

### 11.5 Owning parsers vs outsourcing document intelligence

**Decision:** own the canonical EDGAR ingestion layer; optionally outsource search/enrichment.
**Reason:** thesis memory requires reproducibility and source traceability. Outsourced document search is useful, but not enough as the canonical record.

---

## 12. Immediate next actions

1. **Create vendor trial tracker** with columns: vendor, dataset, account owner, trial start, trial end, cost, rights status, ingestion status, QA result, decision.
2. **Build ingestion adapters** for SEC EDGAR, FMP, Massive/Tiingo, and one transcript source.
3. **Run 50-company QA** across mega-cap tech, financials, cyclicals, healthcare, energy, small/mid-cap, and delisted examples.
4. **Define proof universe**: US common stocks, minimum liquidity threshold, delisted included, excluding ADRs/preferreds/warrants unless deliberately included.
5. **Write licensing questionnaire** and send it before relying on any vendor for production.
6. **Do not make performance claims** until proof-grade data is in place and the prediction ledger has been tested.

---

## 13. Source notes

Sources consulted for this working document include official/vendor pages current as of 2026-06-24:

- SEC EDGAR APIs: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
- Nasdaq Data Link / Sharadar Core US Equities Bundle: https://data.nasdaq.com/databases/SFA
- Sharadar overview: https://www.sharadar.com/
- Massive.com / formerly Polygon.io stock API docs: https://massive.com/docs/rest/stocks/overview
- Massive.com rebrand note: https://massive.com/blog/polygon-is-now-massive/
- Tiingo API / pricing: https://www.tiingo.com/ and https://www.tiingo.com/about/pricing
- Financial Modeling Prep pricing/docs: https://site.financialmodelingprep.com/developer/docs/pricing
- Finnhub pricing/docs: https://finnhub.io/pricing and https://finnhub.io/docs/api
- Intrinio products/docs: https://intrinio.com/ and https://docs.intrinio.com/
- Alpha Vantage API documentation: https://www.alphavantage.co/documentation/
- LSEG I/B/E/S Estimates: https://www.lseg.com/en/data-analytics/financial-data/company-data/ibes-estimates
- FactSet API catalog and transcripts: https://developer.factset.com/api-catalog and https://www.factset.com/marketplace/catalog/product/documents-distributor-near-real-time-transcripts-api
- Visible Alpha / S&P Global Marketplace: https://www.marketplace.spglobal.com/en/datasets/visible-alpha-estimates-%281714423281%29
- AlphaSense financial data / research platform: https://www.alpha-sense.com/platform/financial-data/
