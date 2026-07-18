# Stock Screener

A value stock screening application that combines fundamental financial analysis with news sentiment scoring to produce an "Investability Score" for each stock. Built on AWS serverless, runs daily at market close.

## What It Does

1. **Scans the entire US stock market** — SEC EDGAR provides financials for ~5,097 companies in ~10 API calls
2. **Two-pass filtering** — Pre-screen (EDGAR metrics) narrows to ~69, then full screen (with Finnhub enrichment) to ~6 final picks
3. **Fetches news** — Recent articles from ~10,000 sources per passing stock (TickerTick)
4. **Analyzes sentiment** — Claude AI (Bedrock Haiku 4.5) scores each article and detects risk flags
5. **Calculates investability** — Combines fundamental quality (70%) + sentiment (30%) into a 0-100 score
6. **Shows company profiles** — Business descriptions from Polygon, logos/industry from Finnhub
7. **Alerts you** — Email notification when stocks breach thresholds or new value picks appear

## Architecture

AWS serverless — runs daily Mon-Fri at market close (4 PM ET / 8 PM UTC), costs under $5/month.

```
EventBridge (Mon-Fri 4PM ET) → Step Functions (8-step pipeline)
  Step 1: EDGAR Bulk Fundamentals (~10 API calls → 5,097 companies)
  Step 2: Pre-Screen (D/E, QR, OpMargin, EPS Growth, Rev Growth → ~69 pass)
  Step 3: Polygon Bulk Prices (1 call) + Finnhub Metrics (3 calls/stock for ~69 candidates)
  Step 4: Full Screen (all 12 filters, missing data = FAIL → ~6 pass)
  Step 5: News Fetch (TickerTick, 10 articles/stock)
  Step 6: Sentiment Analysis (Bedrock Claude Haiku 4.5)
  Step 7: Score Calculator (investability score + Polygon company descriptions + DynamoDB)
  Step 8: Alert Checker (threshold monitoring + tracking lifecycle + SNS)

API Gateway (REST) → Lambda → DynamoDB → React Dashboard
```

## Live Demo

- **API**: https://kw8mlahpj2.execute-api.us-east-2.amazonaws.com/prod/
- **Frontend**: Run locally with `cd frontend && npm run dev`

## Quick Start

### Prerequisites
- AWS CLI configured (`aws configure --profile stock-screener`)
- Node.js 20+, Python 3.12+, Docker Desktop
- CDK CLI (`npm install -g aws-cdk`)

### Deploy
```bash
npm install
npx cdk deploy --profile stock-screener
```

### Run Pipeline Manually
```bash
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:us-east-2:116488731375:stateMachine:stock-screener-pipeline \
  --input '{}' \
  --profile stock-screener --region us-east-2
```

### Run Frontend Locally
```bash
cd frontend
npm install
npm run dev
```

## API Endpoints

Base URL: `https://kw8mlahpj2.execute-api.us-east-2.amazonaws.com/prod`

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | /stocks | All tracked stocks with scores and metrics |
| GET | /stocks/{ticker} | Full stock detail (profile, fundamentals, sentiment) |
| GET | /stocks/{ticker}/history | Score history time series |
| GET | /stocks/{ticker}/news | Live news articles (from TickerTick) |
| POST | /stocks/{ticker}/track | Manually track a stock |
| DELETE | /stocks/{ticker}/track | Stop tracking |
| GET | /pipeline/status | Pipeline summary (active/grace counts) |

## Screening Criteria

Based on value investing principles. All configurable via `shared/config/screener-filters.json`.

| Filter | Type | Threshold | Source |
|--------|------|-----------|--------|
| P/E Ratio | max | 50 | Polygon Price ÷ EDGAR EPS |
| Forward P/E | max | 20 | Finnhub |
| PEG Ratio | max | 1.0 | P/E ÷ EDGAR EPS Growth |
| Price/FCF | max | 20 | Polygon Price ÷ EDGAR FCF |
| Debt/Equity | max | 1.0 | EDGAR |
| Quick Ratio | min | 1.0 | EDGAR |
| Operating Margin | min | 0% | EDGAR |
| EPS Growth YoY | min | 0% | EDGAR |
| Revenue Growth YoY | min | 0% | EDGAR |
| Est. LT Growth | min | 0% | Finnhub |
| Analyst Recommendation | max | 3.0 | Finnhub (1=Strong Buy, 5=Strong Sell) |
| Sentiment Score | min | -0.3 | Bedrock Claude |

**Deferred** (no reliable free source): `target_price_upside`, `institutional_transactions`

## Data Sources

| Source | What | Cost | Rate Limit |
|--------|------|------|------------|
| SEC EDGAR | Financial statements (~5,097 companies) | Free | Unlimited |
| Polygon.io | Stock prices (grouped daily) + company descriptions | Free | 5 req/min |
| Finnhub | Forward P/E, LT Growth, Analyst Recs, Logo, Industry | Free | 60 req/min |
| TickerTick | News articles per ticker | Free | 10 req/min |
| Bedrock Claude Haiku 4.5 | Sentiment analysis | ~$3.60/month | Pay per token |

## Project Structure

```
stock-screener/
├── lib/stock-screener-stack.ts        CDK infrastructure (all AWS resources)
├── lambdas/
│   ├── fundamentals-fetcher/          Step 1: SEC EDGAR bulk fundamentals
│   │   └── providers/
│   │       ├── base.py                DataProvider ABC + StockFundamentals
│   │       ├── edgar_provider.py      ACTIVE: EDGAR Frames API
│   │       └── fmp_provider.py        INACTIVE: retained for paid upgrade
│   ├── stock-screener/                Steps 2 & 4: Value filter (pre-screen + full screen)
│   ├── enrichment/                    Step 3: Polygon prices + Finnhub metrics + profiles
│   ├── news-fetcher/                  Step 5: TickerTick news articles
│   ├── sentiment-analyzer/            Step 6: Bedrock/Claude sentiment
│   ├── score-calculator/              Step 7: Investability score + Polygon descriptions + DynamoDB
│   ├── alert-checker/                 Step 8: Threshold monitoring + SNS alerts
│   └── api/                           REST API for dashboard
├── frontend/                          React + TypeScript dashboard (Vite 8)
│   └── src/components/
│       ├── StockTable.tsx             Metrics table with color-coded pass/fail
│       └── StockDetail.tsx            Company profile + scores + news
├── shared/config/
│   └── screener-filters.json          Filter thresholds (source of truth)
├── scripts/                           One-time utility scripts
└── .kiro/steering/architecture.md     Architecture & decisions doc
```

## GitHub

https://github.com/bahrigokhanyilmaz/StockScreener
