# Stock Screener — Architecture & Decisions

## Overview

A value stock screening application that combines fundamental financial analysis
with news sentiment scoring to produce an "Investability Score" for each stock.
Built on AWS, designed to be modular, scalable, and cost-effective.

The user is a long-term value investor learning to build this from scratch.
Always explain concepts, teach the stack, and keep the user engaged.

## Core Features

1. **Fundamental Screening** — Filter stocks by value KPIs (P/E, PEG, FCF, debt ratios, etc.)
2. **Sentiment Analysis** — Score news articles to detect negative/positive developments
3. **Investability Score** — Combined metric: fundamental quality + sentiment health
4. **Dynamic KPI Sliders** — React frontend with real-time adjustable thresholds
5. **Threshold Alerts** — Email/SMS when stocks breach user-configured rules
6. **Retroactive Analysis** — Historical data accumulation for trend analysis
7. **Presets** — Save/load filter configurations ("Aggressive Value", "Conservative", etc.)

## Design Principles (NON-NEGOTIABLE)

### 1. No Shortcuts
- Never take shortcuts that create long-term problems
- Every decision should be forward-looking and scalable
- If a "quick fix" would limit future evolution, choose the proper approach even if slower

### 2. Modularity & Swappability
- Use abstraction layers (interfaces) so components can be swapped without rewrites
- Data providers, sentiment engines, storage backends — all behind interfaces
- Switching a component should be a config change, not a code change

### 3. Provider Abstraction Pattern
- Financial data sourcing uses the Strategy Pattern
- Abstract `DataProvider` interface defines the contract
- Implementations are interchangeable via `PROVIDER` environment variable

### 4. Free First, Upgrade Later
- Start with free/open tools for proof of concept
- Design the architecture so paid upgrades are drop-in replacements
- Never let the "free" choice dictate the architecture

### 5. Clean As You Go
- Remove obsolete artifacts immediately when approach changes
- No dead code, unused files, or leftover experiments in the repo

### 6. Security By Default
- NEVER hardcode secrets in source code
- Use AWS SSM Parameter Store (SecureString) for all secrets
- Lambda reads parameter NAME from env vars, fetches decrypted value at runtime

### 7. Teach While Building
- This is a learning project — explain every concept as it's introduced
- Keep the user engaged and informed at every step

### 8. Production-Grade Tooling
- Docker for dependency bundling (PythonFunction in CDK)
- ARM64 Lambda architecture (cheaper, faster, native to dev machine)
- Infrastructure as Code (CDK) — never create resources manually in console

### 9. Always Reassess
- Do not assume that because we started with a tool/API/approach, we must continue
- At every stage, evaluate whether a better option exists
- Pivoting to redo something properly is always acceptable

### 10. Test Locally First
- Test Lambda logic locally before deploying (fast feedback loop)
- Deploy only when local tests pass
- Virtual env at `.venv/` for local Python development

## Tech Stack

### Infrastructure (CDK - TypeScript)
- **AWS CDK v2** for Infrastructure as Code
- **AWS Account**: 116488731375, region us-east-2
- **AWS Profile**: `stock-screener`
- **Deployment**: `cdk deploy` (profile set in cdk.json)
- **Docker**: Required for PythonFunction Lambda bundling

### Orchestration
- **AWS Step Functions** — State machine: `stock-screener-pipeline`
- **Amazon EventBridge** — Cron: Mon-Fri 8PM UTC (4PM ET market close)

### Backend (Python 3.12, ARM64)
- **6 Lambda Functions** chained via Step Functions
- **PythonFunction** CDK construct — Docker-based dependency bundling
- **Amazon Bedrock** — Claude Haiku 4.5 for sentiment analysis
- **Amazon S3** — Raw data lake (fundamentals + news + sentiment)
- **Amazon SNS** — Alert notifications (email)
- **AWS SSM Parameter Store** — Secure secret storage

### Data Provider Layer
- **Abstract Interface**: `DataProvider` in `providers/base.py`
- **Active Provider**: `FMPProvider` (set via `PROVIDER=fmp` env var)
- **Universe Source**: NASDAQ official traded symbols file (free, ~5,200 stocks)
- **Fundamentals**: FMP `/stable/ratios-ttm`, `/stable/profile`, `/stable/financial-growth`, `/stable/price-target-consensus`
- **Market Cap Filter**: $300M minimum (configurable via `MIN_MARKET_CAP` env var)
- **Symbol Normalization**: GOOG→GOOGL alias mapping
- **Premium Detection**: Gracefully skips FMP-blocked symbols (PG, HD, CRM on free tier)

### News Source
- **TickerTick API** — Free, no API key, covers all US stocks
- Rate limit: 10 req/min (6.5s delay between requests)
- Returns articles from ~10,000 sources (Reuters, WSJ, SEC filings, etc.)

### Sentiment Engine
- **Amazon Bedrock** — Model: `us.anthropic.claude-haiku-4-5-20251001-v1:0`
- Inference profile required (not raw model ID — Bedrock changed this)
- Cost: ~$3.60/month at expected volume
- Returns: sentiment (-1 to +1), confidence, relevance, risk_flags, summary

### Frontend (Phase 4 — not yet built)
- **React** (TypeScript) — Dashboard with slider panel
- **AWS Amplify** — Hosting

## Two-Tier Data Architecture

### Tier 1: Discovery (broad scan, daily)
- Fetches NASDAQ ticker list → filters to ~1,000 stocks (market cap ≥ $300M)
- Pulls fundamentals from FMP for all ~1,000
- Stores ALL data in S3 (even non-passing stocks — for slider exploration + retroactive analysis)
- Applies value filters → ~30-50 pass

### Tier 2: Tracking (focused, deep)
- Passing stocks get: news fetched + sentiment analyzed + investability scored
- **Grace period: 90 days** — stock stays tracked after dropping off screen
- Tracking status: Active (green) | Grace (yellow) | Manual (blue)
- After 90 days without re-qualifying and no manual pin: tracking stops

### How Sliders Work
- Sliders re-filter from the most recent daily scan — instant, no new API calls
- Sliders are for EXPLORATION (what would pass if I changed criteria?)
- Saved default filters determine what gets AUTO-TRACKED
- User can manually "Track" any stock from exploration mode

## Pipeline Steps (Step Functions State Machine)

| Step | Lambda | Purpose | Timeout |
|------|--------|---------|---------|
| 1 | `stock-screener-fundamentals-fetcher` | NASDAQ universe + FMP data → S3 | 5 min |
| 2 | `stock-screener-filter` | Apply value filters, score fundamentals | 60s |
| 3 | `stock-screener-news-fetcher` | TickerTick news for passing stocks → S3 | 10 min |
| 4 | `stock-screener-sentiment-analyzer` | Bedrock/Claude sentiment per article | 10 min |
| 5 | `stock-screener-score-calculator` | Combine fundamental + sentiment → investability | 30s |
| 6 | `stock-screener-alert-checker` | Check thresholds → SNS notifications | 30s |

Pipeline total timeout: 30 minutes.

## Investability Score Formula

```
investability = (0.7 × fundamental_score) + (0.3 × sentiment_adjustment) + risk_penalties

Where:
- fundamental_score: 0-100 (how well stock passes value filters)
- sentiment_adjustment: -25 to +25 (sentiment × 25 × confidence)
- risk_penalties: -10 to -35 per flag (SEC investigation, fraud, etc.)
- Final score clamped to 0-100
```

Categories: Highly Investable (≥70) | Moderate (40-70) | Low (<40)

## Screening Criteria (from Finviz filters)

Config file: `shared/config/screener-filters.json`

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
| Est. LT Growth | min | 0% | percent_as_decimal |
| Institutional Transactions | min | 0% | percent_as_decimal |
| Target Price Upside | min | 20% | percent_as_decimal |
| Sentiment Score | min | -0.3 | ratio |

Note: `percent_as_decimal` means data is stored as 0.22 (= 22%) but slider shows 22.
The screener divides threshold by 100 before comparing.

## Data Sources — Current State

| Need | Source | Status | Notes |
|------|--------|--------|-------|
| Stock universe | NASDAQ traded symbols file | Working | Free, ~5,200 stocks |
| Fundamentals | FMP free tier (per-symbol endpoints) | Working | ~1,000 stocks/day within bandwidth |
| EPS/Revenue growth | FMP `/stable/financial-growth` | Working | Free tier |
| Analyst targets | FMP `/stable/price-target-consensus` | Working | Free tier |
| News articles | TickerTick API | Working | Free, no key, 10 req/min |
| Sentiment | Amazon Bedrock (Claude Haiku 4.5) | Working | ~$3.60/month |
| Alerts | Amazon SNS (email) | Deployed | Email: bahrigokhanyilmaz@gmail.com |

### FMP Free Tier Limitations
- Some popular symbols blocked (PG, HD, CRM return "Premium" error)
- Stock screener endpoint (`/stable/stock-screener`) not available
- Bandwidth: 500MB/30 days (current usage: ~220MB/month for daily scans)
- Legacy model IDs deprecated (Claude 3 Haiku → use inference profile IDs)

### Key Lesson: yfinance Does NOT Work From Lambda
Yahoo Finance actively blocks AWS IP ranges. Discovered during build.
FMP + NASDAQ ticker file is the reliable alternative.

## FMP Bandwidth Budget (Free: 500MB/30 days)
- Daily scan: ~1,000 stocks × 7.7KB (profile+ratios+growth+target) = 7.7 MB/day
- 30 days × 7.7 MB = ~231 MB/month
- Weekly universe refresh: ~5,200 profiles × 2.7KB = 14 MB/week → 56 MB/month
- **Total: ~287 MB/month (within 500 MB limit)**

## AWS Resources (Deployed)

| Resource | Name/ARN |
|----------|----------|
| S3 Bucket | `stock-screener-raw-data-116488731375` |
| Lambda (Step 1) | `stock-screener-fundamentals-fetcher` |
| Lambda (Step 2) | `stock-screener-filter` |
| Lambda (Step 3) | `stock-screener-news-fetcher` |
| Lambda (Step 4) | `stock-screener-sentiment-analyzer` |
| Lambda (Step 5) | `stock-screener-score-calculator` |
| Lambda (Step 6) | `stock-screener-alert-checker` |
| Step Functions | `stock-screener-pipeline` |
| SNS Topic | `stock-screener-alerts` |
| EventBridge Rule | `stock-screener-daily-trigger` (Mon-Fri 8PM UTC) |
| SSM Parameter | `/stock-screener/fmp-api-key` (SecureString) |
| CDK Stack | `StockScreenerStack` |

## Project Structure

```
stock-screener/
├── bin/                           → CDK app entry point
├── lib/
│   └── stock-screener-stack.ts    → Full CDK stack (all 6 Lambdas + Step Functions + SNS)
├── lambdas/
│   ├── fundamentals-fetcher/
│   │   ├── handler.py             → Provider-agnostic data fetcher
│   │   ├── requirements.txt       → requests, boto3
│   │   └── providers/
│   │       ├── __init__.py        → Factory + registry
│   │       ├── base.py            → DataProvider ABC + StockFundamentals schema
│   │       └── fmp_provider.py    → FMP free tier (NASDAQ universe + per-symbol data)
│   ├── stock-screener/
│   │   ├── handler.py             → Value filter logic + scoring
│   │   ├── screener-filters.json  → Bundled config (copy of shared/config)
│   │   └── requirements.txt
│   ├── news-fetcher/
│   │   ├── handler.py             → TickerTick API integration
│   │   └── requirements.txt       → requests, boto3
│   ├── sentiment-analyzer/
│   │   ├── handler.py             → Bedrock/Claude per-article analysis
│   │   └── requirements.txt       → boto3
│   ├── score-calculator/
│   │   ├── handler.py             → Combine fundamental + sentiment scores
│   │   └── requirements.txt
│   └── alert-checker/
│       ├── handler.py             → Threshold checks + SNS notifications
│       └── requirements.txt       → boto3
├── frontend/                      → React app (Phase 4 — not started)
├── shared/config/
│   └── screener-filters.json      → Source of truth for filter thresholds
├── .venv/                         → Local Python virtual env (gitignored)
└── .kiro/steering/
    └── architecture.md            → This file
```

## Build Phases — Progress

| Phase | Status | What's Done |
|-------|--------|-------------|
| 1. Fundamentals pipeline | COMPLETE | Fetch + screen + store in S3 |
| 2. News + Sentiment | COMPLETE | TickerTick + Bedrock/Claude + scoring + alerts |
| 3. Step Functions orchestration | COMPLETE | Full 6-step pipeline + EventBridge daily trigger |
| 4. React dashboard | NOT STARTED | Sliders, tables, charts, alert config |
| 5. Retroactive analysis | NOT STARTED | Athena + S3, historical trends, DynamoDB tracking |

## What's Next

1. **DynamoDB tables** — For tracked stocks list, score history, alert rules, user presets
2. **API Gateway** — REST API for the React frontend to consume
3. **React dashboard** — Sliders, stock table, trend charts
4. **Full pipeline test** — FMP was rate-limited during testing; verify on next run
5. **SNS confirmation** — User needs to confirm email subscription for alerts

## Conventions

- Lambdas: Python 3.12 ARM64, each in its own folder with handler.py + requirements.txt
- Dependencies: Docker-bundled via PythonFunction CDK construct (or plain lambda.Function for no-dependency Lambdas)
- Infrastructure: TypeScript CDK, single stack
- Config: JSON files in shared/config/ — single source of truth for frontend + backend
- Naming: kebab-case for folders, snake_case for Python, camelCase for TypeScript
- Secrets: Always in SSM Parameter Store (SecureString), never in code
- Pin dependency versions for reproducibility
- Remove dead code and obsolete files immediately
- Test locally first, deploy only when verified
