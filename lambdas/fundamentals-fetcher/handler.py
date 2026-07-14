"""
Fundamentals Fetcher Lambda
============================
Step 1 in the pipeline.

Single responsibility: Fetch fundamental financial data for the stock universe
via the SEC EDGAR Frames API.

This Lambda does NOT filter or enrich. It fetches raw data and passes it
forward. Separation of concerns:
- Step 1 (this): Fetch data
- Step 2 (screener): Filter and rank
- Step 3 (enrichment): Add price data to top candidates

The EDGAR Frames API gives us ALL companies in ~10 API calls (bulk).
No per-stock iteration needed.

Environment Variables:
    PROVIDER          - Data provider to use ("edgar" or "fmp")
    RAW_DATA_BUCKET   - S3 bucket for raw data storage
    ALPHA_VANTAGE_KEY_PARAM - (unused here — moved to enrichment step)
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
    """Build provider configuration from environment variables."""
    provider_name = os.environ.get("PROVIDER", "edgar")
    kwargs = {}

    if provider_name == "edgar":
        # No API key needed for EDGAR — it's free and public
        min_market_cap = os.environ.get("MIN_MARKET_CAP")
        if min_market_cap:
            kwargs["min_market_cap"] = float(min_market_cap)

    elif provider_name == "fmp":
        param_name = os.environ.get("FMP_API_KEY_PARAM")
        if not param_name:
            raise ValueError("FMP_API_KEY_PARAM env var required when PROVIDER=fmp")
        response = ssm_client.get_parameter(Name=param_name, WithDecryption=True)
        kwargs["api_key"] = response["Parameter"]["Value"]
        min_market_cap = os.environ.get("MIN_MARKET_CAP")
        if min_market_cap:
            kwargs["min_market_cap"] = float(min_market_cap)

    return provider_name, kwargs


def store_raw_data(bucket_name, data, data_type):
    """Store data in S3, partitioned by date."""
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
    print(f"Stored {len(data) if isinstance(data, list) else 1} records to s3://{bucket_name}/{key}")
    return key


def handler(event, context):
    """
    Lambda entry point.

    Fetches fundamental data for the full stock universe via EDGAR.
    Returns ALL stocks with whatever data EDGAR provides — no filtering here.

    The EDGAR Frames API is bulk: ~10 requests fetches data for ~5,000 companies.
    No batching needed (unlike per-stock APIs).

    Args:
        event: Can contain 'universe' (pre-supplied ticker list) for testing
        context: Lambda runtime metadata
    """
    start_time = datetime.now(timezone.utc)
    print(f"Starting fundamentals fetch at {start_time.isoformat()}")

    # Initialize provider
    provider_name, provider_kwargs = get_provider_config()
    provider = get_provider(provider_name, **provider_kwargs)
    print(f"Using data provider: {provider.name}")

    # Discover the stock universe
    if "universe" in event:
        universe = event["universe"]
        print(f"Using pre-supplied universe: {len(universe)} stocks")
    else:
        print("Discovering stock universe...")
        try:
            universe = provider.get_stock_universe()
            print(f"Universe discovered: {len(universe)} stocks")
        except ProviderError as e:
            print(f"FATAL: Could not discover stock universe: {e}")
            raise

    # Fetch fundamentals (EDGAR: bulk, ~10 API calls regardless of universe size)
    print("Fetching fundamentals...")
    results = provider.get_fundamentals_batch(universe)

    # Convert to dicts for serialization
    results_dicts = [stock.to_dict() for stock in results]

    # Store raw data in S3 (all stocks — for retroactive analysis and slider exploration)
    bucket_name = os.environ.get("RAW_DATA_BUCKET")
    if bucket_name and results_dicts:
        store_raw_data(bucket_name, results_dicts, "fundamentals")

    # Build response for Step 2 (screener)
    end_time = datetime.now(timezone.utc)
    duration_seconds = (end_time - start_time).total_seconds()

    response = {
        "stocks": results_dicts,
        "metadata": {
            "provider": provider.name,
            "universe_size": len(universe),
            "stocks_with_data": len(results_dicts),
            "duration_seconds": duration_seconds,
            "timestamp": end_time.isoformat(),
        },
    }

    print(f"Done in {duration_seconds:.1f}s. Got data for {len(results_dicts)}/{len(universe)} stocks.")

    # Write output to S3 (Step Functions has 256KB payload limit)
    from pipeline_io import write_pipeline_output
    return write_pipeline_output(response, step_name="step1_fundamentals")
