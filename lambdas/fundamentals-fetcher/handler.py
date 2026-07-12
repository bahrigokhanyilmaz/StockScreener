"""
Fundamentals Fetcher Lambda
============================
Step 1 in the stock screener pipeline.

Responsibilities:
1. Discover the stock universe (which stocks to analyze)
2. Fetch fundamental data for each stock via the configured provider
3. Store raw data in S3 (timestamped — for retroactive analysis)
4. Return standardized data for the next step (stock-screener Lambda)

Architecture:
    This handler is provider-agnostic. It reads a PROVIDER env var
    to determine which data source to use (yfinance, fmp, etc.).
    Switching providers requires only a config change in CDK — no
    code modifications.

Environment Variables (set by CDK):
    PROVIDER        - Data provider to use ("yfinance" or "fmp")
    RAW_DATA_BUCKET - S3 bucket for raw data storage
    FMP_API_KEY_PARAM - (Optional) SSM path for FMP API key (only if PROVIDER=fmp)

Invocation:
    Called by Step Functions on a schedule (EventBridge cron).
    Supports batching via event payload for large universes:
        {"batch_start": 0, "batch_size": 50}
"""

import json
import os
from datetime import datetime, timezone

import boto3

from providers import get_provider
from providers.base import ProviderError

# AWS clients
s3_client = boto3.client("s3")
ssm_client = boto3.client("ssm")


def get_provider_config():
    """
    Build provider configuration from environment variables.

    For yfinance: no extra config needed
    For fmp: fetch the API key from SSM Parameter Store

    Returns:
        Tuple of (provider_name, kwargs_dict)
    """
    provider_name = os.environ.get("PROVIDER", "fmp")
    kwargs = {}

    if provider_name == "fmp":
        param_name = os.environ.get("FMP_API_KEY_PARAM")
        if not param_name:
            raise ValueError(
                "FMP_API_KEY_PARAM env var required when PROVIDER=fmp"
            )
        response = ssm_client.get_parameter(Name=param_name, WithDecryption=True)
        kwargs["api_key"] = response["Parameter"]["Value"]

        # Pass configurable market cap threshold
        min_market_cap = os.environ.get("MIN_MARKET_CAP")
        if min_market_cap:
            kwargs["min_market_cap"] = float(min_market_cap)

    return provider_name, kwargs


def store_raw_data(bucket_name, data, data_type):
    """
    Store data in S3, partitioned by date for efficient querying.

    Path: raw/{data_type}/YYYY/MM/DD/{data_type}_{timestamp}.json

    This structure supports:
    - Amazon Athena queries by date range (partition pruning)
    - Easy identification of when data was captured
    - Multiple runs per day without overwrites
    - Long-term retention for trend analysis
    """
    now = datetime.now(timezone.utc)
    key = (
        f"raw/{data_type}/"
        f"{now.strftime('%Y/%m/%d')}/"
        f"{data_type}_{now.strftime('%Y%m%d_%H%M%S')}.json"
    )

    s3_client.put_object(
        Bucket=bucket_name,
        Key=key,
        Body=json.dumps(data, default=str),
        ContentType="application/json",
    )

    print(f"Stored {len(data) if isinstance(data, list) else 1} records "
          f"to s3://{bucket_name}/{key}")
    return key


def handler(event, context):
    """
    Lambda entry point. Called by Step Functions.

    Flow:
    1. Initialize the configured data provider
    2. Discover stock universe (or use a batch slice)
    3. Fetch fundamentals for each stock
    4. Store raw results in S3
    5. Return standardized data for the screening step

    Batching:
        For large universes (500+ stocks), the Step Functions state machine
        can invoke this Lambda multiple times with different batch slices:
        {"batch_start": 0, "batch_size": 100}
        {"batch_start": 100, "batch_size": 100}
        ...
        This keeps each invocation within Lambda's 5-min timeout.

    Args:
        event: Optional batch config from Step Functions
            - batch_start (int): Starting index in the universe
            - batch_size (int): Number of stocks to process
            - universe (list): Pre-provided stock list (skips discovery)
        context: Lambda runtime metadata
    """
    start_time = datetime.now(timezone.utc)
    print(f"Starting fundamentals fetch at {start_time.isoformat()}")

    # Initialize provider
    provider_name, provider_kwargs = get_provider_config()
    provider = get_provider(provider_name, **provider_kwargs)
    print(f"Using data provider: {provider.name}")

    # Discover or receive the stock universe
    if "universe" in event:
        # Universe pre-supplied (e.g., from a previous step or manual input)
        universe = event["universe"]
        print(f"Using pre-supplied universe: {len(universe)} stocks")
    else:
        # Provider discovers the universe dynamically
        print("Discovering stock universe...")
        try:
            universe = provider.get_stock_universe()
            print(f"Universe discovered: {len(universe)} stocks")
        except ProviderError as e:
            print(f"FATAL: Could not discover stock universe: {e}")
            raise

    # Apply batching if specified
    batch_start = event.get("batch_start", 0)
    batch_size = event.get("batch_size", len(universe))  # Default: process all
    batch = universe[batch_start:batch_start + batch_size]

    print(f"Processing batch: indices {batch_start} to {batch_start + len(batch)} "
          f"({len(batch)} stocks)")

    # Fetch fundamentals
    print("Fetching fundamentals...")
    results = provider.get_fundamentals_batch(batch)

    # Convert to dicts for serialization
    results_dicts = [stock.to_dict() for stock in results]

    # Store raw data in S3
    bucket_name = os.environ.get("RAW_DATA_BUCKET")
    if bucket_name and results_dicts:
        store_raw_data(bucket_name, results_dicts, "fundamentals")
    elif not bucket_name:
        print("Warning: RAW_DATA_BUCKET not set — skipping S3 storage")

    # Build response for the next pipeline step
    end_time = datetime.now(timezone.utc)
    duration_seconds = (end_time - start_time).total_seconds()

    response = {
        "stocks": results_dicts,
        "metadata": {
            "provider": provider.name,
            "universe_size": len(universe),
            "batch_start": batch_start,
            "batch_size": batch_size,
            "stocks_processed": len(batch),
            "stocks_enriched": len(results_dicts),
            "stocks_failed": len(batch) - len(results_dicts),
            "has_more": batch_start + batch_size < len(universe),
            "next_batch_start": batch_start + batch_size,
            "duration_seconds": duration_seconds,
            "timestamp": end_time.isoformat(),
        },
    }

    print(f"Done in {duration_seconds:.1f}s. "
          f"Enriched {len(results_dicts)}/{len(batch)} stocks.")

    return response
