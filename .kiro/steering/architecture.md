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
        → Step 1: EDGAR Bulk Fundamentals (~10 calls → 5,097 companies, ~3 min)
        → Step 2: Pre-Screen (non-price EDGAR filters → ~500 pass, instant)
        → Step 3: Enrichment (Polygon 1 call + FMP 6 calls/candidate → ~2 min)
        → Step 4: Full Screen + Scoring (fundamental_score + soft filters, instant)
        → Step 5: News Fetch (FMP + TickerTick merged → ~4 min)
        → Step 6: Sentiment Analysis (Bedrock Claude Haiku 4.5 → ~5 min)
        → Step 7: Score Calculator (investability + FMP price history + DynamoDB → ~1 min)
        → Step 8: Alert Checker (thresholds + tracking lifecycle → SNS, instant)

Total: ~12-15 minutes per run.
```

### Pipeline Step Details & Reasoning

**Step 1 — EDGAR Bulk Fetch** (~3 minutes)
- Source: SEC EDGAR Frames API (free, unlimited, US government)
- Fetches fundamentals for ~5,097 US companies in ~10 bulk API calls
- Computes TTM (sum of 4 quarters or annual derivation fallback)
- Computes Prior TTM (shifted 1 year back) for YoY growth
- Uses broad XBRL tags for maximum coverage (Liabilities, Revenue, NetIncome, OperatingIncome, etc.)
- All dates computed dynamically from today — no hardcoded quarters
- Output: ~5,097 stocks with EPS, D/E, QR, Operating Margin, Revenue Growth, FCF per share
- **Reasoning:** EDGAR is free, covers the entire US market, and gives us the raw fundamentals needed for initial filtering. No API key required, no rate limits.

**Step 2 — Pre-Screen** (instant)
- Applies non-price hard filters (these don't require stock prices):
  - Debt/Equity ≤ 1.0 (override: ICR > 3.0 — can service debt comfortably)
  - Quick Ratio > 1.0 (can pay short-term obligations)
  - Operating Margin > 0% (override: Revenue Growth > 20% — investing for growth)
  - Revenue Growth YoY > 0% (top-line expanding)
- Missing data = skip (pre-screen is lenient because EDGAR coverage varies)
- ~5,097 → ~500 pass (lenient because many stocks have None fields that get skipped)
- Also computes industry medians (D/E, QR, Op Margin, Rev Growth) from full universe and persists to DynamoDB
- **Reasoning:** Cheap first, expensive second. Eliminates obviously disqualified stocks before we spend any API calls. Price-dependent checks (P/E, PEG, P/FCF) cannot run here because EDGAR doesn't provide stock prices.

**Step 3 — Enrichment** (~2-3 minutes)
- **Stage A — Bulk prices:** Polygon.io Grouped Daily (1 free API call → 12,000+ US stock prices)
- **Stage B — Industry P/E quartiles:** Loads full ~5,000 stock universe from Step 1 S3 output. Computes P/E for each using Polygon price ÷ EDGAR EPS. Groups by SEC SIC industry. Calculates 25th percentile (non-tech) or 50th percentile (tech SIC codes 35xx, 36xx, 737x). Persists to DynamoDB.
- **Stage C — Price-dependent filter on the ~500 passers:** Computes their P/E, PEG, P/FCF locally (Polygon price + EDGAR EPS/FCF). Applies: P/E < industry quartile, PEG < 1.0, P/FCF < 20. ~500 → ~30 pass.
- **Stage D — FMP enrichment (only ~30 survivors):** For each:
  - `/stable/profile` → if stock doesn't exist in FMP: exclude (OTC ghost). If market cap < $150M: exclude (no analyst coverage).
  - `/stable/ratios-ttm` → validated P/E, PEG, P/FCF, D/E, ICR, QR, Operating Margin (62 fields)
  - `/stable/financial-growth` → EPS growth, revenue growth, 5Y net income growth
  - `/stable/analyst-estimates` → forward EPS for next 3 years (→ forward P/E + forward LT growth CAGR)
  - `/stable/price-target-summary` → analyst consensus price target
  - `/stable/grades` → individual analyst Buy/Hold/Sell grades → recommendation score (1-5)
  - `/stable/profile` → company description, logo, website, sector, industry
  - 6 API calls per stock with retry logic (3 retries, exponential backoff)
  - Output: ~25-30 stocks with complete enriched data
- **Reasoning:** The price-dependent filter runs BEFORE FMP calls to minimize expensive API usage. 500 stocks × 6 FMP calls = 3,000 calls (too many). 30 stocks × 6 = 180 calls (fine). The local P/E/PEG/P/FCF computation using free Polygon+EDGAR data eliminates 94% of stocks at zero API cost.

**Step 4 — Full Screen + Scoring** (instant)
- Receives ~30 fully enriched stocks from Step 3
- Re-evaluates ALL hard filters using FMP-enriched data (catches values FMP updated that differ from EDGAR)
- Applies soft filters: Forward P/E < 20 (skip if absent, fail if present and bad)
- Computes `fundamental_score` (0-100): for each filter, scores 0-1 based on how far beyond threshold. Average × 100.
- Missing data = FAIL (full screen is strict — stock must prove it qualifies with complete data)
- ~30 → ~20-30 pass with scores
- **Reasoning:** Step 4 exists to compute `fundamental_score` which is 60% of the investability formula. It also catches edge cases where FMP data differs from EDGAR (e.g., FMP reports different revenue growth than EDGAR computed from filings).

**Step 5 — News Fetch** (~4 minutes)
- For each passing stock + GRACE stocks from DynamoDB:
  - FMP `/stable/news/stock` — batched 5 symbols per call, good article summaries
  - TickerTick API — individual calls, 6.5s pacing. Catches SEC filings, short interest reports, analyst downgrades that FMP misses.
  - Merge and deduplicate by title (first 50 chars). FMP articles take priority.
- ~10-15 articles per stock after merge
- **Reasoning:** Dual sources because FMP misses critical articles for smaller stocks (e.g., "Short Interest Up 859%" only appeared on TickerTick for WETH). TickerTick is free but slow (10 req/min). FMP is fast but incomplete for niche sources (sec.gov, tickerreport, gurufocus).

**Step 6 — Sentiment Analysis** (~5 minutes)
- Amazon Bedrock Claude Haiku 4.5 per article
- Returns: relevance, sentiment (-1 to +1), confidence, risk flags, summary
- Risk flags constrained to 8 values (fraud, SEC investigation, accounting, regulatory, lawsuit, revenue risk, management departure, product recall)
- Aggregate per stock: confidence-weighted average sentiment
- **Competition assessment:** One additional Claude call per stock after all articles analyzed. Receives HHI score + article summaries, returns adjusted competition_score (1-5) + reasoning.
- **Reasoning:** AI reads every article and produces structured sentiment. Humans can't read 200 articles daily. Claude identifies specific risk categories that feed into the scoring penalty system. Competition assessment leverages Claude's broad knowledge of market dynamics to adjust the quantitative HHI score.

**Step 7 — Score Calculator** (~1-2 minutes)
- Investability = (0.6 × fundamental_score) + (0.25 × sentiment_normalized) + (0.15 × competition_normalized) + risk_penalties
- Fetches 30-day OHLCV price history from FMP `/stable/historical-price-eod/full` (1s pacing)
- Fetches company descriptions from FMP `/stable/profile` (fallback if not already present)
- Manages risk flag ledger (time-decay for one-time events, persistence for uncertain events)
- Persists ALL to DynamoDB: LATEST, SCORE#date, TRACKING, ARTICLES, PRICE_HISTORY#
- Computes portfolio signals (green/yellow/red) for owned stocks
- **Reasoning:** Single step that combines fundamental + sentiment + competition into one actionable score, persists everything for the dashboard, and maintains the risk lifecycle.

**Step 8 — Alert Checker** (instant)
- Detects: new passers, dropped stocks, sentiment crashes, risk flags, grace expiry
- ACTIVE → GRACE (30 days) → removed (full cleanup)
- Sends email via SNS if thresholds breached
- **Reasoning:** Lifecycle management ensures stale stocks don't persist forever. Alerts notify the user of material changes between dashboard visits.
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
- Investability formula: `(0.6 × fundamental) + (0.25 × sentiment_normalized) + (0.15 × competition_normalized) + risk_penalties`
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
| STOCK#{ticker} | ARTICLES | Analyzed articles with per-article risk flags (overwritten daily) |
| PRICE_HISTORY#{ticker} | DAILY | 30-day OHLCV price bars (overwritten daily) |
| INDUSTRY_AVG#{industry} | METRICS | Industry median benchmarks (overwritten each pipeline run) |

**GSI**: `tracking-status-index` (PK: tracking_status, SK: last_updated, projection: ALL)

**LATEST item fields**: symbol, company_name, company_description, logo, weburl, sector, industry, sic_industry, price, market_cap, investability_score, fundamental_score, sentiment_score, sentiment_confidence, hhi_score, competition_score, competition_reasoning, risk_flags (ledger: list of objects with flag/first_seen/last_seen/days_active), passes_screen, tracking_status, pe_ratio, forward_pe, peg_ratio, price_to_fcf, debt_to_equity, quick_ratio, interest_coverage_ratio, operating_margin, eps_growth_yoy, revenue_growth_yoy, est_lt_growth, analyst_recommendation, analyst_target_price, target_price_upside, institutional_transactions, last_updated

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
| est_lt_revenue_growth | min | 0% | percent_as_decimal | FMP analyst-estimates (forward revenue CAGR). Enrichment-dependent: skipped in Step 2 pre-screen (no FMP data yet), applied strictly in Step 4 (missing = FAIL) |

**Note on revenue growth:** As of the forward-revenue-growth swap, the hard filter is `est_lt_revenue_growth` (forward-looking analyst revenue CAGR), NOT trailing `revenue_growth_yoy`. Trailing `revenue_growth_yoy` is still computed from EDGAR and stored on the stock (used for the operating-margin override "rev growth > 20%" and displayed on the dashboard), but it is no longer a pass/fail filter. Rationale: a value investor cares whether the business is expected to keep growing, not just whether it grew last year. Missing forward estimate = FAIL (can't invest on absent forward data).

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
investability = (0.6 × fundamental_score) + (0.25 × sentiment_normalized) + (0.15 × competition_normalized) + risk_penalties

fundamental_score: 0-100
  Per filter: 0 = at threshold (barely passed), 1.0 = best possible
  For percent_as_decimal filters: value × 100 before comparing to config ranges
  Final = average(per_filter_scores) × 100

sentiment_normalized: 0-100
  = 50 + (raw_sentiment × 50 × confidence)
  Where raw_sentiment is -1 to +1, confidence is 0 to 1
  Neutral (no news or low confidence) = 50

competition_normalized: 0-100
  = (5 - competition_score) × 25
  Where competition_score is 1-5 (1=dominant, 5=fragmented)
  Maps: 1→100, 2→75, 3→50, 4→25, 5→0
  
risk_penalties: applied from risk flag ledger (see below)
Final: clamped [0, 100]

Range verification:
  Max: (0.6 × 100) + (0.25 × 100) + (0.15 × 100) = 100 ✓
  Min: 0 (clamped) ✓
  Neutral midpoint: (0.6 × 50) + (0.25 × 50) + (0.15 × 50) = 50 ✓
```

### Competition Score (Competitive Landscape Rating)

**Two-stage assessment:**
1. **HHI (Herfindahl-Hirschman Index)** — computed in Step 2 from EDGAR revenue data grouped by SEC SIC industry. Measures market concentration. Mapped to 1-5 scale.
2. **Claude adjustment** — per-stock Claude call in Step 6 receives the HHI score + article summaries and adjusts based on actual moat, niche dominance, switching costs, and recent competitive dynamics from news.

**HHI → Score mapping:**
| HHI Range | Score | Meaning |
|-----------|-------|---------|
| ≥ 4000 | 1 | Very concentrated (near monopoly) |
| 2500-3999 | 2 | Concentrated (few players) |
| 1500-2499 | 3 | Moderate concentration |
| 750-1499 | 4 | Competitive |
| < 750 | 5 | Highly competitive (fragmented) |

**Claude adjustment prompt includes:**
- Warning that SEC SIC categories are broad (company may dominate a niche within a broadly classified industry)
- Warning that training data may not reflect recent market entries/exits — consider news
- Request to assess moat, switching costs, brand, network effects, regulatory barriers

**Stored in DynamoDB LATEST:** `hhi_score` (raw), `competition_score` (Claude-adjusted), `competition_reasoning`

**Frontend display:** Shows both scores when they differ (e.g., "3→2"), color-coded green (1) to red (5), tooltip shows reasoning.

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
ICR = Operating Income / Interest Expense (TTM, from EDGAR multi-tag merge)
Interest Expense tags: InterestExpense, InterestAndDebtExpense, InterestPaidNet
(3 reliable tags merged for ~3,400 company coverage vs 829 with single tag)
```
- Rule applied consistently at ALL 3 stages: Step 2 pre-screen, Step 3 enrichment pre-filter, Step 4 full screen
- If D/E > 1.0 (would normally fail), stock passes if ICR > 3.0
- Companies like CRM, META, MSFT now get the override (was broken before due to single-tag coverage)
- AAPL still fails (doesn't report interest expense in any EDGAR frame tag)
- Shown in table: D/E value in amber with "ICR✓" badge
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
- Table shows **daily change** column (last day %, color-coded), NOT 30d total
- Detail panel shows SVG sparkline chart with 30-day trend + state badge + day count
- Price fetching is batched (4 at a time) to avoid API throttling

### GRACE Stock Lifecycle

- **ACTIVE**: passes today's filters. Refreshed daily (news, sentiment, prices, scores).
- **GRACE**: failed today but was previously ACTIVE. Still refreshed daily.
  - Step 5 (news-fetcher) reads GRACE stocks from DynamoDB and includes them alongside passers
  - They flow through Steps 5→6→7→8 getting fresh news, sentiment, scores, price history
  - No stock on the dashboard ever shows stale data
- **After 30 days in GRACE**: stock is completely removed (LATEST, TRACKING, ARTICLES, PRICE_HISTORY all deleted)
- Grace period: 30 days (configurable via GRACE_PERIOD_DAYS or screener-filters.json)

### Analyzed Articles in DynamoDB

- Score calculator persists analyzed articles as `PK: STOCK#{ticker}, SK: ARTICLES`
- Each article stored with: title, url, source, published_at, sentiment, confidence, risk_flags, summary
- API serves these (with per-article flags) instead of live TickerTick when available
- Frontend shows risk flag badges (REV RISK, FRAUD, etc.) next to the specific article that triggered them
- Also shows per-article sentiment score (+42, -65) next to source/time
- Falls back to live TickerTick (no flags) if ARTICLES item doesn't exist yet

### Key Decisions Log

| Decision | Rationale |
|----------|-----------|
| Forward revenue growth filter (swap) | Replaced trailing `revenue_growth_yoy` hard filter with `est_lt_revenue_growth` (forward analyst revenue CAGR). Value investing cares about expected future growth, not just last year. Removed from Step 2 (no FMP data yet), applied strictly in Step 4 (missing = FAIL). Trailing rev growth still computed/stored for op-margin override + display |
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
| P/E is industry-relative (lower quartile) | Computed from full universe (98 industries). Tech SIC (35xx, 36xx, 737x) uses 50th percentile; non-tech uses 25th |
| Soft filters for Finnhub-dependent metrics | forward_pe, est_lt_growth, analyst_recommendation: skip if absent, apply if present |
| Polygon T-2 for price date | Free tier requires completed trading day; always go back 2 days |
| Finnhub peNormalizedAnnual for P/E | epsTTM still includes one-time items; peNormalized strips them. Prevents VISN/RIGL-type artifacts |
| Revenue TTM: require all 4 quarters or use derivation | Partial sums (3 of 4 quarters) produce wrong growth; must be all-or-nothing |
| P/E quartile sanity: exclude P/E<1, EPS>revenue/share | One-time gains create impossible P/E values that pollute industry distributions |
| Finnhub peNormalizedAnnual for P/E | epsTTM still includes one-time items; peNormalized strips them |
| Forward EPS growth override | Trailing negative + Finnhub forward positive = pass (matches Finviz) |
| Interest expense multi-tag (3 tags) | InterestExpense + InterestAndDebtExpense + InterestPaidNet → ~3,400 coverage |
| D/E override consistent at all 3 stages | Pre-screen, enrichment pre-filter, and full screen all use same rule |
| GRACE stocks refreshed daily | News-fetcher reads GRACE from DynamoDB, includes in pipeline. No stale data. |
| Grace period 30 days (was 90) | Full cleanup on expiry (LATEST + TRACKING + ARTICLES + PRICE_HISTORY deleted) |
| Articles stored in DynamoDB | Per-article risk flags visible in UI. Replaced live TickerTick (which has no flags) |
| Score calculator timeout 600s | 19 stocks × 12s/stock for prices + descriptions = needs 5+ minutes |
| Step 1 timeout 10 minutes | Multi-tag revenue/interest TTM needs ~60 EDGAR API calls |
| P/E slider removed from UI | Now industry-relative; no fixed threshold to adjust |
| Daily change in table (not 30d) | 30d already in detail panel sparkline; daily is more actionable |
| yfinance blocked from Lambda | Yahoo blocks AWS data center IPs. Can't use from Lambda |
| finvizfinance blocked from Lambda | 403 Forbidden from AWS IPs |
| Competition score: HHI + Claude hybrid | HHI from EDGAR revenue is quantitative but SEC SIC too broad. Claude adjusts with moat/niche knowledge. Stores both for transparency |
| Competition weight 15% of investability | Enough to differentiate but doesn't dominate. Fundamentals (60%) remain primary signal |
| revenue_risk prompt tightened | Only flags concrete forward threats (guidance cuts, contract loss). Past declines or normalizations excluded |
| EDGAR multi-tag merge (capex, OCF, opIncome, equity, revenue) | Single-tag coverage was 50-76%. Multi-tag adds fallbacks for companies using alternative XBRL taxonomy |
| Monthly tag discovery Lambda | Queries EDGAR companyfacts for companies with missing data, discovers alternative tags. EventBridge 1st of month |
| Companyfacts backfill for non-calendar FY | ~1,800 stocks with missing FCF get backfilled from individual companyfacts. Annual+YTD derivation handles fiscal years ending any month |
| Foreign filer reference file (monthly) | 20-F/6-K filers (ATAT, TKC, etc.) fetched from companyfacts, stored as S3 reference. Step 3 loads in milliseconds vs 800 API calls/run |
| IFRS namespace support | Foreign filer generator checks ifrs-full when us-gaap has no data. Tag mappings: ProfitLoss, Revenue, CashFlowsFromUsedInOperatingActivities, etc. |
| 6-month data recency filter | Stocks with latest filing >180 days old are excluded. Can't invest on stale data. Applied across all stocks (GAAP and IFRS) |
| P/E exemption when PEG < 1.0 | If PEG proves growth justifies valuation, P/E industry check is bypassed. Prevents blocking high-growth value stocks (e.g., WAY PEG=0.096) |
| Polygon T-1 with T-2 fallback | Changed from hardcoded T-2 to T-1 first (yesterday's prices). Falls back to T-2 if Polygon returns error. Pipeline runs at 8PM UTC, well past 5AM publish time |
| TickerTick only for under-covered stocks | Skip TickerTick for stocks with 4+ FMP articles. Saves ~60% of 6.5s-paced API calls |
| News-fetcher includes all tracked stocks | ACTIVE + GRACE stocks from DynamoDB included in news fetch, not just today's passers. No tracked stock goes stale |
| FMP ICR=0 treated as null | FMP returns interestCoverageRatioTTM=0 for debt-free companies. We now skip 0 values to avoid overwriting valid null with misleading zero |
| Hollow growth exclusion | Exclude stocks where Forward P/E < Trailing P/E AND LT Growth ≤ 0. Near-term earnings improving but long-term outlook negative = value trap (cost-cutting, not real growth) |

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
| Competition score (HHI + Claude hybrid) | COMPLETE |
| EDGAR multi-tag coverage (capex, OCF, opIncome, equity, revenue) | COMPLETE |
| Monthly tag discovery Lambda | COMPLETE |
| Industry P/E median (50th pctile for all) | COMPLETE |
| Companyfacts backfill (non-calendar FY stocks) | COMPLETE |
| Foreign filer reference file (20-F/6-K + IFRS) | COMPLETE |
| 6-month data recency filter | COMPLETE |
| P/E exemption when PEG < 1.0 | COMPLETE |
| Polygon T-1 pricing (fresher data) | COMPLETE |
| Column sorting (click any header) | COMPLETE |
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
