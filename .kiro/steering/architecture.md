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

These principles govern ALL technical decisions in this project:

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
- Implementations (yfinance, FMP, Polygon) are interchangeable
- Active provider is selected via environment variable, not hardcoded

### 4. Free First, Upgrade Later
- Start with free/open tools for proof of concept
- Design the architecture so paid upgrades are drop-in replacements
- Never let the "free" choice dictate the architecture
- Current: yfinance (free) → Future: FMP or Polygon (paid, stable API)

### 5. Clean As You Go
- Remove obsolete artifacts immediately when approach changes
- No dead code, unused files, or leftover experiments in the repo
- Every file should serve a current purpose

### 6. Security By Default
- NEVER hardcode secrets (API keys, credentials) in source code
- Use AWS SSM Parameter Store (SecureString) for all secrets
- Lambda reads parameter NAME from env vars, fetches decrypted value at runtime
- Secrets never appear in CloudFormation, env vars visible in console, or logs

### 7. Teach While Building
- This is a learning project — explain every concept as it's introduced
- Keep the user engaged and informed at every step
- Comment code thoroughly with WHY, not just WHAT

### 8. Production-Grade Tooling
- Docker for dependency bundling (PythonFunction in CDK)
- ARM64 Lambda architecture (cheaper, faster, native to dev machine)
- Infrastructure as Code (CDK) — never create resources manually in console
- Version-controlled everything

## Tech Stack

### Infrastructure (CDK - TypeScript)
- **AWS CDK v2** for Infrastructure as Code
- **AWS Account**: 116488731375, region us-east-2
- **AWS Profile**: `stock-screener`
- **Deployment**: `cdk deploy --profile stock-screener`
- **Docker**: Required for PythonFunction Lambda bundling

### Orchestration
- **AWS Step Functions** — Visual, stateful workflow orchestrating 6 Lambda steps
- **Amazon EventBridge** — Cron trigger (daily at market close)

### Backend (Python)
- **AWS Lambda** (Python 3.12, ARM64) — 6 functions in the pipeline
- **PythonFunction** CDK construct — Docker-based dependency bundling
- **Amazon Bedrock (Claude)** — Sentiment analysis via LLM
- **Amazon DynamoDB** — Scores, history, alert rules, user presets
- **Amazon S3** — Raw data lake (fundamentals + articles) for archival/Athena
- **AWS SSM Parameter Store** — Secure secret storage

### Data Provider Layer
- **Abstract Interface**: `DataProvider` in `providers/base.py`
- **Active Provider**: Set via `PROVIDER` env var in CDK
- **Current**: `FMPProvider` (free tier — per-symbol endpoints + NASDAQ universe)
- **Future**: Paid FMP tier (batch endpoints, screener) or Polygon
- **Pattern**: Strategy Pattern with factory function `get_provider()`
- **Universe Source**: NASDAQ official traded symbols file (free, comprehensive, ~5,200 stocks)
- **Market Cap Filter**: $300M minimum (configurable via `MIN_MARKET_CAP` env var)

### API Layer
- **Amazon API Gateway** (REST) — Serves data to frontend
- Endpoints: /stocks, /stocks/{ticker}/history, /stocks/{ticker}/news, /alerts/rules, /presets

### Frontend
- **React** (with TypeScript) — Dashboard with slider panel
- **AWS Amplify** — Hosting
- Features: sortable stock table, KPI sliders, trend charts, alert configuration

### Alerts
- **Amazon SNS** — Email/SMS notifications when thresholds are breached

## Two-Tier Data Architecture

### Tier 1: Discovery (broad scan, periodic)
- Runs daily at market close
- Scans ~1,000 liquid US stocks (S&P 500 + Nasdaq 100 + Russell mid-cap)
- Fetches fundamentals for all via provider (yfinance)
- Stores ALL scanned data in S3 (raw data lake)
- Applies default value filters to identify passing stocks (~30-50)

### Tier 2: Tracking (focused, deep, ongoing)
- Stocks that pass the screen are added to the **Tracked Stocks** list (DynamoDB)
- Tracked stocks get daily: fundamental updates, news fetches, sentiment scores
- **Grace period: 90 days** — a stock stays tracked for 90 days after it stops passing
- This provides longitudinal data for retroactive analysis
- User can also manually track/untrack stocks

### Tracking Status
- **Active** (passes filters now) — green in UI
- **Grace period** (recently dropped off) — yellow in UI
- **Manually tracked** (user chose to watch) — blue in UI
- After 90 days without re-qualifying (and no manual pin): tracking stops

### How Sliders Work
- Sliders re-filter from the most recent daily scan — instant, no new API calls
- Sliders in the UI are for EXPLORATION (what would pass if I changed criteria?)
- The saved default filters determine what gets AUTO-TRACKED
- User can "Track" any stock they see in exploration mode

### Growth Pattern
```
Week 1:  30 pass → 30 tracked
Week 2:  +7 new, -2 drop → 35 pass, 37 tracked (2 in grace)
Week 3:  +3 new, -1 drop → 37 pass, 40 tracked (3 in grace)
...stabilizes at ~50-80 tracked stocks
```

## Pipeline Steps (Step Functions)

| Step | Lambda | Purpose |
|------|--------|---------|
| 1 | fundamentals-fetcher | Scan broad universe + fetch data via provider → S3 |
| 2 | stock-screener | Apply value filters → update Tracked Stocks list |
| 3 | news-fetcher | Fetch recent news for tracked stocks → S3 |
| 4 | sentiment-analyzer | Send articles to Bedrock/Claude → DynamoDB |
| 5 | score-calculator | Combine fundamental + sentiment scores → DynamoDB |
| 6 | alert-checker | Check thresholds → SNS notifications |

## Screening Criteria (from Finviz filters)

- P/E < 50
- Forward P/E < 20
- PEG < 1.0 (growth at reasonable price)
- Price/FCF < 20
- Debt/Equity < 1.0
- Quick Ratio > 1.0
- Operating Margin > 0%
- EPS Growth YoY > 0%
- Estimated LT Growth > 0%
- Sales Growth QoQ > 0%
- Analyst Recommendation: Hold or better
- Institutional Transactions: Net positive
- Target Price Upside: > 20%

Filter defaults live in: `shared/config/screener-filters.json`

## Data Sources

### Current (POC — Free)
| Need | Source | Notes |
|------|--------|-------|
| Fundamentals | yfinance (Python) | Free, comprehensive, no API key |
| Stock universe | Wikipedia (S&P 500 + Nasdaq 100) | Dynamic, always current |
| News | TBD (Phase 2) | NewsAPI, GNews, or EDGAR |
| Sentiment | Amazon Bedrock / Claude | Pay-per-token, cheap at our scale |

### Future (Paid Upgrade — Drop-in via provider swap)
| Need | Source | Notes |
|------|--------|-------|
| Fundamentals | FMP Starter ($29/mo) | Full screener, stable API |
| News | Benzinga or dedicated news API | Higher quality, real-time |
| Real-time | Polygon.io | Streaming data |

## Build Phases

1. **Phase 1** — Fundamentals pipeline (fetch, screen, store) ← CURRENT
2. **Phase 2** — News + Sentiment pipeline (fetch articles, Bedrock/Claude scoring)
3. **Phase 3** — Scoring engine + Alerts (combine scores, threshold checking)
4. **Phase 4** — React dashboard (sliders, tables, charts)
5. **Phase 5** — Retroactive analysis (Athena + S3, historical trends)

## Project Structure

```
stock-screener/
├── bin/                     → CDK app entry point
├── lib/                     → CDK stack definitions
├── lambdas/                 → Python Lambda functions (one folder each)
│   ├── fundamentals-fetcher/
│   │   ├── handler.py       → Lambda entry point (provider-agnostic)
│   │   ├── requirements.txt → Python dependencies
│   │   └── providers/       → Data provider abstraction
│   │       ├── __init__.py  → Factory + registry
│   │       ├── base.py      → Abstract interface + StockFundamentals schema
│   │       ├── yfinance_provider.py → Free provider (current)
│   │       └── fmp_provider.py      → Paid provider (future upgrade)
│   ├── stock-screener/
│   ├── news-fetcher/
│   ├── sentiment-analyzer/
│   ├── score-calculator/
│   └── alert-checker/
├── frontend/                → React app (Phase 4)
├── shared/config/           → Shared configuration (screener-filters.json)
└── .kiro/steering/          → Architecture docs (this file)
```

## Conventions

- Lambdas: Python 3.12 ARM64, each in its own folder with handler.py + requirements.txt
- Dependencies: Docker-bundled via PythonFunction CDK construct
- Infrastructure: TypeScript CDK, single stack for now (split later if needed)
- Config: JSON files in shared/config/ — single source of truth for frontend + backend
- Naming: kebab-case for folders, snake_case for Python, camelCase for TypeScript
- Secrets: Always in SSM Parameter Store (SecureString), never in code or env vars
- Always pin dependency versions for reproducibility
- Remove dead code and obsolete files immediately

## AWS Resources (Deployed)

- **S3 Bucket**: `stock-screener-raw-data-116488731375`
- **Lambda**: `stock-screener-fundamentals-fetcher` (ARM64, Python 3.12)
- **SSM Parameter**: `/stock-screener/fmp-api-key` (SecureString, for future FMP use)
- **CDK Bootstrap**: Account 116488731375 / us-east-2 bootstrapped
- **Stack**: `StockScreenerStack`
