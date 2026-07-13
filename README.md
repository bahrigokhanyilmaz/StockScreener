# Stock Screener

A value stock screening application that combines fundamental financial analysis with news sentiment scoring to produce an "Investability Score" for each stock.

## What It Does

1. **Scans the US stock market** — SEC EDGAR provides financials for ~6,000 companies
2. **Filters by value criteria** — P/E, PEG, Debt/Equity, Quick Ratio, Operating Margin, etc.
3. **Fetches news** — Recent articles from ~10,000 sources per passing stock
4. **Analyzes sentiment** — Claude AI scores each article (-1 to +1) and detects risk flags
5. **Calculates investability** — Combines fundamental quality + sentiment into a 0-100 score
6. **Alerts you** — Email notification when stocks breach thresholds or new value picks appear

## Architecture

AWS serverless — runs daily at market close (4 PM ET), costs under $5/month.

```
EventBridge (daily trigger) → Step Functions (6-step pipeline)
  → EDGAR (fundamentals) → Filter (value screen) → TickerTick (news)
  → Bedrock/Claude (sentiment) → Score Calculator → Alert Checker
  → DynamoDB (persistence) → API Gateway (REST) → React Dashboard
```

## Quick Start

### Prerequisites
- AWS CLI configured (`aws configure --profile stock-screener`)
- Node.js 20+, Python 3.12+, Docker Desktop
- CDK CLI (`npm install -g aws-cdk`)

### Deploy
```bash
npm install
npm run build
cdk deploy
```

### Run Pipeline Manually
```bash
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:us-east-2:116488731375:stateMachine:stock-screener-pipeline \
  --input '{"universe": ["AAPL", "MSFT", "GOOGL", "META", "AMZN"], "batch_size": 5, "max_enrichments": 5}' \
  --profile stock-screener --region us-east-2
```

### API
Base URL: `https://kw8mlahpj2.execute-api.us-east-2.amazonaws.com/prod`

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | /stocks | All tracked stocks with scores |
| GET | /stocks/{ticker} | Single stock detail |
| GET | /stocks/{ticker}/history | Score history (for charts) |
| POST | /stocks/{ticker}/track | Manually track a stock |
| DELETE | /stocks/{ticker}/track | Stop tracking |
| GET | /pipeline/status | Pipeline run summary |

### Local Development
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install requests boto3

# Run Lambda locally
cd lambdas/fundamentals-fetcher
python3 -c "from handler import handler; print(handler({'universe': ['AAPL']}, None))"
```

## Project Structure

```
├── lib/stock-screener-stack.ts     CDK infrastructure (all AWS resources)
├── lambdas/
│   ├── fundamentals-fetcher/       SEC EDGAR + Alpha Vantage data
│   ├── stock-screener/             Value filter logic
│   ├── news-fetcher/               TickerTick news articles
│   ├── sentiment-analyzer/         Bedrock/Claude sentiment
│   ├── score-calculator/           Investability scoring + DynamoDB
│   ├── alert-checker/              Threshold monitoring + SNS
│   └── api/                        REST API for dashboard
├── frontend/                       React dashboard (coming soon)
└── shared/config/                  Filter thresholds (screener-filters.json)
```

## Data Sources

| Source | What | Cost |
|--------|------|------|
| SEC EDGAR | Financial statements (6,000+ companies) | Free |
| Alpha Vantage | Price, P/E, PEG, analyst targets | Free (25/day) |
| TickerTick | News articles per ticker | Free |
| Bedrock Claude | Sentiment analysis | ~$3.60/month |

## Screening Criteria (Configurable)

Based on value investing principles:
- P/E < 50, Forward P/E < 20, PEG < 1.0
- Debt/Equity < 1.0, Quick Ratio > 1.0
- Operating Margin > 0%, EPS Growth > 0%
- Analyst Target Upside > 20%
- News Sentiment > -0.3

All thresholds are configurable via `shared/config/screener-filters.json` and will be adjustable via UI sliders.
