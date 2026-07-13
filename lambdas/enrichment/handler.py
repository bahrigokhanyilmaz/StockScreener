"""
Price Enrichment Lambda
========================
Step 3 in the pipeline (between initial screen and full screen).

Takes stocks that passed the EDGAR-based pre-screen (~233 stocks)
and adds current stock prices from Twelve Data.

With price data, the downstream screener can evaluate:
- P/E ratio (Price / EPS)
- Price/FCF (Price / Free Cash Flow per share)
- Market Cap (Price × Shares Outstanding)

Twelve Data free tier: 800 credits/day, 8 credits/minute.
Each symbol = 1 credit. We can batch up to 8 symbols per request.
~233 stocks = ~30 requests (8 symbols each) = ~4 minutes at 1 req/sec pacing.

This Lambda processes ALL stocks passed to it — no artificial limit.
The number of stocks is naturally limited by the upstream EDGAR filters.

Environment Variables:
    TWELVE_DATA_KEY_PARAM - SSM path for the Twelve Data API key
"""

import json
import os
import time
import math
from datetime import datetime, timezone

import boto3
import requests as http_requests

# AWS
ssm_client = boto3.client("ssm")

# Twelve Data
TWELVE_DATA_URL = "https://api.twelvedata.com/price"
BATCH_SIZE = 8  # Max credits per minute = 8, so request 8 symbols at once
RATE_PAUSE = 61  # Seconds to wait between batches (must wait full minute for credit reset)

# Cache
_api_key = None


def get_api_key() -> str:
    """Fetch Twelve Data API key from SSM."""
    global _api_key
    if _api_key:
        return _api_key

    param_name = os.environ.get("TWELVE_DATA_KEY_PARAM")
    if not param_name:
        raise ValueError("TWELVE_DATA_KEY_PARAM env var not set")

    response = ssm_client.get_parameter(Name=param_name, WithDecryption=True)
    _api_key = response["Parameter"]["Value"]
    return _api_key


def fetch_prices_batch(symbols: list[str], api_key: str) -> dict[str, float]:
    """
    Fetch prices for a batch of symbols from Twelve Data.

    Args:
        symbols: List of ticker symbols (max 8 per call)
        api_key: Twelve Data API key

    Returns:
        Dict mapping symbol → price (float)
    """
    symbols_str = ",".join(symbols)
    try:
        response = http_requests.get(
            TWELVE_DATA_URL,
            params={"symbol": symbols_str, "apikey": api_key},
            timeout=15,
        )
        if response.status_code != 200:
            print(f"    Warning: Twelve Data returned {response.status_code}")
            return {}

        data = response.json()

        # Handle rate limit error
        if isinstance(data, dict) and data.get("code") == 429:
            print(f"    Rate limited — waiting...")
            return {}

        # Single symbol returns {"price": "123.45"}
        # Multiple symbols returns {"AAPL": {"price": "123.45"}, "MSFT": {...}}
        prices = {}
        if len(symbols) == 1 and "price" in data:
            prices[symbols[0]] = float(data["price"])
        else:
            for sym, val in data.items():
                if isinstance(val, dict) and "price" in val:
                    prices[sym] = float(val["price"])

        return prices

    except Exception as e:
        print(f"    Warning: Twelve Data error: {e}")
        return {}


def handler(event, context):
    """
    Lambda entry point.

    Takes stocks from the initial screener (Step 2) that passed EDGAR-based
    filters. Adds current price to each stock. Returns enriched stocks
    for the full screener (Step 4) to evaluate price-based filters.

    The input stocks already have: D/E, Quick Ratio, Operating Margin, EPS,
    revenue_per_share, etc. from EDGAR. This step adds: price, market_cap,
    pe_ratio, price_to_fcf.

    Input event:
        event["passing_stocks"] — stocks that passed initial EDGAR screen
        event["near_misses"] — stocks close to passing (also enrich these)

    Output:
        Same structure with price fields populated.
    """
    start_time = datetime.now(timezone.utc)
    print(f"Starting price enrichment at {start_time.isoformat()}")

    # Collect all stocks to enrich (passers + near misses)
    passing = event.get("passing_stocks", [])
    near_misses = event.get("near_misses", [])
    all_stocks = passing + near_misses

    if not all_stocks:
        print("No stocks to enrich")
        return {
            "enriched_stocks": [],
            "metadata": {"error": "No stocks provided"},
        }

    # Extract symbols
    symbols = [s.get("symbol") for s in all_stocks if s.get("symbol")]
    print(f"Enriching {len(symbols)} stocks with price data from Twelve Data")

    # Fetch prices in batches of 8 (respecting 8 credits/minute limit)
    api_key = get_api_key()
    all_prices = {}
    num_batches = math.ceil(len(symbols) / BATCH_SIZE)

    for i in range(num_batches):
        batch = symbols[i * BATCH_SIZE : (i + 1) * BATCH_SIZE]
        prices = fetch_prices_batch(batch, api_key)
        all_prices.update(prices)

        fetched_so_far = len(all_prices)
        print(f"  Batch {i+1}/{num_batches}: got {len(prices)} prices "
              f"(total: {fetched_so_far}/{len(symbols)})")

        # Wait for rate limit reset (8 credits/minute)
        if i < num_batches - 1:
            time.sleep(RATE_PAUSE)

    print(f"Prices fetched: {len(all_prices)}/{len(symbols)}")

    # Enrich each stock with price-derived metrics
    enriched = []
    for stock in all_stocks:
        symbol = stock.get("symbol", "")
        price = all_prices.get(symbol)

        if price:
            stock["price"] = price

            # Calculate P/E = Price / EPS
            eps = stock.get("eps")
            if eps and eps > 0:
                stock["pe_ratio"] = round(price / eps, 2)

            # Calculate Market Cap = Price × Shares Outstanding
            # (shares_outstanding not in the stock dict from screener,
            #  but EPS is calculated from net_income/shares, so we can
            #  reverse: shares = net_income / EPS if both available)
            # For now, leave market_cap as None — we'll get it from
            # Alpha Vantage for tracked stocks later, or compute it.

            # Calculate Price/FCF if fcf_per_share available
            fcf_ps = stock.get("fcf_per_share")
            if fcf_ps and fcf_ps > 0:
                stock["price_to_fcf"] = round(price / fcf_ps, 2)

        enriched.append(stock)

    # Count how many now have P/E
    pe_count = sum(1 for s in enriched if s.get("pe_ratio") is not None)
    print(f"Stocks with P/E after enrichment: {pe_count}/{len(enriched)}")

    end_time = datetime.now(timezone.utc)
    duration = (end_time - start_time).total_seconds()

    result = {
        "enriched_stocks": enriched,
        "metadata": {
            "total_stocks": len(all_stocks),
            "prices_fetched": len(all_prices),
            "pe_calculated": pe_count,
            "duration_seconds": duration,
            "timestamp": end_time.isoformat(),
        },
    }

    print(f"Done in {duration:.1f}s.")
    return result
