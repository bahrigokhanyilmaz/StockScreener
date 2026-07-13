# Stock Screener — Architecture & Decisions

## Overview

A value stock screening application that combines fundamental financial analysis
with news sentiment scoring to produce an "Investability Score" for each stock.
Built on AWS, designed to be modular, scalable, and cost-effective.

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

## Current Architecture (Deployed & Working)

### Data Flow
```
EventBridge (Mon-Fri 4PM ET)
    → Step Functions (stock-screener-pipeline, 8 steps)
        → Step 1: EDGAR Bulk Fundamentals (~10 API calls for ~5,097 companies)
        → Step 2: Pre-Screen (EDGAR-only filters: D/E, QR, OpMargin → ~233 pass)
        → Step 3: Price Enrichment (Twelve Data for ALL ~233 passers)
        → Step 4: Full Screen (all filters incl. P/E, Price/FCF)
        → Step 5: News Fetch (TickerTick — articles per final passer)
        → Step 6: Sentiment Analysis (Bedrock Claude Haiku 4.5)
        → Step 7: Score Calculator (investability + DynamoDB write)
        → Step 8: Alert Checker (thresholds + tracking lifecycle → SNS)

API Gateway (REST)
    → API Lambda → DynamoDB → JSON response to React frontend
```

### Tech Stack

| Layer | Technology | Notes |
|-------|-----------|-------|
| Infrastructure | AWS CDK v2 (TypeScript) | `cdk deploy` from laptop |
| Compute | AWS Lambda (Python 3.12, ARM64) | 8 functions total |
| Orchestration | Step Functions + EventBridge | Daily Mon-Fri 4PM ET |
| Storage | S3 (raw data lake) + DynamoDB (live data) | Single-table design |
| Fundamentals | SEC EDGAR Frames API | Free, unlimited, ~10 requests for all US stocks |
| Price/Valuation | Twelve Data | Free, 800 req/day, 8 req/min |
| News | TickerTick API | Free, no key, 10 req/min |
| Sentiment | Amazon Bedrock (Claude Haiku 4.5) | ~$3.60/month |
| Alerts | Amazon SNS | Email to bahrigokhanyilmaz@gmail.com |
| API | API Gateway (REST) | CORS enabled |
| Frontend | React (not yet built) | Will use AWS Amplify |
| Secrets | SSM Parameter Store (SecureString) | 2 keys stored |

### AWS Resources (Deployed)

| Resource | Name/ID |
|----------|---------|
| Stack | StockScreenerStack |
| S3 Bucket | stock-screener-raw-data-116488731375 |
| DynamoDB Table | stock-screener-data |
| DynamoDB GSI | tracking-status-index |
| Step Functions | stock-screener-pipeline |
| EventBridge Rule | stock-screener-daily-trigger |
| SNS Topic | stock-screener-alerts |
| API Gateway | https://kw8mlahpj2.execute-api.us-east-2.amazonaws.com/prod/ |
| Lambda (Step 1) | stock-screener-fundamentals-fetcher |
| Lambda (Step 2) | stock-screener-filter |
| Lambda (Step 3) | stock-screener-news-fetcher |
| Lambda (Step 4) | stock-screener-sentiment-analyzer |
| Lambda (Step 5) | stock-screener-score-calculator |
| Lambda (Step 6) | stock-screener-alert-checker |
| Lambda (API) | stock-screener-api |
| SSM | /stock-screener/fmp-api-key (inactive, retained) |
| SSM | /stock-screener/alpha-vantage-api-key |
| SSM | /stock-screener/twelve-data-api-key |
| AWS Account | 116488731375, us-east-2 |
| AWS Profile | stock-screener |

### API Keys Reference (stored in SSM Parameter Store — SecureString)

| Service | SSM Path | Free Tier Limits | Status |
|---------|----------|-----------------|--------|
| FMP | /stock-screener/fmp-api-key | 500MB/30 days (exhausted) | INACTIVE |
| Alpha Vantage | /stock-screener/alpha-vantage-api-key | 25 req/day, 1 req/sec | ACTIVE |
| Twelve Data | /stock-screener/twelve-data-api-key | 800 req/day, 8 req/min | ACTIVE (price data) |

Note: Actual key values are NEVER in code or docs. They're in SSM only.
To retrieve a key value: `aws ssm get-parameter --name "/stock-screener/<key-name>" --with-decryption --profile stock-screener --region us-east-2`

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | /stocks | List all tracked stocks with latest scores |
| GET | /stocks/{ticker} | Single stock detail (fundamentals + sentiment) |
| GET | /stocks/{ticker}/history | Score history time series (for charts) |
| POST | /stocks/{ticker}/track | Manually track a stock |
| DELETE | /stocks/{ticker}/track | Stop tracking a stock |
| GET | /pipeline/status | Pipeline summary (active/grace counts) |

### DynamoDB Schema (Single-Table)

| PK | SK | Purpose |
|----|----|---------| 
| STOCK#{ticker} | LATEST | Current scores + fundamentals (overwritten daily) |
| STOCK#{ticker} | SCORE#{date} | Historical score (one per day, never overwritten) |
| STOCK#{ticker} | TRACKING | Tracking status (ACTIVE/GRACE/MANUAL) |
| PIPELINE#{date} | RESULT | Daily pipeline run summary (future) |
| ALERT_RULE#{id} | CONFIG | User alert rules (future) |
| PRESET#{name} | CONFIG | Saved filter presets (future) |

**GSI**: `tracking-status-index` (PK: tracking_status, SK: last_updated)

### Two-Tier Data Architecture

**Tier 1: Discovery** (daily, broad)
- EDGAR Frames API: bulk financials for ~6,000 companies (~10 requests)
- Alpha Vantage: enriches top ~25 passing stocks with price metrics
- ALL data stored in S3 (even non-passing — for slider exploration + retroactive)

**Tier 2: Tracking** (daily, focused)
- Passing stocks get: news + sentiment + investability score
- Results persisted to DynamoDB
- Grace period: 90 days after dropping off screen
- Status: ACTIVE (green) | GRACE (yellow) | MANUAL (blue)

### Investability Score Formula

```
investability = (0.7 × fundamental_score) + (0.3 × sentiment_adjustment) + risk_penalties
- fundamental_score: 0-100 (how well stock passes value filters)
- sentiment_adjustment: -25 to +25 (sentiment × 25 × confidence)
- risk_penalties: -10 to -35 per flag (SEC investigation, fraud, etc.)
- Final: clamped 0-100
```

### Screening Criteria

Config: `shared/config/screener-filters.json`

| Filter | Type | Default | Data Format |
|--------|------|---------|-------------|
| P/E Ratio | max | 50 | ratio |
| Forward P/E | max | 20 | ratio |
| PEG Ratio | max | 1.0 | ratio |
| Price/FCF | max | 20 | ratio |
| Debt/Equity | max | 1.0 | ratio |
| Quick Ratio | min | 1.0 | ratio |
| Operating Margin | min | 0% | percent_as_decimal |
| EPS Growth YoY | min | 0% | percent_as_decimal |
| Revenue Growth YoY | min | 0% | percent_as_decimal |
| Target Price Upside | min | 20% | percent_as_decimal |
| Sentiment Score | min | -0.3 | ratio |

### Data Sources

| Need | Source | Limit | Cost |
|------|--------|-------|------|
| Fundamentals | SEC EDGAR Frames API | Unlimited | Free |
| Price/Valuation | Alpha Vantage OVERVIEW | 25/day | Free |
| News | TickerTick API | 10/min | Free |
| Sentiment | Bedrock Claude Haiku 4.5 | Pay per token | ~$3.60/mo |
| Universe | EDGAR (companies filing 10-K) | Unlimited | Free |
| Alerts | SNS email | Unlimited | Free tier |

**Key Lessons Learned:**
- yfinance does NOT work from Lambda (Yahoo blocks AWS IPs)
- FMP free tier has 500MB/30-day bandwidth (exhausted during development)
- EDGAR Frames API is the best free source (bulk data, no limits)
- Alpha Vantage needs 1 req/sec spacing (free tier)
- Bedrock requires inference profile IDs (not raw model IDs)

### Project Structure

```
stock-screener/
├── bin/stock-screener.ts              → CDK entry point
├── lib/stock-screener-stack.ts        → Full infrastructure definition
├── lambdas/
│   ├── fundamentals-fetcher/          → EDGAR + Alpha Vantage
│   │   ├── handler.py
│   │   ├── requirements.txt
│   │   └── providers/
│   │       ├── __init__.py            → Factory + registry
│   │       ├── base.py               → DataProvider ABC + StockFundamentals
│   │       ├── edgar_provider.py      → ACTIVE: EDGAR + Alpha Vantage hybrid
│   │       └── fmp_provider.py        → INACTIVE: retained for future paid upgrade
│   ├── stock-screener/                → Value filter logic
│   │   ├── handler.py
│   │   └── screener-filters.json
│   ├── news-fetcher/                  → TickerTick integration
│   ├── sentiment-analyzer/            → Bedrock/Claude
│   ├── score-calculator/              → Investability score + DynamoDB write
│   ├── alert-checker/                 → Tracking lifecycle + SNS alerts
│   └── api/                           → REST API handler (all routes)
├── frontend/                          → React app (Phase 4 — next)
├── shared/config/
│   └── screener-filters.json          → Source of truth for filter thresholds
├── .venv/                             → Local Python virtualenv (gitignored)
├── cdk.json                           → CDK config (profile: stock-screener)
└── .kiro/steering/architecture.md     → This file
```

### Build Progress

| Phase | Status | What's Done |
|-------|--------|-------------|
| 1. Fundamentals pipeline | COMPLETE | EDGAR bulk + Alpha Vantage enrichment |
| 2. News + Sentiment | COMPLETE | TickerTick + Bedrock/Claude |
| 3. Scoring + Alerts + Tracking | COMPLETE | Investability score + DynamoDB + SNS |
| 4. API Gateway | COMPLETE | REST endpoints, all verified |
| 5. React dashboard | NEXT | Sliders, tables, charts |
| 6. Retroactive analysis | FUTURE | Athena + historical trends |

### Conventions

- Lambdas: Python 3.12 ARM64, handler.py + requirements.txt per folder
- Dependencies: Docker-bundled via PythonFunction (or plain Function if no deps)
- Infrastructure: TypeScript CDK, single stack
- Config: JSON in shared/config/ — single source of truth
- Naming: kebab-case folders, snake_case Python, camelCase TypeScript
- Secrets: SSM Parameter Store (SecureString), never in code
- Pin dependency versions
- Test locally with `.venv/bin/python3`, deploy only when verified
- Remove dead code immediately
