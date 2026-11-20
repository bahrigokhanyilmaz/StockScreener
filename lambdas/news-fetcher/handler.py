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

from market_time import new_york_date
from news_select import (cap_articles, detect_earnings_event, filter_company_articles,
                         plan_tickertick_calls)
from news_text import dedupe_articles

# AWS clients
s3_client = boto3.client("s3")
ssm_client = boto3.client("ssm")

# FMP API
FMP_BASE = "https://financialmodelingprep.com/stable"

# Rate limit: 300 requests/min → can go fast, but add small delay for safety
RATE_LIMIT_DELAY = 1.0  # seconds between requests

# TickerTick (free tier: 10 req/min). Calls are paced start-to-start and capped so
# the phase fits the 15-minute Lambda timeout even when every call times out:
# 20 s other work + 420 s phase budget + 15 s last call = about 465 s.
TT_PACE_SECONDS = 6.5
TT_MAX_CALLS = 45
TT_PHASE_BUDGET_S = 420
TT_MIN_REMAINING_MS = 150_000
# n=6 never reached the earnings items (HLIO: newest 6 were wrong-company noise); n=50 reaches back ~2 weeks.
TT_FEED_SIZE = 50
TT_MAX_AGE_DAYS = 14
TT_MIN_DESCRIPTION_CHARS = 40
MAX_ARTICLES_PER_STOCK = 12

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


def fetch_tickertick_news(symbol: str, max_articles: int = TT_FEED_SIZE) -> list[dict]:
    """
    Fetch news from TickerTick (free, no API key needed).
    Catches articles from sources FMP doesn't cover (tickerreport, gurufocus, sec.gov, etc.)
    Rate limit: 10 req/min — caller must pace.
    """
    url = f"https://api.tickertick.com/feed?q=tt:{symbol.lower()}&lang=en&n={max_articles}"
    req = urllib.request.Request(url, headers={"User-Agent": "StockScreener/2.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        stories = data.get("stories", [])
        articles = []
        for story in stories:
            ts = story.get("time", 0)
            # Convert ms timestamp to a UTC date string (same convention as FMP)
            pub_date = ""
            if ts and ts > 0:
                pub_date = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            articles.append({
                "title": story.get("title", ""),
                "description": story.get("description", ""),
                "url": story.get("url", ""),
                "source": story.get("site", ""),
                "published_at": pub_date,
                "image": "",
                "ticker": symbol,
            })
        return articles
    except Exception as e:
        print(f"  TickerTick error for {symbol}: {e}")
        return []


def load_latest_signals(table, symbols: list[str]) -> dict[str, dict]:
    """
    Read-only: per symbol, the stored news_price_divergence (advisory flag from
    Step 7) and the stored guidance event date. Symbols that fail to read are omitted.
    """
    signals = {}
    for sym in symbols:
        try:
            item = table.get_item(
                Key={"PK": f"STOCK#{sym}", "SK": "LATEST"},
                ProjectionExpression="news_price_divergence, guidance_signal.event_date",
            ).get("Item") or {}
        except Exception as e:
            print(f"  Warning: could not read LATEST signals for {sym}: {e}")
            continue
        divergence = item.get("news_price_divergence")
        signals[sym] = {
            # Decimal -> float so the value survives the JSON pipeline output
            "news_price_divergence": json.loads(json.dumps(divergence, default=float)) if divergence else None,
            "prior_event_date": (item.get("guidance_signal") or {}).get("event_date"),
        }
    return signals


def _remaining_ms(context) -> float:
    getter = getattr(context, "get_remaining_time_in_millis", None)
    return getter() if getter else float("inf")


def _recent_company_articles(tt_articles: list, symbol: str, company_name: str, today: str) -> list:
    """TickerTick items about this company that are at most TT_MAX_AGE_DAYS old."""
    cutoff = datetime.fromisoformat(today).replace(tzinfo=timezone.utc).timestamp() - TT_MAX_AGE_DAYS * 86400
    fresh = []
    for a in filter_company_articles(tt_articles, symbol, company_name):
        try:
            ts = datetime.strptime(a.get("published_at") or "", "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
        if ts >= cutoff:
            fresh.append(a)
    return fresh


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

    # Investability gate: only stocks worth full analysis get news/sentiment/competition
    # - Average daily volume > 100K (can enter/exit position)
    # - At least 1 analyst covering (basic price discovery)
    # Stocks failing this gate still passed fundamentals but aren't investable
    qualified_stocks = []
    for stock in passing_stocks:
        avg_vol = stock.get("average_volume") or 0
        has_analyst = stock.get("analyst_recommendation") is not None
        if avg_vol >= 100_000 and has_analyst:
            qualified_stocks.append(stock)

    gated_count = len(passing_stocks) - len(qualified_stocks)
    if gated_count > 0:
        print(f"  Investability gate: {len(passing_stocks)} passed filters → {len(qualified_stocks)} qualify (volume>100K + analyst)")
        print(f"  Excluded: {gated_count} stocks (low volume or no analyst coverage)")
    passing_stocks = qualified_stocks

    # Also include ACTIVE and GRACE tracked stocks from DynamoDB
    # (they need fresh news/sentiment/competition even if they failed today's filters)
    tracked_stocks = []
    table = None
    try:
        import boto3
        table_name = os.environ.get("DATA_TABLE_NAME", "")
        if table_name:
            dynamodb = boto3.resource("dynamodb")
            table = dynamodb.Table(table_name)
            passing_symbols = {s.get("symbol") for s in passing_stocks}
            for status in ["GRACE", "ACTIVE"]:
                resp = table.query(
                    IndexName="tracking-status-index",
                    KeyConditionExpression=boto3.dynamodb.conditions.Key("tracking_status").eq(status),
                )
                for item in resp.get("Items", []):
                    if item.get("SK") == "LATEST" and item.get("symbol") not in passing_symbols:
                        tracked_stocks.append({
                            "symbol": item.get("symbol"),
                            "company_name": item.get("company_name", ""),
                            "sic_industry": item.get("sic_industry", ""),
                            "price": float(item["price"]) if item.get("price") else None,
                        })
            if tracked_stocks:
                print(f"  Including {len(tracked_stocks)} tracked stocks (ACTIVE+GRACE) for news refresh")
    except Exception as e:
        print(f"  Warning: Could not load tracked stocks: {e}")

    all_stocks = passing_stocks + tracked_stocks
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
        batch_results = fetch_news_batch(batch, api_key, articles_per_stock=6)

        for sym, articles in batch_results.items():
            all_fetched[sym] = articles

        print(f"  Batch {batch_start//BATCH_SIZE + 1}: {batch} → "
              f"{sum(len(a) for a in batch_results.values())} articles")

        # Rate limiting between batches
        if batch_start + BATCH_SIZE < len(symbols):
            time.sleep(RATE_LIMIT_DELAY)

    # For any stock that got 0 articles from batching, try individual FMP fetch
    for sym in symbols:
        if not all_fetched.get(sym):
            articles = fetch_news_for_ticker(sym, api_key)
            all_fetched[sym] = articles
            if articles:
                print(f"  {sym}: fallback individual FMP fetch → {len(articles)} articles")
            time.sleep(0.5)

    # Earnings events and divergence flags decide who gets a TickerTick call
    today = new_york_date()
    stock_by_symbol = {s.get("symbol"): s for s in all_stocks}
    signals = load_latest_signals(table, symbols) if table is not None else {}
    events = {}
    for sym in symbols:
        prior = signals.get(sym, {}).get("prior_event_date")
        events[sym] = detect_earnings_event(all_fetched.get(sym, []), today, prior_event_date=prior)

    candidates = [{
        "symbol": sym,
        "fmp_count": len(all_fetched.get(sym, [])),
        "earnings_event": events[sym],
        "divergence": bool(signals.get(sym, {}).get("news_price_divergence")),
    } for sym in symbols]
    selected, deferred = plan_tickertick_calls(candidates, TT_MAX_CALLS, today)
    print(f"  TickerTick plan: {len(selected)} calls (cap {TT_MAX_CALLS}), {len(deferred)} deferred "
          f"{deferred[:10]}, {sum(1 for e in events.values() if e)} earnings events, "
          f"{sum(1 for c in candidates if c['divergence'])} divergence flags")

    # TickerTick: 10 req/min free tier, paced start-to-start so a slow response does not add idle time
    tt_started = time.monotonic()
    last_call_start = None
    calls_made = 0
    for sym in selected:
        if time.monotonic() - tt_started >= TT_PHASE_BUDGET_S:
            print(f"  TickerTick phase budget {TT_PHASE_BUDGET_S}s reached after {calls_made} calls")
            break
        if _remaining_ms(context) < TT_MIN_REMAINING_MS:
            print(f"  Lambda time nearly out, stopping TickerTick after {calls_made} calls")
            break
        if last_call_start is not None:
            wait = TT_PACE_SECONDS - (time.monotonic() - last_call_start)
            if wait > 0:
                time.sleep(wait)
        last_call_start = time.monotonic()
        tt_articles = fetch_tickertick_news(sym)
        calls_made += 1

        company_name = stock_by_symbol.get(sym, {}).get("company_name", "")
        fmp_articles = all_fetched.get(sym, [])
        tt_recent = _recent_company_articles(tt_articles, sym, company_name, today)
        # Detect on the merged list first: short items (sec.gov, empty Seeking Alpha) still evidence an event
        events[sym] = detect_earnings_event(fmp_articles + tt_recent, today,
                                            prior_event_date=signals.get(sym, {}).get("prior_event_date"))
        usable = [a for a in tt_recent if len(a.get("description") or "") >= TT_MIN_DESCRIPTION_CHARS]
        all_fetched[sym] = fmp_articles + usable
        print(f"    {sym}: TickerTick {len(tt_articles)} stories, {len(tt_recent)} about the company, "
              f"{len(usable)} usable")
    print(f"  TickerTick calls: planned {len(selected)}, made {calls_made}")

    # Build output
    for symbol in symbols:
        stock_data = stock_by_symbol.get(symbol, {})
        deduped, _ = dedupe_articles(all_fetched.get(symbol, []), stock_data.get("company_name", ""), symbol)
        articles = cap_articles(deduped, MAX_ARTICLES_PER_STOCK)
        total_articles += len(articles)

        event = events.get(symbol)
        stock_out = {
            **stock_data,
            "articles": articles,
            "article_count": len(articles),
        }
        if event:
            stock_out["earnings_event"] = {**event, "event_key": f"{symbol}:{event['event_date']}"}
        divergence = signals.get(symbol, {}).get("news_price_divergence")
        if divergence:
            stock_out["news_price_divergence"] = divergence
        stocks_with_news.append(stock_out)

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
