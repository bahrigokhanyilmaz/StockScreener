"""
News Fetcher Lambda
===================
Step 3 in the pipeline.

Takes the list of passing/tracked stocks from the screener (Step 2)
and fetches recent news articles for each using TickerTick API.

TickerTick is:
- Free, no API key needed
- Covers all US-listed stocks (~10,000 tickers)
- Sources from ~10,000 websites (Reuters, WSJ, CNBC, SEC filings, etc.)
- Rate limit: 10 requests/minute (we respect this with delays)

Input (from Step Functions / stock-screener):
    event["passing_stocks"] — stocks that passed value filters
    event["near_misses"] — stocks close to passing (optional: fetch for these too)

Output:
    - List of stocks with their recent articles attached
    - Articles include: title, description, source, url, publish time

Environment Variables:
    RAW_DATA_BUCKET - S3 bucket for storing raw news data
    NEWS_LOOKBACK_HOURS - How far back to fetch news (default: 168 = 7 days)
"""

import json
import os
import time
from datetime import datetime, timezone

import boto3
import requests

# AWS clients
s3_client = boto3.client("s3")

# TickerTick API
TICKERTICK_BASE_URL = "https://api.tickertick.com/feed"

# Rate limit: 10 requests per minute → 6 seconds between requests
RATE_LIMIT_DELAY = 6.5  # seconds between requests (safe margin)


def fetch_news_for_ticker(symbol: str, lookback_hours: int = 168, max_articles: int = 10) -> list[dict]:
    """
    Fetch recent news articles for a single stock ticker from TickerTick.

    Args:
        symbol: Stock ticker (e.g., "AAPL")
        lookback_hours: How far back to search (default 168 = 7 days)
        max_articles: Maximum articles to return per ticker

    Returns:
        List of article dicts with: title, description, url, source, published_at
    """
    params = {
        "q": f"tt:{symbol.lower()}",
        "lang": "en",
        "n": max_articles,
        "hours_ago": lookback_hours,
    }

    try:
        response = requests.get(TICKERTICK_BASE_URL, params=params, timeout=15)
        if response.status_code != 200:
            print(f"  Warning: TickerTick returned {response.status_code} for {symbol}")
            return []

        data = response.json()
        stories = data.get("stories", [])

        # Normalize to our standard article schema
        articles = []
        for story in stories:
            article = {
                "title": story.get("title", ""),
                "description": story.get("description", ""),
                "url": story.get("url", ""),
                "source": story.get("site", ""),
                "published_at": story.get("time", 0),  # Unix timestamp (ms)
                "tags": story.get("tags", []),
                "ticker": symbol,
            }
            articles.append(article)

        return articles

    except requests.RequestException as e:
        print(f"  Warning: TickerTick error for {symbol}: {e}")
        return []


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
    start_time = datetime.now(timezone.utc)
    print(f"Starting news fetch at {start_time.isoformat()}")

    # Get the stocks we want news for
    passing_stocks = event.get("passing_stocks", [])
    near_misses = event.get("near_misses", [])

    # Fetch news for passing stocks + near misses (both are tracked)
    stocks_to_fetch = passing_stocks + near_misses
    symbols = [s.get("symbol") for s in stocks_to_fetch if s.get("symbol")]

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
    lookback_hours = int(os.environ.get("NEWS_LOOKBACK_HOURS", "168"))  # 7 days
    bucket_name = os.environ.get("RAW_DATA_BUCKET")

    # Fetch news for each stock (respecting rate limits)
    stocks_with_news = []
    total_articles = 0

    for i, symbol in enumerate(symbols):
        articles = fetch_news_for_ticker(symbol, lookback_hours=lookback_hours)
        total_articles += len(articles)

        stock_data = next((s for s in stocks_to_fetch if s.get("symbol") == symbol), {})
        stocks_with_news.append({
            **stock_data,
            "articles": articles,
            "article_count": len(articles),
        })

        print(f"  [{i+1}/{len(symbols)}] {symbol}: {len(articles)} articles")

        # Store raw news in S3
        if bucket_name and articles:
            store_raw_news(bucket_name, articles, symbol)

        # Rate limiting: TickerTick allows 10 req/min
        if i < len(symbols) - 1:
            time.sleep(RATE_LIMIT_DELAY)

    # Build response
    end_time = datetime.now(timezone.utc)
    duration = (end_time - start_time).total_seconds()

    result = {
        "stocks_with_news": stocks_with_news,
        "metadata": {
            "stocks_requested": len(symbols),
            "articles_fetched": total_articles,
            "lookback_hours": lookback_hours,
            "duration_seconds": duration,
            "timestamp": end_time.isoformat(),
        },
    }

    print(f"Done in {duration:.1f}s. Fetched {total_articles} articles "
          f"for {len(symbols)} stocks.")

    return result
