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
14. **Tests Are Mandatory (NON-NEGOTIABLE)** — Every change or addition MUST be covered by automated tests. Before writing code, identify the blast radius (which steps/functions/DynamoDB items/UI fields the change touches) and ensure tests exist or are added for each affected area. Rules:
    - **Unit tests** for every Lambda's core logic (screening filters, scoring math, date/trading-day resolution, tracking-field preservation, sentiment/competition parsing, market-gate decisions).
    - **Integration tests** for cross-step invariants: TRACKING item survives Step 7 → Step 8 rewrites (mark_price, mark_date, first_tracked must persist); enrichment resolves a real trading day (never a holiday); a full-pipeline dry run over fixture data lands the expected DynamoDB shape.
    - **Regression tests** — every production bug gets a test that reproduces it BEFORE the fix, so it can never silently return (e.g. holiday→0 prices→all GRACE, mark wipe, Days reset).
    - Run the full suite (`pytest`) and confirm green BEFORE every deploy. A deploy without passing tests is not allowed.
    - No "wipe and rerun to see what happens" as a substitute for tests. We must understand impact ahead of time, not discover it in production.
    - When a change breaks a test, fix the code (or the test if the contract legitimately changed) — never delete a test to make the suite pass.
15. **Be Concise (NON-NEGOTIABLE)** — Say something once. No restating the same point in different words, no recap sections, no re-explaining what was already said. Lead with the answer. Trust the user to ask follow-ups. Verbosity and repetition waste tokens and the user's time. User is in Pacific time.
16. **Verify Before Explaining (NON-NEGOTIABLE)** — Never assert a cause, number, or behavior you have not checked against the actual data (DynamoDB items, S3 outputs, price bars, logs, code). Read the record first, then explain. If you have not verified something, say so plainly ("I haven't checked X") instead of inventing a plausible-sounding reason. A fabricated explanation that happens to sound reasonable is a lie and is worse than admitting uncertainty. When the user reports a discrepancy, reproduce it from the stored data before offering any theory.

## Current Architecture (Deployed & Working)

### 8-Step Pipeline

```
EventBridge Scheduler (Mon-Fri 5PM America/New_York, DST-aware)
    → Step Functions (stock-screener-pipeline)
        → Step 0: Market Gate (is US market open today? skip weekends/holidays → Choice, instant)
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

**Step 0 — Market Gate** (instant)
- Lambda `stock-screener-market-gate`. First state in the machine (`StartAt: MarketGateCheck`).
- Determines if the US equity market is OPEN today:
  - Weekend check (Sat/Sun always closed)
  - Polygon `/v1/marketstatus/upcoming` holiday calendar — if today is listed as `closed` for NYSE/NASDAQ, the market is closed. Data-driven, self-maintaining (no hardcoded holiday list).
- Returns `{"market_open": bool, "reason", "today"}`. A Step Functions `Choice` (`MarketOpen?`) routes: closed → `MarketClosed` (Succeed, pipeline skipped, zero cost); open → full pipeline.
- Fail-open: if the Polygon call errors, it proceeds (never skips a real trading day over a transient API error). Weekend check runs regardless.
- No time-of-day awareness: the 5 PM ET schedule is what guarantees the close has passed. Step 7 additionally refuses to store non-final bars (see "Price canonicalisation and bar finality").
- **Reasoning:** The schedule fires Mon-Fri but has no holiday awareness. On holidays (e.g. Labor Day) prices don't advance, so a run adds no new market data yet still incurs Bedrock/API cost. This gate stops the no-op spend. Consistent with "be frugal" + "data-driven, no hardcoded lists".

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
- **Stage B — Industry P/E quartiles:** Loads full ~5,000 stock universe from Step 1 S3 output. Computes P/E for each using Polygon price ÷ EDGAR EPS. Groups by 4-digit SEC SIC. Calculates median (50th) + 25th percentile per industry (min 5 valid-P/E peers). **3-digit fallback:** if a 4-digit SIC has <5 valid-P/E peers, uses its 3-digit SIC group median instead (stops at 3 digits — never 2-digit). Persists pe_median/pe_lower_quartile/pe_source (4-digit|3-digit) to DynamoDB. `compute_industry_pe_thresholds` is unit-tested.
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
- Computes `fundamental_score` (0-100): for each filter, scores 0-1 based on how far beyond threshold. Average × 100.
- Missing data = FAIL (full screen is strict — stock must prove it qualifies with complete data)
- ~30 → ~20-30 pass with scores
- **Reasoning:** Step 4 exists to compute `fundamental_score` which is 60% of the investability formula. It also catches edge cases where FMP data differs from EDGAR (e.g., FMP reports different revenue growth than EDGAR computed from filings).

**Step 5 — News Fetch** (~7-9 minutes worst case; was ~2 min)
- For each passing stock + ACTIVE/GRACE stocks from DynamoDB:
  - FMP `/stable/news/stock` — batched 5 symbols per call, good article summaries
  - TickerTick API (`tt:{sym}&n=50`) — paced 6.5s start-to-start. Called when ANY of: fewer than 4 FMP articles; an earnings event in the last 7 days (`news_select.detect_earnings_event`: earnings/results/guidance/transcript titles, scheduling and preview titles vetoed, no new API call); `LATEST.news_price_divergence` is set
  - TickerTick results go through a company filter (`is_company_article`: core company name or an explicit ticker reference such as `(NYSE:HLIO)`), because the feed tags every story mentioning "Helios" with HLIO. Stories older than 14 days, or with descriptions under 40 chars, are dropped (event detection runs before the length drop)
  - Dedupe (`shared/news_text.dedupe_articles`): exact normalized title across FMP and TickerTick; the longest description wins (a transcript arrives as 9,999/200/75 chars)
  - Per stock at most 12 articles (`cap_articles`): earnings/analyst titles first, long texts (>= 2,000 chars) before short snippets, then newest
- Call budget (`plan_tickertick_calls`): at most 45 calls per run. Order: divergence, then earnings events newest first, then under-covered stocks rotated by day number. The phase stops at 420 s or when the Lambda has under 150 s left. Worst case with every call timing out is about 465 s of 900 s, so no timeout change
- Output per stock gains `earnings_event` ({event_date, event_key, evidence_titles}) and `news_price_divergence` (copied from LATEST) when present
- **Reasoning:** FMP misses critical articles (short-interest reports for WETH; for HLIO, the Q4 guide below consensus and the price-target cuts only appeared on TickerTick). n=6 never reached the earnings items (the newest six were wrong-company noise), so n=50 with a company filter. The budget keeps the free-tier 10 req/min limit and the Lambda timeout safe at peak earnings season.

**Step 6 — Sentiment Analysis** (~5 minutes)
- Amazon Bedrock Claude Haiku 4.5 per article
- Returns: relevance, sentiment (-1 to +1), confidence, risk flags, summary
- Risk flags constrained to 8 values (fraud, SEC investigation, accounting, regulatory, lawsuit, revenue risk, management departure, product recall)
- Prompt: earnings releases, transcripts and analyst rating/price-target notes are always relevant (short text = low confidence, not irrelevant). Negative anchors: guidance below consensus/lowered, margin compression, price target cut, downgrade, pull-forward. Company wires are promotional; consensus is judged ONLY if the text states it
- Aggregate per stock (`calculate_aggregate_sentiment`): weight = confidence × recency × source. Recency half-life 4 days (floor 0.05, undated = 7 days old). Company wires (Business Wire, GlobeNewswire, PR Newswire, Accesswire) ×0.5 unless the article carries a risk flag. Same-event articles count once (`lambdas/shared/news_text.py`: exact normalized title at any date, or token Jaccard ≥ 0.75 within 2 days; higher confidence wins). Each article gets `analysis.counted`/`duplicate_of`. `sentiment_score = Σ(s·w)/Σw`, `confidence = Σ(w·c)/Σw`; also returns `scored_count` (counted relevant articles) and `article_count` (all analyzed)
- **Earnings guidance extraction** (`sentiment-analyzer/guidance.py`): for stocks with `earnings_event` (from Step 5), ONE Bedrock call per event over the best long text (the longest earnings-titled description, up to 10,000 chars, so the transcript or full release) plus up to 8 analyst/guidance snippets cut to 500 chars, capped at 16,000 chars (about 4-5k tokens), `max_tokens` 700, temperature 0. Strict JSON: `guidance_vs_prior` (raised|maintained|narrowed|lowered|not_stated), `guidance_vs_consensus` (above|in_line|below|not_stated), `margin_outlook` and `demand_outlook` (up|flat|down|not_stated), `one_time_or_pull_forward_flag`, `analyst_actions` (max 8), `key_quotes_or_numbers` (max 3), `evidence_fields_present`, `verdict`, `confidence`. The parser coerces any component not listed in `evidence_fields_present` to `not_stated`, so the model cannot invent consensus; unparseable replies return None (no write, retried next run). Cap 12 calls per run (peak seen: 9 new events in one day), divergence-flagged stocks first, then newest event; the rest are deferred and retried (still inside the 7-day window). Idempotent by `event_key` (`SYMBOL:event_date`, stable via the stored `prior_guidance`); one re-extraction when the input grew by 2,000+ chars (the transcript usually lands a day after the release). Cost cap about $0.10/day (Bedrock price assumed, not verified)
- **Competition assessment:** One additional Claude call per stock after all articles analyzed. Receives HHI score + article summaries, returns adjusted competition_score (1-5) + reasoning.
- **Reasoning:** AI reads every article and produces structured sentiment. Humans can't read 200 articles daily. Claude identifies specific risk categories that feed into the scoring penalty system. Competition assessment leverages Claude's broad knowledge of market dynamics to adjust the quantitative HHI score.

**Step 7 — Score Calculator** (~1-2 minutes)
- Investability = (0.6 × fundamental_score) + (0.25 × sentiment_normalized) + (0.15 × competition_normalized) + risk_penalties
- Fetches 30-day OHLCV price history from FMP `/stable/historical-price-eod/full` (1s pacing)
- Fetches company descriptions from FMP `/stable/profile` (fallback if not already present)
- Manages risk flag ledger (time-decay for one-time events, persistence for uncertain events)
- Persists ALL to DynamoDB: LATEST, SCORE#date, TRACKING, ARTICLES, PRICE_HISTORY#
- ARTICLES stores a raw 240-char `snippet` for unscored articles (the model summary of an unscored article can mislead, e.g. "scheduling announcement" on a transcript); the UI shows it instead
- ARTICLES stores the 10 best articles (`select_articles_to_store`: scored first, newest first, then the rest; output newest first) with `scored` and `duplicate_of`. The stock score uses ALL articles (computed in Step 6)
- **Guidance penalty** (`guidance_penalty.py`): `penalty = -min(15, points) × confidence × decay` only when `guidance_signal.verdict == "negative"`. Points: guidance lowered vs prior 6, below consensus 5, margin down 4, demand down 3, pull-forward/one-time 2. `decay = max(0, 1 - days since event_date / 30)`. Negative only (no bonus), a separate term so the positive recaps of the same release cannot dilute it. HLIO example (5+4+2 = 11 points, confidence 0.8): -8.8 on day 0, -4.4 on day 15, 0 from day 30. `LATEST` is a whole-item put, so `_carry_forward_guidance` copies the stored `guidance_signal` forward while it is inside the 30-day window. `guidance_signal` is written to `LATEST` every run and to `SCORE#{date}` only on the extraction day. Advisory fields `score_breakdown.guidance_penalty`/`guidance_detail` go to the S3 output only
- **News-vs-price divergence** (`price_signals.news_price_divergence`, advisory, never gates or scores): reads the stored final bars after the backfill. Flags when `sentiment_score > 0.3` and a 3-session return ≤ -10% (strong) or ≤ -7% with volume ≥ 2× the prior-20-bar mean (mild), looking back over the last 5 rolling windows (HLIO stayed lit 8 sessions). Written to `LATEST.news_price_divergence` only when flagged, so absence clears it
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
| TickerTick | News articles (earnings/divergence/under-covered stocks, max 45 calls/run) | 10 req/min | ACTIVE |
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
| EventBridge Scheduler | stock-screener-daily-trigger (cron(0 17 ? * MON-FRI *), America/New_York; replaced the UTC-only Events rule, which ran at 3 PM EST in winter) |
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
| STOCK#{ticker} | SCORE#{date} | Historical score snapshot (one per day). Also `guidance_signal` (only on the extraction day), `price_as_of`, `estimates` (analyst-estimate snapshot map), `estimates_as_of` (New York date of the FMP fetch) |
| STOCK#{ticker} | TRACKING | Tracking status (ACTIVE/GRACE/MANUAL). Holds first_tracked, last_passed, grace_start, and for manual marks mark_price+mark_date. NOTE: BOTH Step 7 (score-calculator) and Step 8 (alert-checker) rewrite this item every run and MUST preserve first_tracked, mark_price/mark_date, AND grace_start — else "Since Mark" resets or GRACE stocks never expire. grace_start = day it entered GRACE; the 30-day removal clock counts from it (cleared on passing, stamped once on failing) |
| STOCK#{ticker} | ARTICLES | Up to 10 analyzed articles (newest first, scored preferred) with per-article risk flags, `scored`, `duplicate_of` (overwritten daily) |
| STOCK#{ticker} | TRACK_HIST#{unmark_ts} | Closed manual-tracking stint: mark_date, mark_price, unmark_date, unmark_price, change_pct. record_type=TRACK_HIST. Persisted on unmark, never overwritten |
| PRICE_HISTORY#{ticker} | DAILY | 30-day OHLCV FINAL bars only (overwritten when refetched). Also `last_final_date`, `fetched_at` (ISO UTC), `to_date` (= newest bar date). `last_backfill` is no longer written |
| INDUSTRY_AVG#{industry} | METRICS | Industry median benchmarks (overwritten each pipeline run) |

**GSI**: `tracking-status-index` (PK: tracking_status, SK: last_updated, projection: ALL)

**LATEST item fields**: symbol, company_name, company_description, logo, weburl, sector, industry, sic_industry, price, market_cap, investability_score, fundamental_score, sentiment_score, sentiment_confidence, sentiment_scored_count, sentiment_article_count, guidance_signal (map: the guidance_* components, verdict, confidence, analyst_actions, key_quotes_or_numbers, evidence_fields_present, event_key/event_date, extracted_at/extracted_on, extraction_count, input_chars, input_titles, model; about 1.5-2.5 KB; kept 30 days after the event), news_price_divergence (only when flagged: flag/tier/return_3d/return_1d/volume_ratio/sentiment/as_of/trigger_date; returns are fractions), hhi_score, competition_score, competition_reasoning, risk_flags (ledger: list of objects with flag/first_seen/last_seen/days_active), passes_screen, tracking_status, pe_ratio, forward_pe, peg_ratio, price_to_fcf, debt_to_equity, quick_ratio, interest_coverage_ratio, operating_margin, eps_growth_yoy, revenue_growth_yoy, est_lt_growth, analyst_recommendation, analyst_target_price, target_price_upside, institutional_transactions, last_updated

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | /stocks | All tracked stocks with latest scores (sorted by investability) |
| GET | /stocks/{ticker} | Full stock detail (profile + fundamentals + sentiment) |
| GET | /stocks/{ticker}/history | Score history time series (for trend charts) |
| GET | /stocks/{ticker}/prices | 30-day OHLCV price bars (for sparkline + trend) |
| GET | /stocks/{ticker}/news | Live news from TickerTick |
| POST | /stocks/{ticker}/track | Mark a stock to track. Snapshots current closing price (mark_price) + mark_date |
| DELETE | /stocks/{ticker}/track | Unmark a stock. Persists a TRACK_HIST# record with % change (mark → unmark) and returns it |
| GET | /track-history | All closed manual-tracking stints with their % change (newest first) |
| GET | /industries | Industry median benchmarks (from SEC SIC data) |
| GET | /pipeline/status | Pipeline summary (active/grace counts) |

### Frontend (React + TypeScript, Vite 8)

Located at `frontend/`. Deployed to Amplify: https://main.d2ned6rk557ndc.amplifyapp.com
Deploy script: `./scripts/deploy_frontend.sh`

Layout: Filters (collapsible top bar) → 2-column (table + detail panel)

Components:
- `App.tsx` — Layout, filter sliders at top, fetches trends for all stocks on load
- `StockTable.tsx` — All metrics + 30d trend column + risk badges (Risk column holds only risk-flag badges) + ICR override badge + a separate `Signals` column right after Risk (sortable by active-signal count; one left-aligned row, DIV then G▼, so every badge starts at the column's left edge; `SignalsCell.tsx`, `buildSignalSlots` in `utils/signals.ts`) holding the `DIV` badge (price-vs-news divergence, red strong / amber mild, tooltip "Price fell X% in 3 days while news is positive: check why") + `G▼` marker (negative earnings guidance, tooltip lists the components). One row per stock. Extra columns: "Since Mark" (live % since manual mark), "Track" (☆/★ mark toggle), "Trade" (Buy / ✕ remove). NEW stocks (first tracked today) float to top.
- `TrackHistory.tsx` — Closed manual-tracking stints, grouped by stock. Top row per stock (stint count + avg % change), expandable to a nested table of individual mark→unmark stints. Hidden when no history exists.
- `StockDetail.tsx` — Two tabs:
  - Overview: Company profile, score cards (sentiment card shows "scored n of m articles" and confidence), sparkline chart, divergence banner, Guidance block (components, analyst actions, quotes, event date, age), risk flags (with ledger metadata), news (unscored articles greyed with a "not scored" tag and the raw snippet instead of the model summary, per-article confidence, grey 0 badge for a genuinely scored 0)
  - Metrics Guide: Industry comparison (real medians from SEC SIC data), scoring methodology, metric definitions with interpretation
- `ScoreHistoryChart.tsx` — Score history as one responsive SVG (viewBox 340x80, bar width = W/n, 1px gap, no min-width, so it cannot overflow). Range buttons 30D/90D/1Y/All by calendar span from the newest snapshot (a button that repeats a shorter range is hidden); windows over 120 points show the last snapshot per ISO week. Heading `Score History (N snapshots, since DATE)`; date labels at first/middle/last; `<title>` tooltip per bar. Logic in `utils/history.ts`
- `FilterSliders.tsx` — Client-side re-filtering with D/E override note ("or ICR > 3.0x")
- `MetricsGuide.tsx` — Industry averages (fetched from API), metric definitions, scoring formulas explained
- `utils/signals.ts` — divergence message, guidance lines/tooltip, `isArticleScored` (legacy items without `scored` fall back to `relevant`)
- `utils/trends.ts` — calculateTrend: changePercent, consecutiveDown/Up, isFalling/isStabilizing/isRecovering

### Screening Filters Config

Source of truth: `shared/config/screener-filters.json`

**Hard Filters (8 total, ~100% EDGAR+Polygon coverage):**
| Filter | Type | Threshold | Data Format | Source |
|--------|------|---------|-------------|--------|
| pe_ratio | max | Industry lower quartile (25th pctile) | ratio | Local (Polygon ÷ EDGAR TTM EPS) |
| peg_ratio | max | 1.0 | ratio | FORWARD PEG = forward_pe ÷ (est_lt_growth × 100). Both sides forward-looking. None (fails) if no positive forward P/E or forward growth |
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
investability = (0.6 × fundamental_score) + (0.25 × sentiment_normalized) + (0.15 × competition_normalized) + risk_penalties + guidance_penalty + rev_accel_bonus

guidance_penalty: 0 to -15, see Step 7 (negative earnings-guidance verdict only, × confidence × 30-day decay). Never positive.

rev_accel_bonus: +5 flat when revenue_accel_underpriced flag is set (fwd rev growth > trailing AND P/S < industry median). Clamped into [0,100].

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
- **AI disruption threat** — how easily the company's core offerings can be replicated/replaced by others using AI. Easily-copied software/content/routine digital services push the score toward commoditization (higher = more competitive); proprietary data, regulatory barriers, physical assets, network effects, and high switching costs are treated as AI-resilient. Instructed to weigh both erosion and resilience, not blindly penalize AI exposure. Returns an `ai_threat` field (low/moderate/high) and must explain in reasoning how AI threat factored in.
- Warning that SEC SIC categories are broad (company may dominate a niche within a broadly classified industry)
- Warning that training data may not reflect recent market entries/exits — consider news
- Request to assess moat, switching costs, brand, network effects, regulatory barriers

**Stored in DynamoDB LATEST:** `hhi_score` (raw), `competition_score` (Claude-adjusted), `competition_reasoning`, `ai_threat` (low/moderate/high)

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

- Score calculator fetches 30-day OHLCV bars from FMP `/stable/historical-price-eod/full`
- Stored as `PRICE_HISTORY#{ticker}` in DynamoDB (one item per stock, ~19 trading bars)
- Skips the refetch only when the stored series is final and was fetched at/after 17:00 ET on the session date (`should_skip_backfill`); legacy items are refetched
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

### Price canonicalisation and bar finality

- `lambdas/shared/market_time.py` (vendored byte-identical into `score-calculator/`) is real America/New_York time, pure stdlib. `et_date.eastern_date` is PACIFIC despite its name; it is unchanged and still keys `SCORE#`, `first_tracked`, etc. At the 5 PM ET run time both give the same calendar date.
- A bar dated today is FINAL at/after 16:15 ET on a weekday (`BAR_FINAL_AFTER`). Step 7 stores only final bars, so no consumer needs to know about "provisional" bars. Holidays are handled as data (FMP returns no bar for them); no holiday list.
- `LATEST.price` = close of the newest final bar, `price_as_of` = that bar's date, `market_cap = shares_implied * price` when `shares_implied` is known. `shares_implied = profile.marketCap / profile.price` is set in enrichment only when both are positive (no Polygon fallback). Step 7 is the single writer of `LATEST.price`; `_refresh_latest_valuation` no longer writes it.
- Stocks never FMP-enriched since rollout keep their legacy `market_cap` beside the new `price` (residual gap, not audited).
- T-1 note: screening ratios (P/E, forward P/E, P/FCF, PEG, target upside) use the Polygon grouped-daily close for the last trading day (T-1), so after a 5 PM ET run they can be one session older than the displayed `price`. Pre-existing; not changed here.
- Estimate snapshots (`lambdas/shared/estimates.py`, vendored to `enrichment/` and `score-calculator/`): built from data enrichment already fetched (zero new FMP calls), stored in `SCORE#{date}` and `LATEST`. `eps_revision_pct` matches fiscal periods by period end. Not yet wired into any UI or alert.
- `scripts/repair_provisional_bars.py` repairs bars stored before the rollout (dry run by default; `--apply` only after 17:00 ET, conditional writes).

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
| Manual price-tracking on the track mechanism | Reused MANUAL track (not the portfolio buy/sell) for "mark to watch price change." Mark snapshots the pipeline closing price; unmark persists a TRACK_HIST# record + returns % change. Persisted history (not display-once) so the user can review how each idea performed. Closing price (Polygon T-1), consistent with the rest of the dashboard |
| NEW stocks sort to top | Table comparator floats first-tracked-today stocks above all others regardless of sort column; Days column shows "NEW" instead of "<1" |
| News ordered newest-first | Right panel sorts articles by published_at desc; undated fall to bottom |
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
| P/E quartile 3-digit SIC fallback | Thin 4-digit SIC industries (<5 valid-P/E peers, e.g. QCOM's 3663) fall back to the 3-digit group median (366). Stops at 3 digits — 2-digit is too broad to be a real peer set. Blank if 3-digit still thin. pe_source field records which level was used |
| Revenue-acceleration-underpriced flag (▲REV) | Advisory badge (display-only, never gates). Flags when forward revenue growth > trailing revenue growth AND P/S < industry-median P/S — accelerating top line not yet priced in. Uses P/S not EV/Sales because EV components (debt/cash) aren't available universe-wide; the debt blind-spot is bounded by the D/E<1 filter. P/S computed in the same universe loop as P/E (price ÷ revenue_per_share); industry-median P/S reuses the SIC + 3-digit-fallback machinery. Earnings-free by design (revenue less distorted by one-time items). Fields: price_to_sales, revenue_accel_underpriced |
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
| Business dates keyed on US Eastern, not UTC | first_tracked, SCORE#{date}, mark_date, the Days column, and the market-gate holiday/weekend check are all US market (ET) calendar dates. Keying on UTC collapsed two same-UTC-day runs into one SCORE# snapshot and made same-day "NEW" stocks read "1" after UTC midnight. Shared dependency-free helper `et_date.py` (pure-stdlib US DST rules, no tzdata needed) vendored into each Lambda; frontend uses Intl `America/New_York`. Instants/timestamps (last_updated ISO) and S3 raw/ partitions stay UTC. Covered by tests/test_et_date.py (incl. DST boundaries) |
| EventBridge Scheduler at 5 PM America/New_York | Events rules are UTC-only: `cron(0 20 ...)` was 4 PM EDT but 3 PM EST, so winter runs fired before the close and stored a partial bar (HLIO 2026-11-09). Scheduler takes an IANA timezone, so the time holds across DST; bounded retry (2 attempts / 1h) so a failed start cannot run hours late |
| Only FINAL bars are stored (exclude, not flag) | Stored series is always complete, so no consumer needs a provisional state. `last_final_date` + `fetched_at` replace `last_backfill`; a series fetched before 17:00 ET on its session date is refetched rather than frozen |
| Canonical price = newest final bar | `price`, `price_as_of`, `market_cap` set by Step 7 only; `shares_implied` carries the share count from enrichment |
| Missed-run alarm + no-wipe on failed FMP fetch | Scheduler target has an SQS DLQ (`stock-screener-schedule-dlq`) and a CloudWatch alarm `stock-screener-schedule-failed` to the alerts topic, so a run that never starts is not silent. A failed/empty FMP profile or estimates fetch never erases stored `shares_implied`/`estimates` (enrichment skips the write; Step 7 carries them forward from LATEST); `shares_implied` is removed only when a profile was returned but unusable |
| Estimate snapshot in SCORE#/LATEST | Daily analyst-estimate map (EPS FY1/FY2 + period ends, revenue FY1, target price + count) so revisions can be measured later. Zero extra FMP calls |
| Market gate (Step 0) skips weekends/holidays | EventBridge Mon-Fri has no holiday awareness; holiday runs re-process stale prices at full Bedrock/API cost. Step 0 Lambda checks weekend + Polygon holiday calendar (data-driven, self-maintaining), Choice state skips to Succeed when closed. Fail-open on API error |
| first_tracked preserved in alert-checker too | Same class of bug as the mark wipe: Step 8 rebuilds TRACKING every run and reset first_tracked to today when the in-memory `prev` was partial (Days column reset to 1). Fix reads the durable stored first_tracked and never regresses it. Also fixed the portfolio-protected GRACE path which wrote no first_tracked at all |
| Grace clock anchored on durable grace_start | ROOT-CAUSE BUG: score-calculator wrote last_passed=None and no grace_start for GRACE stocks every run, so the 30-day removal clock had no anchor → GRACE stocks never expired (immortal). Fix: stamp grace_start when a stock enters GRACE, preserve it across Step 7 AND Step 8 rewrites, clear it on passing. Expiry + the UI "days left" both count from grace_start. GRACE pill turns red when ≤5 days left. Backfilled existing GRACE stocks (lost real drop date → fresh 30-day clock). Covered by tests/test_grace_anchor.py |
| Manual mark preserved in BOTH Step 7 and Step 8 | The TRACKING item is rewritten every run by score-calculator (Step 7) AND alert-checker (Step 8, even for still-ACTIVE stocks). Both must carry mark_price/mark_date forward or the mark is wiped overnight. First fix only covered Step 7; Step 8 was silently clobbering it. Verified end-to-end that the mark survives the full pipeline |
| AI disruption threat in competition score | Competition prompt now explicitly weighs how easily offerings can be replicated/replaced by AI. Easily-copied digital offerings → commoditized (higher score); proprietary data / regulatory / physical / network-effect moats → AI-resilient. Adds `ai_threat` field. Verified with contrasting test cases (generic content SaaS → high/5; regulated utility → low/2) |
| revenue_risk prompt tightened | Only flags concrete forward threats (guidance cuts, contract loss). Past declines or normalizations excluded |
| EDGAR multi-tag merge (capex, OCF, opIncome, equity, revenue) | Single-tag coverage was 50-76%. Multi-tag adds fallbacks for companies using alternative XBRL taxonomy |
| Monthly tag discovery Lambda | Queries EDGAR companyfacts for companies with missing data, discovers alternative tags. EventBridge 1st of month |
| Companyfacts backfill for non-calendar FY | ~1,800 stocks with missing FCF get backfilled from individual companyfacts. Annual+YTD derivation handles fiscal years ending any month |
| Foreign filer reference file (monthly) | 20-F/6-K filers (ATAT, TKC, etc.) fetched from companyfacts, stored as S3 reference. Step 3 loads in milliseconds vs 800 API calls/run |
| IFRS namespace support | Foreign filer generator checks ifrs-full when us-gaap has no data. Tag mappings: ProfitLoss, Revenue, CashFlowsFromUsedInOperatingActivities, etc. |
| 6-month data recency filter | Stocks with latest filing >180 days old are excluded. Can't invest on stale data. Applied across all stocks (GAAP and IFRS) |
| P/E exemption when PEG < 1.0 | If PEG proves growth justifies valuation, P/E industry check is bypassed. Prevents blocking high-growth value stocks (e.g., WAY PEG=0.096) |
| Forward PEG (both numerator + denominator) | PEG = forward_pe ÷ (forward est_lt_growth × 100). Replaced trailing PEG (P/E ÷ trailing eps_growth_yoy), which for low-base/recovery stocks produced a huge growth ratio that crushed PEG to ~0.00 (e.g. RAMP). Trailing PEG kept ONLY as a private pre-filter key (_prefilter_peg) to gate FMP calls cheaply before forward data exists; the displayed/filtered PEG is forward. No positive forward growth → PEG=None → fails screen (missing=FAIL). compute_forward_peg() covered by tests/test_forward_peg.py |
| Polygon T-1 with T-2 fallback | Changed from hardcoded T-2 to T-1 first (the last completed trading day for screening ratios). Falls back to T-2 if Polygon returns error. The pipeline runs at 5 PM ET, well past the publish time |
| TickerTick after earnings or divergence (replaces under-covered-only rule) | HLIO fell 26% after earnings while TickerTick, the only source with the guide and target cuts, was skipped (FMP returned 4+ articles) and n=6 would not have reached them. Now: <4 FMP articles OR earnings event in 7 days OR divergence flag, n=50, company filter (39 of 100 HLIO stories kept), cap 45 calls/run, 420 s phase budget, 150 s Lambda-time guard, 12 articles/stock. No timeout bump: worst case 465 s of 900 s |
| News-fetcher includes all tracked stocks | ACTIVE + GRACE stocks from DynamoDB included in news fetch, not just today's passers. No tracked stock goes stale |
| FMP ICR=0 treated as null | FMP returns interestCoverageRatioTTM=0 for debt-free companies. We now skip 0 values to avoid overwriting valid null with misleading zero |
| News-vs-price divergence flag | HLIO fell 30.7% in 3 sessions after earnings while stock sentiment was +0.72. Advisory flag from stored final bars (zero API cost): sentiment > 0.3 (0.3 flags 7 of 81 tracked stocks today; 0.2 flags 10) and 3-day return ≤ -10% (5.5% of 2,497 stored windows) or ≤ -7% with volume ≥ 2× (2.9%). Lookback 5 windows so a weekend cannot clear it. Never gates the screen or changes the score. Computed in Step 7 from `PRICE_HISTORY#` read back after the backfill |
| Sentiment recency half-life 4 days | Matches the 5-day one-time risk-flag decay; median article (4 days old) weighs 0.5, p90 (15 days) 0.07. The old docstring claimed recency weighting that did not exist |
| Same-event articles count once | HLIO had the same "Q3 Earnings Call Highlights" recap twice (MarketBeat, Defense World). Aggregation dedupes; risk flags are still collected from every relevant article so a duplicate cannot hide one |
| Company wires weigh 0.5 | Business Wire/GlobeNewswire/PR Newswire/Accesswire are issuer-written. Exempt when the article carries a risk flag (adverse disclosures are not promotional) |
| Guidance extraction + separate penalty | HLIO's guide below consensus, margin down and pull-forward appeared only in long texts the per-article scorer never read, and 3 of 4 relevant HLIO articles were positive recaps, so any sentiment adjustment would be diluted. One capped Bedrock call per earnings event over the transcript/release returns an evidence-gated signal; a negative verdict subtracts up to 15 points × confidence × 30-day decay. Cap 12 calls/run, idempotent by event key, one re-read when the text grows 2,000+ chars. Asymmetric by design: no bonus for good guidance. Components the text does not state are `not_stated`, so the model cannot invent consensus |
| Unscored articles keep a raw snippet | The model summary of an irrelevant/duplicate article can describe the wrong thing, so Step 7 stores a 240-char raw snippet and the UI labels the article "not scored" instead of showing the summary |
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

### Testing (see Design Principle #14 + tests/README.md)

- Suite lives in `tests/` (pytest). Dev deps in `requirements-dev.txt`
  (`pytest`, `moto`, `requests-mock`) — install once with
  `.venv/bin/python3 -m pip install -r requirements-dev.txt`.
- Run: `./scripts/run_tests.sh` (pytest, then `npx jest` for the CDK assertions, then Vitest for the frontend: `utils/history.ts` + `ScoreHistoryChart` render safety) / `-m unit|integration|regression` (pytest args only).
- MUST be green before every deploy. Ad-hoc `scripts/test_*.py` (live-API
  probes) are NOT the suite and are excluded from pytest collection.
- Mocking: `moto` for DynamoDB (single-table + tracking-status-index GSI),
  `requests-mock` for Polygon/FMP, `monkeypatch` to freeze time.
- Coverage today: enrichment trading-day (holiday regression), TRACKING field
  preservation across Step 7/8 (mark + first_tracked regressions), market-gate
  decisions, screening filter logic, bar finality / price canonicalisation (`test_market_time.py`, `test_bar_backfill.py`), estimate snapshots (`test_estimates.py`), the repair script, divergence/aggregation/news selection, guidance extraction + penalty + persistence + API pass-through (`test_guidance*.py`), and a Jest test asserting the Scheduler schedule. Every new change adds/extends tests for its
  blast radius; every fixed bug gets a regression test that fails on old code.
- A `PostFileSave` hook (`.kiro/hooks/test-on-handler-save.json`) runs the suite
  automatically when any `lambdas/**/handler.py` is saved.
- **Data-integrity audit** (`scripts/audit_data_integrity.py`): checks every
  tracked stock in the LIVE table against documented invariants (hard filters +
  overrides, sanity ranges, PEG↔forward-PE consistency, market-cap floor, data
  recency, orphan items). Run manually (`--json` for machine output) or via the
  `audit-after-task` hook. Logic is unit-tested in `tests/test_data_integrity.py`.
  Scope limit: validates data IN DynamoDB against the rules — it cannot catch bad
  data from sources (FMP/EDGAR) or logic with no checkable DB trace.
