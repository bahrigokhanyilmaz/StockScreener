# Stock Screener — Architecture & Decisions

## Overview

A value stock screening application that combines fundamental financial analysis
with news sentiment scoring to produce an "Investability Score" for each stock.
Built on AWS serverless, designed to be modular, scalable, and cost-effective.

The user is a long-term value investor learning to build this from scratch.
Always explain concepts, teach the stack, and keep the user engaged.
Always keep README and steering docs updated without being asked.

## Design Principles (NON-NEGOTIABLE)

1. **No Shortcuts** — Never sacrifice long-term quality for short-term convenience
2. **Modularity & Swappability** — Interfaces everywhere, swap via config
3. **Provider Abstraction** — Strategy Pattern for data sources
4. **Free First, Upgrade Later** — Architecture doesn't change, only implementation
5. **Clean As You Go** — No dead code or obsolete artifacts
6. **Security By Default** — SSM for secrets, never hardcode
7. **Teach While Building** — Explain every concept
8. **Production-Grade Tooling** — Docker, ARM64, CDK, version control
9. **Always Reassess** — Pivot when better options exist
10. **Test Locally First** — Use `.venv/bin/python3` for local tests, deploy only when verified
11. **Keep Docs Updated** — Steering file and README always reflect current state
12. **Missing Data = FAIL** — Stocks missing any filter metric are rejected (no skipping)
13. **Be Frugal** — Plan before deploying, minimize wasted API calls

## Current Architecture (Deployed & Working)

### 8-Step Pipeline

```
EventBridge (Mon-Fri 4PM ET / 8PM UTC)
    → Step Functions (stock-screener-pipeline)
        → Step 1: EDGAR Bulk Fundamentals (~10 calls → 5,097 companies, ~4s)
        → Step 2: Pre-Screen (5 EDGAR filters → ~69 pass, instant)
        → Step 3: Enrichment (Polygon 1 call + Finnhub 3 calls/candidate → ~5-8 min)
        → Step 4: Full Screen (12 filters, missing data = FAIL → ~6 pass, instant)
        → Step 5: News Fetch (TickerTick, 6.5s/stock → ~40s)
        → Step 6: Sentiment Analysis (Bedrock Claude Haiku 4.5 → ~5 min)
        → Step 7: Score Calculator (investability + Polygon descriptions + DynamoDB → ~75s)
        → Step 8: Alert Checker (thresholds + tracking lifecycle → SNS, instant)

Total: ~15-20 minutes per run.
```

### Pipeline Step Details

**Step 1 — EDGAR Bulk Fetch** (~2-3 minutes)
- Source: SEC EDGAR Frames API (free, unlimited, US government)
- Dynamically discovers latest quarter with >= 4000 companies
- **TTM (Trailing Twelve Months)** for income items:
  - Primary: direct sum of 4 quarterly frames (if all 4 present for a company)
  - Fallback: `Annual + Latest_Q1 - Prior_Q1` (algebraically identical, for companies with non-calendar fiscal years that don't have all 4 quarters in EDGAR)
  - A company only gets a TTM value via one method — never a partial sum
- **Prior TTM**: same derivation shifted 1 year back (for YoY growth)
- Uses BROAD universal tags for maximum coverage:
  - `Liabilities` (4,862 cos) + `LiabilitiesCurrent` (4,199) → D/E = (Liabilities - LiabilitiesCurrent) / Equity
  - `NetIncomeLoss` (4,944) → TTM EPS (for industry quartile computation only)
  - `OperatingIncomeLoss` (4,004) → Operating Margin
  - Both `RevenueFromContractWithCustomer...` (2,306) + `Revenues` (1,683) merged per quarter for TTM
  - `CommonStockSharesOutstanding` + `WeightedAverageNumberOfDilutedSharesOutstanding` fallback
- **P/E for candidates** is recalculated in Step 3 using Finnhub's `peNormalizedAnnual` (strips one-time items)
- All dates computed dynamically from today — no hardcoded quarters
- Output: S3 `step1_fundamentals_*.json`

**Step 2 — Pre-Screen** (instant)
- Applies 5 EDGAR-evaluable filters:
  - Debt/Equity < 1.0 (overridable if ICR > 3.0)
  - Quick Ratio > 1.0
  - Operating Margin > 0%
  - EPS Growth YoY > 0%
  - Revenue Growth YoY > 0%
- 5,097 → ~69 pass
- Also computes industry medians: loads `ticker_industry_map.json` from S3, groups all stocks by SEC SIC industry, computes medians, persists to DynamoDB as `INDUSTRY_AVG#` items
- Output: S3 `step2_prescreen_*.json` (passing_stocks + all_screened)

**Step 3 — Price + Metrics Enrichment** (~5-8 minutes)
- Stage 1: Polygon.io Grouped Daily — ONE API call → prices for ALL 12,000+ US stocks (date = T-2 for free tier)
- Stage 2: Local P/E calculation + industry-relative pre-filter:
  - P/E = Polygon Price ÷ EDGAR TTM EPS (for all pre-screen passers)
  - Loads Step 1 full universe from S3 to compute P/E for all 4,500+ stocks
  - Computes 25th percentile P/E per SEC SIC industry (98 industries)
  - Sanity filter: excludes P/E < 1, EPS > revenue/share, P/E > 500 from quartile computation
  - Stock passes if its P/E < its industry's lower quartile
  - Also requires PEG < 1.0 and P/FCF < 20 (locally computed)
- Stage 3: Finnhub for survivors (~5-45 stocks, 3 calls each):
  - `/stock/metric?metric=all` → `peNormalizedAnnual` overrides EDGAR P/E (strips one-time items), Forward P/E, Est. LT Growth
  - `/stock/recommendation` → Analyst consensus (1-5 scale)
  - `/stock/profile2` → Logo, weburl, industry
- Pacing: 3s between stocks (safe under 60 calls/min)
- Output: S3 `step3_enriched_*.json`

**Step 4 — Full Screen** (instant)
- Applies ALL 12 active filters (2 deferred)
- **Missing data = FAIL** (strict: if Finnhub didn't return Forward P/E, stock fails)
- Deferred (skipped without penalty): `target_price_upside`, `institutional_transactions`
- Skipped: `sentiment_score` (calculated later in Step 6)
- ~69 enriched → ~6 pass
- Output: S3 `step4_fullscreen_*.json`

**Step 5 — News Fetch** (~40 seconds for 6 stocks)
- TickerTick API: 10 articles per stock, 6.5s rate limit pacing
- Sources: Reuters, WSJ, CNBC, SEC filings, etc.
- Output: S3 `step5_news_*.json`

**Step 6 — Sentiment Analysis** (~5 minutes)
- Amazon Bedrock Claude Haiku 4.5 (`us.anthropic.claude-haiku-4-5-20251001-v1:0`)
- Per article: relevance (0-1), sentiment (-1 to +1), confidence (0-1), risk_flags, summary
- Aggregate per stock: confidence-weighted average sentiment
- Cost: ~$0.12/day for ~60 articles
- Output: S3 `step6_sentiment_*.json`

**Step 7 — Score Calculator** (~2-3 minutes)
- Loads existing risk flag ledgers from DynamoDB (for lifecycle management)
- Investability formula: `(0.7 × fundamental) + (0.3 × sentiment_normalized) + risk_penalties`
- Sentiment normalized: `50 + (raw × 50 × confidence)` — maps to 0-100
- Risk flag ledger: merges new flags from sentiment with existing, applies time-decay
- Fetches company descriptions from Polygon `/v3/reference/tickers/{ticker}` (12s pacing)
- Enriches stocks with `sic_industry` from S3 industry map
- Backfills 30-day price history from Polygon `/v2/aggs/ticker/{ticker}/range/1/day/` (12s pacing)
- Persists ALL to DynamoDB: LATEST, SCORE#date, TRACKING, PRICE_HISTORY#
- Output: S3 `step7_scores_*.json`

**Step 8 — Alert Checker** (instant)
- Detects: new passers, dropped stocks, sentiment crashes, risk flags, grace expiry
- Updates tracking lifecycle: ACTIVE → GRACE (90-day) → dropped
- Sends email via SNS if thresholds breached
- Output: S3 `step8_alerts_*.json`

### Data Sources & Rate Limits

| Source | What | Rate Limit | Status |
|--------|------|-----------|--------|
| SEC EDGAR Frames API | Bulk fundamentals (5,097 companies) | Unlimited | ACTIVE |
| Polygon.io Grouped Daily | ALL US stock prices in 1 call | 5 req/min | ACTIVE |
| Polygon.io Ticker Details | Company descriptions | 5 req/min | ACTIVE |
| Finnhub /stock/metric | Forward P/E, LT Growth | 60 req/min | ACTIVE |
| Finnhub /stock/recommendation | Analyst consensus | 60 req/min | ACTIVE |
| Finnhub /stock/profile2 | Logo, weburl, industry | 60 req/min | ACTIVE |
| TickerTick | News articles | 10 req/min | ACTIVE |
| Bedrock Claude Haiku 4.5 | Sentiment analysis | Pay per token | ACTIVE |
| FMP | — | — | INACTIVE (bandwidth exhausted) |
| Alpha Vantage | — | — | INACTIVE (25/day too few) |
| Twelve Data | — | — | INACTIVE (replaced by Polygon) |

### AWS Resources

| Resource | Name/ID |
|----------|---------|
| AWS Account | 116488731375, us-east-2 |
| AWS Profile | stock-screener |
| CloudFormation Stack | StockScreenerStack |
| S3 Bucket | stock-screener-raw-data-116488731375 |
| DynamoDB Table | stock-screener-data |
| DynamoDB GSI | tracking-status-index (PK: tracking_status, SK: last_updated) |
| Step Functions | stock-screener-pipeline |
| EventBridge Rule | stock-screener-daily-trigger (Mon-Fri 8PM UTC) |
| SNS Topic | stock-screener-alerts |
| API Gateway | https://kw8mlahpj2.execute-api.us-east-2.amazonaws.com/prod/ |
| Lambda (Step 1) | stock-screener-fundamentals-fetcher |
| Lambda (Steps 2 & 4) | stock-screener-filter |
| Lambda (Step 3) | stock-screener-price-enrichment |
| Lambda (Step 5) | stock-screener-news-fetcher |
| Lambda (Step 6) | stock-screener-sentiment-analyzer |
| Lambda (Step 7) | stock-screener-score-calculator |
| Lambda (Step 8) | stock-screener-alert-checker |
| Lambda (API) | stock-screener-api |
| Amplify Hosting | d2ned6rk557ndc (https://main.d2ned6rk557ndc.amplifyapp.com) |

### SSM Parameters

| Service | SSM Path | Status |
|---------|----------|--------|
| Polygon.io | /stock-screener/polygon-api-key | ACTIVE |
| Finnhub | /stock-screener/finnhub-api-key | ACTIVE |
| FMP | /stock-screener/fmp-api-key | INACTIVE |
| Alpha Vantage | /stock-screener/alpha-vantage-api-key | INACTIVE |
| Twelve Data | /stock-screener/twelve-data-api-key | INACTIVE |

### DynamoDB Schema (Single-Table)

| PK | SK | Purpose |
|----|----|---------| 
| STOCK#{ticker} | LATEST | Current scores + all fundamentals + profile (overwritten daily) |
| STOCK#{ticker} | SCORE#{date} | Historical score snapshot (one per day, never overwritten) |
| STOCK#{ticker} | TRACKING | Tracking status (ACTIVE/GRACE/MANUAL) |
| PRICE_HISTORY#{ticker} | DAILY | 30-day OHLCV price bars (overwritten daily) |
| INDUSTRY_AVG#{industry} | METRICS | Industry median benchmarks (overwritten each pipeline run) |

**GSI**: `tracking-status-index` (PK: tracking_status, SK: last_updated, projection: ALL)

**LATEST item fields**: symbol, company_name, company_description, logo, weburl, sector, industry, sic_industry, price, market_cap, investability_score, fundamental_score, sentiment_score, sentiment_confidence, risk_flags (ledger: list of objects with flag/first_seen/last_seen/days_active), passes_screen, tracking_status, pe_ratio, forward_pe, peg_ratio, price_to_fcf, debt_to_equity, quick_ratio, interest_coverage_ratio, operating_margin, eps_growth_yoy, revenue_growth_yoy, est_lt_growth, analyst_recommendation, target_price_upside, institutional_transactions, last_updated

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | /stocks | All tracked stocks with latest scores (sorted by investability) |
| GET | /stocks/{ticker} | Full stock detail (profile + fundamentals + sentiment) |
| GET | /stocks/{ticker}/history | Score history time series (for trend charts) |
| GET | /stocks/{ticker}/prices | 30-day OHLCV price bars (for sparkline + trend) |
| GET | /stocks/{ticker}/news | Live news from TickerTick |
| POST | /stocks/{ticker}/track | Manually track a stock |
| DELETE | /stocks/{ticker}/track | Stop tracking |
| GET | /industries | Industry median benchmarks (from SEC SIC data) |
| GET | /pipeline/status | Pipeline summary (active/grace counts) |

### Frontend (React + TypeScript, Vite 8)

Located at `frontend/`. Deployed to Amplify: https://main.d2ned6rk557ndc.amplifyapp.com
Deploy script: `./scripts/deploy_frontend.sh`

Layout: Filters (collapsible top bar) → 2-column (table + detail panel)

Components:
- `App.tsx` — Layout, filter sliders at top, fetches trends for all stocks on load
- `StockTable.tsx` — All metrics + 30d trend column + risk badges + ICR override badge
- `StockDetail.tsx` — Two tabs:
  - Overview: Company profile, score cards, sparkline chart, risk flags (with ledger metadata), news
  - Metrics Guide: Industry comparison (real medians from SEC SIC data), scoring methodology, metric definitions with interpretation
- `FilterSliders.tsx` — Client-side re-filtering with D/E override note ("or ICR > 3.0x")
- `MetricsGuide.tsx` — Industry averages (fetched from API), metric definitions, scoring formulas explained
- `utils/trends.ts` — calculateTrend: changePercent, consecutiveDown/Up, isFalling/isStabilizing/isRecovering

### Screening Filters Config

Source of truth: `shared/config/screener-filters.json`

**Hard Filters (8 total, ~100% EDGAR+Polygon coverage):**
| Filter | Type | Threshold | Data Format | Source |
|--------|------|---------|-------------|--------|
| pe_ratio | max | Industry lower quartile (25th pctile) | ratio | Local (Polygon ÷ EDGAR TTM EPS) |
| peg_ratio | max | 1.0 | ratio | Local (P/E ÷ EDGAR TTM EPS growth) |
| price_to_fcf | max | 20 | ratio | Local (Polygon ÷ EDGAR TTM FCF) |
| debt_to_equity | max | 1.0 (or ICR > 3.0) | ratio | EDGAR: (Liabilities - LiabilitiesCurrent) / Equity |
| quick_ratio | min | 1.0 | ratio | EDGAR |
| operating_margin | min | 0% | percent_as_decimal | EDGAR: OperatingIncomeLoss / Revenue |
| eps_growth_yoy | min | 0% | percent_as_decimal | EDGAR TTM vs prior TTM |
| revenue_growth_yoy | min | 0% | percent_as_decimal | EDGAR TTM vs prior TTM |

**Soft Filters (applied if data exists, skipped if Finnhub has no coverage):**
| Filter | Type | Threshold | Source |
|--------|------|---------|--------|
| forward_pe | max | 20 | Finnhub |
| est_lt_growth | min | 0% | Finnhub |
| analyst_recommendation | max | 3.0 | Finnhub |

**Deferred (no free data source):**
| Filter | Reason |
|--------|--------|
| target_price_upside | Finnhub returns empty on free tier |
| institutional_transactions | No reliable free source |
| sentiment_score | Applied post-scoring in Step 6/7, not in screen |

### Investability Score Formula

```
investability = (0.7 × fundamental_score) + (0.3 × sentiment_normalized) + risk_penalties

fundamental_score: 0-100
  Per filter: 0 = at threshold (barely passed), 1.0 = best possible
  For percent_as_decimal filters: value × 100 before comparing to config ranges
  Final = average(per_filter_scores) × 100

sentiment_normalized: 0-100
  = 50 + (raw_sentiment × 50 × confidence)
  Where raw_sentiment is -1 to +1, confidence is 0 to 1
  Neutral (no news or low confidence) = 50
  
risk_penalties: applied from risk flag ledger (see below)
Final: clamped [0, 100]

Range verification:
  Max: (0.7 × 100) + (0.3 × 100) = 100 ✓
  Min: 0 (clamped) ✓
  Neutral midpoint: (0.7 × 50) + (0.3 × 50) = 50 ✓
```

### Risk Flag System

**Constrained flag list** (Claude can ONLY return these 8 flags):
| Flag | Penalty | Category |
|------|---------|----------|
| fraud_allegation | -35 | Uncertain (persists) |
| SEC_investigation | -30 | Uncertain (persists) |
| accounting_irregularity | -25 | Uncertain (persists) |
| regulatory_risk | -15 | Uncertain (persists) |
| lawsuit | -10 | Uncertain (persists) |
| revenue_risk | -15 | One-time (decays over 5 days) |
| management_departure | -10 | One-time (decays over 5 days) |
| product_recall | -10 | One-time (decays over 5 days) |

**Risk Flag Ledger** (tracked over time in DynamoDB):
- Each flag stored with: `flag`, `first_seen` (article publication date), `last_seen`, `days_active`
- `first_seen` uses the article's publication date (not pipeline run date) so time-decay starts when the market reacted
- Uncertain flags: full penalty persists until flag expires (no decay)
- One-time flags: penalty decays linearly to 0 over 5 days from `first_seen`
- Flags expire from ledger after 14 days of not being re-confirmed in new articles
- `risk_flags` in DynamoDB LATEST is now a list of objects (not strings)

### Interest Coverage Ratio (D/E Override)

```
ICR = Operating Income / Interest Expense (from EDGAR)
```
- If D/E > 1.0 (would normally fail), stock can still pass if ICR > 3.0
- Meaning: company earns 3x+ its interest payments — debt is serviceable
- Shown in table: D/E value in amber with "ICR✓" badge (not red)
- Filter slider shows "or ICR > 3.0x" note

### Industry Averages (Static Reference Map)

Architecture:
- `ticker_industry_map.json` in S3 (600KB, 9,075 tickers → 401 SEC SIC industries)
- Built once from SEC submissions API (one-time script, re-run monthly if needed)
- Step 2 (pre-screen) loads map, joins to all 5,097 stocks, computes medians per industry
- Persisted to DynamoDB as `INDUSTRY_AVG#{industry}` items (189 industries, min 5 stocks each)
- API endpoint: `GET /industries` returns all industry medians
- Frontend MetricsGuide matches via `sic_industry` field on each stock
- Metrics computed: debt_to_equity, quick_ratio, operating_margin, eps_growth_yoy, revenue_growth_yoy
- P/E median not available (requires prices which aren't in Step 1)

### Price History & Trend Detection

- Score calculator fetches 30-day OHLCV bars from Polygon `/v2/aggs/ticker/{ticker}/range/1/day/`
- Stored as `PRICE_HISTORY#{ticker}` in DynamoDB (one item per stock, ~19 trading bars)
- Skips backfill if already done today (idempotent)
- API endpoint: `GET /stocks/{ticker}/prices`
- Frontend calculates trends from bars:
  - `changePercent`: overall period change
  - `consecutiveDownDays`: sustained directional decline (not volatility)
  - `consecutiveUpDays`: sustained recovery
  - **FALLING**: 5+ consecutive down days OR -15% in 10 trading days
  - **STABILIZING**: Was falling (>10% decline in days 5-14 ago), last 1-2 days flat/up
  - **RECOVERING**: Was falling, now 3+ consecutive up days
- Table shows 30d trend column (arrow + %, color-coded)
- Detail panel shows SVG sparkline chart with trend state and day count

### Key Decisions Log

| Decision | Rationale |
|----------|-----------|
| EDGAR over FMP/yfinance for bulk | EDGAR Frames API: ~10 calls for 5,097 companies. FMP bandwidth-limited, yfinance blocked from Lambda |
| Polygon Grouped Daily for prices | 1 call = 12,000+ stock prices. Replaced Twelve Data (8/min too slow) |
| Finnhub for analyst data | Forward P/E, LT Growth, Analyst Recommendation. 60/min free tier |
| Polygon for company descriptions | Finnhub profile2 free tier doesn't include descriptions. Polygon does |
| Polygon for 30-day price history | Per-stock OHLCV bars for trend detection. 5/min, only for ~6 final stocks |
| Deferred institutional_transactions | No reliable free source. Finnhub `/stock/institutional-ownership` returns access denied |
| Deferred target_price_upside | Finnhub `/stock/price-target` returns empty on free tier |
| Local growth/FCF computation | EPS growth from EDGAR (CY2025-CY2024). Gives ~100% coverage vs 36-47% from Finnhub |
| Missing data = FAIL in full screen | Conservative: if data unavailable, stock doesn't qualify |
| D/E override by ICR | Company with D/E > 1.0 passes if Interest Coverage > 3.0 (can service debt) |
| Static industry map in S3 | SEC SIC codes for 9,075 tickers. One-time build, never changes. No API calls per run |
| Industry medians from full universe | Computed in Step 2 from all 5,097 stocks (not just filtered survivors) |
| Risk flags constrained to 8 values | Prevents Claude from inventing flags and double-counting |
| Time-decay on one-time risk flags | Market prices in contract losses within days. Penalty fades over 5 days |
| first_seen from article date | Time-decay starts when market reacted (article published), not when we detected it |
| Sentiment normalized to 0-100 | Old formula maxed at 77.5. New: 50 = neutral, properly fills 0-100 range |
| Fundamental score 0-1 per filter | Removed old `× 0.5 + 0.5` compression. 0 = at threshold, 1 = best. Simple |
| percent_as_decimal conversion in scoring | Data stores 0.15, config uses 15. Must × 100 before comparing |
| Strip markdown from Claude responses | Claude wraps JSON in ```json fences. Parser strips before json.loads() |
| Broad XBRL tags over specific | `Liabilities` (4,862) vs `LongTermDebt` (1,601). Universal coverage, no tag guessing |
| D/E = (Liabilities - LiabilitiesCurrent) / Equity | Captures ALL non-current obligations. Companies can't hide behind variant tag names |
| Multi-tag revenue for TTM | Both `RevenueFromContract...` + `Revenues` merged across ALL TTM quarters |
| Diluted shares fallback | `WeightedAverageNumberOfDilutedSharesOutstanding` fills 500+ companies missing from instant frame |
| Dynamic quarter discovery | No hardcoded dates. Tests from newest to oldest, picks first with >=4000 companies |
| TTM = 4 actual quarters (not annualized) | Sum of Q1+Q4+Q3+Q2 with annual derivation fallback. No shortcuts |
| Prior TTM = same derivation shifted 1 year | Gives proper rolling YoY growth |
| P/E is industry-relative (lower quartile) | Computed from full universe (98 industries). No hardcoded threshold |
| Soft filters for Finnhub-dependent metrics | forward_pe, est_lt_growth, analyst_recommendation: skip if absent, apply if present |
| Polygon T-2 for price date | Free tier requires completed trading day; always go back 2 days |
| Finnhub peNormalizedAnnual for P/E | epsTTM still includes one-time items; peNormalized strips them. Prevents VISN/RIGL-type artifacts |
| Revenue TTM: require all 4 quarters or use derivation | Partial sums (3 of 4 quarters) produce wrong growth; must be all-or-nothing |
| P/E quartile sanity: exclude P/E<1, EPS>revenue/share | One-time gains create impossible P/E values that pollute industry distributions |
| Step 1 timeout 10 minutes | Multi-tag revenue TTM needs ~50 EDGAR API calls; 5min was too short |
| yfinance blocked from Lambda | Yahoo blocks AWS data center IPs. Can't use from Lambda |
| finvizfinance blocked from Lambda | 403 Forbidden from AWS IPs |

### Build Progress

| Phase | Status |
|-------|--------|
| EDGAR fundamentals pipeline | COMPLETE |
| Polygon prices + Finnhub enrichment | COMPLETE |
| Two-pass screening (pre + full) | COMPLETE |
| News + Sentiment (TickerTick + Bedrock) | COMPLETE |
| Scoring + DynamoDB persistence | COMPLETE |
| Alert checker + SNS | COMPLETE |
| API Gateway (REST) | COMPLETE |
| React dashboard (table + detail + news) | COMPLETE |
| Company profiles (Finnhub + Polygon) | COMPLETE |
| Amplify deployment (public URL) | COMPLETE |
| Industry averages (SEC SIC static map) | COMPLETE |
| Interest Coverage Ratio + D/E override | COMPLETE |
| Risk flag ledger (time-decay lifecycle) | COMPLETE |
| 30-day price history + trend detection | COMPLETE |
| Scoring formula normalization (0-100) | COMPLETE |
| Metrics Guide (definitions + methodology) | COMPLETE |
| TTM EPS (proper 4-quarter sum) | COMPLETE |
| Broad XBRL tags (universal coverage) | COMPLETE |
| Industry-relative P/E (lower quartile) | COMPLETE |
| Soft Finnhub filters | COMPLETE |
| Dynamic EDGAR dates (no hardcoded quarters) | COMPLETE |
| Custom domain for Amplify | NEXT (user to purchase domain) |
| Retroactive analysis (Athena) | FUTURE |

### Conventions

- Lambdas: Python 3.12, ARM64, handler.py + requirements.txt per folder
- Dependencies: Docker-bundled via PythonFunction (or plain Function if no deps)
- Infrastructure: TypeScript CDK, single stack
- Config: JSON in shared/config/ — single source of truth
- Naming: kebab-case folders, snake_case Python, camelCase TypeScript
- Secrets: SSM Parameter Store (SecureString), never in code
- Pin dependency versions
- Test locally first, deploy only when verified
- Remove dead code immediately
- Commit and push after every meaningful change
- GitHub: https://github.com/bahrigokhanyilmaz/StockScreener
