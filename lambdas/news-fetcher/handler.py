"""
News Fetcher Lambda
===================
Step 5 in the pipeline.

Takes the list of passing/tracked stocks from the full screen (Step 4)
and fetches recent news articles for each using FMP /stable/news/stock.

FMP News:
- Requires Starter plan ($19/mo)
- Endpoint: /stable/news/stock?symbols=AAPL&limit=10
- Returns: symbol, publishedDate, publisher, title, text, url, image, site
- Rate limit: 300 requests/minute (very generous)
- Sources: Motley Fool, Seeking Alpha, Reuters, Bloomberg, etc.

Input (from Step Functions):
    event["passing_stocks"] — stocks that passed value filters

Output:
    - List of stocks with their recent articles attached
    - Articles include: title, description, source, url, published_at

Environment Variables:
    RAW_DATA_BUCKET - S3 bucket for storing raw news data
    DATA_TABLE_NAME - DynamoDB table for GRACE stocks lookup
    FMP_API_KEY_PARAM - SSM path for FMP API key
"""

import json
import os
import time
import urllib.request
from datetime import datetime, timezone

import boto3

# AWS clients
s3_client = boto3.client("s3")
ssm_client = boto3.client("ssm")

# FMP API
FMP_BASE = "https://financialmodelingprep.com/stable"

# Rate limit: 300 requests/min → can go fast, but add small delay for safety
RATE_LIMIT_DELAY = 1.0  # seconds between requests

# Cache
_fmp_key = None


def get_fmp_key() -> str:
    global _fmp_key
    if not _fmp_key:
        param = os.environ.get("FMP_API_KEY_PARAM", "/stock-screener/fmp-api-key")
        resp = ssm_client.get_parameter(Name=param, WithDecryption=True)
        _fmp_key = resp["Parameter"]["Value"]
    return _fmp_key


def fetch_news_for_ticker(symbol: str, api_key: str, max_articles: int = 10) -> list[dict]:
    """
    Fetch recent news articles for a single stock ticker from FMP.
    Fallback for when batching doesn't return enough articles for a ticker.
    """
    url = (f"{FMP_BASE}/news/stock?symbols={symbol}&limit={max_articles}"
           f"&apikey={api_key}")
    req = urllib.request.Request(url, headers={"User-Agent": "StockScreener/2.0"})

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())

        if not isinstance(data, list):
            return []

        return _normalize_articles(data, symbol)

    except urllib.error.HTTPError as e:
        print(f"  Warning: FMP news returned HTTP {e.code} for {symbol}")
        return []
    except Exception as e:
        print(f"  Warning: FMP news error for {symbol}: {e}")
        return []


def fetch_news_batch(symbols: list[str], api_key: str, articles_per_stock: int = 10) -> dict[str, list[dict]]:
    """
    Fetch news for multiple stocks in a single API call.

    FMP /stable/news/stock supports comma-separated symbols.
    The 'limit' parameter is TOTAL across all symbols (not per-symbol),
    so we set limit = len(symbols) × articles_per_stock to ensure coverage.

    Returns: {symbol: [articles]} dict
    """
    symbols_str = ",".join(symbols)
    total_limit = len(symbols) * articles_per_stock
    url = (f"{FMP_BASE}/news/stock?symbols={symbols_str}&limit={total_limit}"
           f"&apikey={api_key}")
    req = urllib.request.Request(url, headers={"User-Agent": "StockScreener/2.0"})

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())

        if not isinstance(data, list):
            return {sym: [] for sym in symbols}

        # Group articles by symbol
        result = {sym: [] for sym in symbols}
        for story in data:
            sym = story.get("symbol", "")
            if sym in result:
                result[sym].append(_normalize_article(story))

        return result

    except Exception as e:
        print(f"  Warning: Batch news fetch error: {e}")
        return {sym: [] for sym in symbols}


def _normalize_article(story: dict) -> dict:
    """Normalize a single FMP news story to our standard schema."""
    return {
        "title": story.get("title", ""),
        "description": story.get("text", ""),  # FMP 'text' = article summary
        "url": story.get("url", ""),
        "source": story.get("publisher", "") or story.get("site", ""),
        "published_at": story.get("publishedDate", ""),
        "image": story.get("image", ""),
        "ticker": story.get("symbol", ""),
    }


def _normalize_articles(data: list, symbol: str) -> list[dict]:
    """Normalize a list of FMP news stories."""
    articles = []
    for story in data:
        article = _normalize_article(story)
        article["ticker"] = symbol  # Ensure ticker is set
        articles.append(article)
    return articles


def store_raw_news(bucket_name: str, data: list, symbol: str):
    """Store raw news data in S3, organized by date and ticker."""
    now = datetime.now(timezone.utc)
    key = (
        f"raw/news/{now.strftime('%Y/%m/%d')}/"
        f"{symbol}_{now.strftime('%Y%m%d_%H%M%S')}.json"
    )

    s3_client.put_object(
        Bucket=bucket_name,
        Key=key,
        Body=json.dumps(data, default=str),
        ContentType="application/json",
    )
    return key


def handler(event, context):
    """
    Lambda entry point. Called by Step Functions after stock-screener.

    Input event:
        event["passing_stocks"] — stocks that passed the screen
        event["near_misses"] — optional: near-miss stocks to also track news for

    Output:
        List of stocks with articles attached, ready for sentiment analysis.
    """
    from pipeline_io import read_pipeline_input, write_pipeline_output

    start_time = datetime.now(timezone.utc)
    print(f"Starting news fetch at {start_time.isoformat()}")

    # Read input from S3 if needed (Step Functions payload limit workaround)
    data = read_pipeline_input(event)

    # Get passing stocks (Step Functions only passes stocks that cleared the full screen)
    passing_stocks = data.get("passing_stocks", [])

    # Also include GRACE stocks from DynamoDB (they need fresh news/sentiment too)
    grace_stocks = []
    try:
        import boto3
        table_name = os.environ.get("DATA_TABLE_NAME", "")
        if table_name:
            dynamodb = boto3.resource("dynamodb")
            table = dynamodb.Table(table_name)
            resp = table.query(
                IndexName="tracking-status-index",
                KeyConditionExpression=boto3.dynamodb.conditions.Key("tracking_status").eq("GRACE"),
            )
            passing_symbols = {s.get("symbol") for s in passing_stocks}
            for item in resp.get("Items", []):
                if item.get("SK") == "LATEST" and item.get("symbol") not in passing_symbols:
                    # Convert DynamoDB item to stock dict format
                    grace_stocks.append({
                        "symbol": item.get("symbol"),
                        "company_name": item.get("company_name", ""),
                        "price": float(item["price"]) if item.get("price") else None,
                    })
            if grace_stocks:
                print(f"  Including {len(grace_stocks)} GRACE stocks for news refresh")
    except Exception as e:
        print(f"  Warning: Could not load GRACE stocks: {e}")

    all_stocks = passing_stocks + grace_stocks
    symbols = [s.get("symbol") for s in all_stocks if s.get("symbol")]

    if not symbols:
        print("No stocks to fetch news for")
        return {
            "stocks_with_news": [],
            "metadata": {
                "stocks_requested": 0,
                "articles_fetched": 0,
                "timestamp": start_time.isoformat(),
            },
        }

    print(f"Fetching news for {len(symbols)} stocks: {symbols[:10]}...")

    # Configuration
    api_key = get_fmp_key()
    bucket_name = os.environ.get("RAW_DATA_BUCKET")

    # Fetch news in batches of 5 symbols (FMP limit is total, not per-symbol)
    # Batching: 5 symbols × 10 articles = limit=50 per call
    # For 20 stocks = 4 API calls instead of 20
    BATCH_SIZE = 5
    stocks_with_news = []
    total_articles = 0
    all_fetched = {}  # symbol → articles

    for batch_start in range(0, len(symbols), BATCH_SIZE):
        batch = symbols[batch_start:batch_start + BATCH_SIZE]
        batch_results = fetch_news_batch(batch, api_key, articles_per_stock=10)

        for sym, articles in batch_results.items():
            all_fetched[sym] = articles

        print(f"  Batch {batch_start//BATCH_SIZE + 1}: {batch} → "
              f"{sum(len(a) for a in batch_results.values())} articles")

        # Rate limiting between batches
        if batch_start + BATCH_SIZE < len(symbols):
            time.sleep(RATE_LIMIT_DELAY)

    # For any stock that got 0 articles from batching, try individual fetch
    for sym in symbols:
        if not all_fetched.get(sym):
            articles = fetch_news_for_ticker(sym, api_key)
            all_fetched[sym] = articles
            if articles:
                print(f"  {sym}: fallback individual fetch → {len(articles)} articles")
            time.sleep(0.5)

    # Build output
    for symbol in symbols:
        articles = all_fetched.get(symbol, [])
        total_articles += len(articles)

        stock_data = next((s for s in all_stocks if s.get("symbol") == symbol), {})
        stocks_with_news.append({
            **stock_data,
            "articles": articles,
            "article_count": len(articles),
        })

        # Store raw news in S3
        if bucket_name and articles:
            store_raw_news(bucket_name, articles, symbol)

    # Build response
    end_time = datetime.now(timezone.utc)
    duration = (end_time - start_time).total_seconds()

    result = {
        "stocks_with_news": stocks_with_news,
        "metadata": {
            "stocks_requested": len(symbols),
            "articles_fetched": total_articles,
            "lookback_hours": 168,  # FMP returns recent articles by default
            "duration_seconds": duration,
            "timestamp": end_time.isoformat(),
        },
    }

    print(f"Done in {duration:.1f}s. Fetched {total_articles} articles "
          f"for {len(symbols)} stocks.")

    return write_pipeline_output(result, step_name="step5_news")
