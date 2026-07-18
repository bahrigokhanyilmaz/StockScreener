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

**Step 1 — EDGAR Bulk Fetch** (~4 seconds)
- Source: SEC EDGAR Frames API (free, unlimited, US government)
- ~10 API calls → bulk financials for ALL ~5,097 US public companies
- Data: Net Income, Revenue, Operating Income, Equity, Debt, Assets, Liabilities, Shares, Cash, CapEx, Operating CF
- Calculates locally: EPS, D/E, Quick Ratio, Operating Margin, EPS Growth YoY, Revenue Growth YoY, FCF per share
- CY2025 primary, CY2024 prior year for growth calculations
- Output: S3 `step1_fundamentals_*.json`

**Step 2 — Pre-Screen** (instant)
- Applies 5 EDGAR-evaluable filters:
  - Debt/Equity < 1.0
  - Quick Ratio > 1.0
  - Operating Margin > 0%
  - EPS Growth YoY > 0%
  - Revenue Growth YoY > 0%
- 5,097 → ~69 pass
- Output: S3 `step2_prescreen_*.json` (passing_stocks + all_screened)

**Step 3 — Price + Metrics Enrichment** (~5-8 minutes)
- Stage 1: Polygon.io Grouped Daily — ONE API call → prices for ALL 12,000+ US stocks
- Stage 2: Local P/E calculation (Price ÷ EDGAR EPS) + pre-filter (P/E<50 + other hard filters)
- Stage 3: Finnhub for survivors (~50-80 stocks, 3 calls each):
  - `/stock/metric?metric=all` → Forward P/E, Est. LT Growth
  - `/stock/recommendation` → Analyst consensus (1-5 scale)
  - `/stock/profile2` → Logo, weburl, industry
- Local PEG: P/E ÷ (EDGAR EPS Growth × 100)
- Local Price/FCF: Polygon Price ÷ EDGAR FCF per share
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

**Step 7 — Score Calculator** (~75 seconds)
- Investability formula: `(0.7 × fundamental) + (0.3 × sentiment_adj) + risk_penalties`
- Fetches company descriptions from Polygon `/v3/reference/tickers/{ticker}` (12s pacing, 5/min)
- Persists ALL metrics to DynamoDB:
  - LATEST item: all scores, fundamentals, sentiment, profile data
  - SCORE#date item: historical snapshot (for trend charts)
  - TRACKING item: status (ACTIVE/GRACE/MANUAL)
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

**GSI**: `tracking-status-index` (PK: tracking_status, SK: last_updated, projection: ALL)

**LATEST item fields**: symbol, company_name, company_description, logo, weburl, sector, industry, price, market_cap, investability_score, fundamental_score, sentiment_score, sentiment_confidence, risk_flags, passes_screen, tracking_status, pe_ratio, forward_pe, peg_ratio, price_to_fcf, debt_to_equity, quick_ratio, operating_margin, eps_growth_yoy, revenue_growth_yoy, est_lt_growth, analyst_recommendation, target_price_upside, institutional_transactions, last_updated

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | /stocks | All tracked stocks with latest scores (sorted by investability) |
| GET | /stocks/{ticker} | Full stock detail (profile + fundamentals + sentiment) |
| GET | /stocks/{ticker}/history | Score history time series (for trend charts) |
| GET | /stocks/{ticker}/news | Live news from TickerTick |
| POST | /stocks/{ticker}/track | Manually track a stock |
| DELETE | /stocks/{ticker}/track | Stop tracking |
| GET | /pipeline/status | Pipeline summary (active/grace counts) |

### Frontend (React + TypeScript, Vite 8)

Located at `frontend/`. Three-panel layout:
- **Left**: Stock list (sorted by investability score)
- **Center**: Metrics table (horizontally scrollable, color-coded pass=green/fail=red)
- **Right**: Detail panel (company profile + business model + scores + news)

Components:
- `StockTable.tsx` — Metrics table with all 12+ filter metrics, color-coded
- `StockDetail.tsx` — Company description, score cards, risk flags, live news
- `App.tsx` — Layout + filter sliders + state management

### Screening Filters Config

Source of truth: `shared/config/screener-filters.json`

| Filter | Type | Default | Data Format | Source |
|--------|------|---------|-------------|--------|
| pe_ratio | max | 50 | ratio | Local (Polygon ÷ EDGAR) |
| forward_pe | max | 20 | ratio | Finnhub |
| peg_ratio | max | 1.0 | ratio | Local (P/E ÷ EDGAR growth) |
| price_to_fcf | max | 20 | ratio | Local (Polygon ÷ EDGAR) |
| debt_to_equity | max | 1.0 | ratio | EDGAR |
| quick_ratio | min | 1.0 | ratio | EDGAR |
| operating_margin | min | 0% | percent_as_decimal | EDGAR |
| eps_growth_yoy | min | 0% | percent_as_decimal | EDGAR |
| revenue_growth_yoy | min | 0% | percent_as_decimal | EDGAR |
| est_lt_growth | min | 0% | percent_as_decimal | Finnhub |
| analyst_recommendation | max | 3.0 | ratio | Finnhub |
| sentiment_score | min | -0.3 | ratio | Bedrock Claude |
| target_price_upside | min | 20% | percent_as_decimal | DEFERRED |
| institutional_transactions | min | 0% | percent_as_decimal | DEFERRED |

### Investability Score Formula

```
investability = (0.7 × fundamental_score) + (0.3 × sentiment_adjustment) + risk_penalties

fundamental_score: 0-100 (how strongly stock passes value filters)
sentiment_adjustment: sentiment_score × 25 × confidence
risk_penalties: -10 to -35 per flag (SEC investigation, fraud, etc.)
Final: clamped 0-100
```

### Key Decisions Log

| Decision | Rationale |
|----------|-----------|
| EDGAR over FMP/yfinance for bulk | EDGAR Frames API: ~10 calls for 5,097 companies. FMP bandwidth-limited, yfinance blocked from Lambda |
| Polygon Grouped Daily for prices | 1 call = 12,000+ stock prices. Replaced Twelve Data (8/min too slow) |
| Finnhub for analyst data | Forward P/E, LT Growth, Analyst Recommendation. 60/min free tier |
| Polygon for company descriptions | Finnhub profile2 free tier doesn't include descriptions. Polygon does |
| Deferred institutional_transactions | No reliable free source. Finnhub `/stock/institutional-ownership` returns access denied |
| Deferred target_price_upside | Finnhub `/stock/price-target` returns empty on free tier |
| Local growth/FCF computation | EPS growth from EDGAR (CY2025-CY2024). Gives ~100% coverage vs 36-47% from Finnhub |
| Missing data = FAIL in full screen | Conservative: if data unavailable, stock doesn't qualify |
| PythonFunction for score-calculator | Needed requests library for Polygon API calls |
| Score calculator fetches descriptions | Only ~6 final stocks need Polygon descriptions (5/min is fine for 6 stocks) |
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
| Amplify deployment (public URL) | NEXT |
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
