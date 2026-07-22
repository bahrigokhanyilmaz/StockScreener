"""
Stock Screener Lambda
=====================
Steps 2 & 4 in the pipeline.

Takes the enriched fundamental data from Step 1 (fundamentals-fetcher)
and applies value investing filter criteria to identify passing stocks.

For each stock that passes ALL filters, calculates a "fundamental score"
representing HOW STRONGLY it passes (a stock barely passing vs. one
that crushes every metric should be distinguished).

When running as Step 2 (pre-screen with is_prescreen=True):
- Also loads the static ticker→industry reference map from S3
- Joins industry labels to all ~5,097 stocks in memory
- Computes TRUE industry medians from the full unfiltered universe
- Persists medians to DynamoDB as INDUSTRY_AVG#{industry} items

Input (from Step Functions / fundamentals-fetcher):
    event["stocks"] — list of stock dicts with fundamental data

Output:
    - "passing_stocks": stocks that meet all criteria (with scores)
    - "all_stocks": full list with pass/fail status (for UI slider exploration)

Environment Variables:
    FILTERS_BUCKET - S3 bucket containing screener-filters.json (optional)
    
Design Notes:
    - Filters are loaded from the shared config (screener-filters.json)
    - The same filter logic can be invoked by the API (for real-time slider changes)
    - Stocks with missing data for a filter are treated as "not passing" that filter
      (conservative approach — we don't assume missing data is good)
    - The fundamental score is normalized 0-100
"""

import json
import os
from typing import Optional

# Default filters — loaded from shared config at module level.
# In Lambda, we bundle the config file. In the API layer,
# we could also load from S3 or DynamoDB (for user-customized presets).
FILTERS_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "shared", "config", "screener-filters.json"
)

# Fallback: if running in Lambda (bundled flat), try local path
LOCAL_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "screener-filters.json")


def load_default_filters() -> dict:
    """
    Load filter configuration from the shared config file.

    Returns the 'filters' dict from screener-filters.json.
    Each filter has: type (min/max), default value, and metadata.
    """
    # Try bundled config first (Lambda deployment)
    for path in [LOCAL_CONFIG_PATH, FILTERS_CONFIG_PATH]:
        if os.path.exists(path):
            with open(path) as f:
                config = json.load(f)
            return config.get("filters", {})

    # If no config file found, use hardcoded defaults (safety net)
    print("Warning: screener-filters.json not found, using hardcoded defaults")
    return {
        "pe_ratio": {"type": "max", "default": 50},
        "peg_ratio": {"type": "max", "default": 1.0},
        "price_to_fcf": {"type": "max", "default": 20},
        "debt_to_equity": {"type": "max", "default": 1.0},
        "quick_ratio": {"type": "min", "default": 1.0},
        "operating_margin": {"type": "min", "default": 0},
    }


def apply_filter(value: Optional[float], filter_type: str, threshold: float, data_format: str = "ratio") -> bool:
    """
    Check if a single value passes a filter.

    Handles unit conversion:
    - "ratio": value and threshold are in the same units (e.g., P/E of 15 vs threshold 50)
    - "percent_as_decimal": data is stored as decimal (0.22 = 22%), slider shows percentage.
      We convert the threshold: slider shows 20 → compare against 0.20

    Args:
        value: The stock's metric value (None if data unavailable)
        filter_type: "min" (value must be >= threshold) or "max" (value must be <= threshold)
        threshold: The filter threshold (in display units — percentage for sliders)
        data_format: "ratio" or "percent_as_decimal"

    Returns:
        True if passes, False if fails or data missing
    """
    if value is None:
        return False  # Conservative: missing data = fail

    # Convert threshold to match data units
    effective_threshold = threshold
    if data_format == "percent_as_decimal":
        # Slider shows 20 (meaning 20%), data stores 0.20
        effective_threshold = threshold / 100.0

    if filter_type == "max":
        return value <= effective_threshold
    elif filter_type == "min":
        return value >= effective_threshold
    else:
        return False


def calculate_fundamental_score(stock: dict, filters: dict) -> float:
    """
    Calculate how STRONGLY a stock passes the filters (0-100 scale).

    A stock that barely passes every filter scores 0.
    A stock that crushes every filter scores 100.

    Scoring method:
    - For each filter, score 0-1: 0 = exactly at threshold, 1 = best possible value
    - Average all per-filter scores × 100 = final fundamental score (0-100)

    This score is used for ranking — which value stocks are the MOST compelling?
    """
    scores = []

    for filter_name, filter_config in filters.items():
        value = stock.get(filter_name)
        if value is None:
            continue  # Skip filters where data isn't available

        threshold = filter_config["default"]
        filter_type = filter_config["type"]
        filter_min = filter_config.get("min", 0)
        filter_max = filter_config.get("max", 100)
        data_format = filter_config.get("data_format", "ratio")

        # Convert percent_as_decimal: data stores 0.15 (15%), config uses 15
        effective_value = value * 100 if data_format == "percent_as_decimal" else value

        # Calculate how far beyond the threshold this stock is (0 to 1)
        if filter_type == "max":
            # Lower is better.
            # At threshold = 0 (barely passing), at filter_min = 1.0 (best)
            if threshold == filter_min:
                score = 1.0 if effective_value <= threshold else 0.0
            else:
                score = max(0.0, min(1.0, (threshold - effective_value) / (threshold - filter_min)))

        elif filter_type == "min":
            # Higher is better.
            # At threshold = 0 (barely passing), at filter_max = 1.0 (best)
            if filter_max == threshold:
                score = 1.0 if effective_value >= threshold else 0.0
            else:
                score = max(0.0, min(1.0, (effective_value - threshold) / (filter_max - threshold)))
        else:
            continue

        scores.append(score)

    if not scores:
        return 0.0

    # Average per-filter scores (each 0-1) and scale to 0-100
    return round((sum(scores) / len(scores)) * 100, 1)


def screen_stock(stock: dict, filters: dict, thresholds: Optional[dict] = None, is_prescreen: bool = False) -> dict:
    """
    Screen a single stock against all filters.

    RULE: Stocks missing data for ANY filter FAIL that filter.
    All filters must be evaluable for a stock to pass.
    No skipping — if we don't have the data, the stock doesn't qualify.

    Exception: The 'sentiment_score' filter is skipped ONLY on the pre-screen
    (Step 2) since sentiment hasn't been calculated yet at that point.
    It will be evaluated in the pipeline's final scoring step.

    Args:
        stock: Dict with fundamental data
        filters: Filter config from screener-filters.json
        thresholds: Optional override thresholds (for slider exploration)

    Returns:
        Dict with passes_screen, fundamental_score, filter_results
    """
    filter_results = {}
    evaluated_count = 0
    passed_count = 0

    for filter_name, filter_config in filters.items():
        threshold = (thresholds or {}).get(filter_name, filter_config["default"])
        filter_type = filter_config["type"]
        value = stock.get(filter_name)
        data_format = filter_config.get("data_format", "ratio")

        if value is None:
            # Missing data handling depends on screening mode:
            # - Pre-screen (Step 2): skip filters that need enrichment data (price-based)
            # - Full screen (Step 4): FAIL on any missing data (stock must prove it qualifies)
            # Sentiment is always skippable (calculated later in pipeline)
            is_prescreen = is_prescreen  # Use the explicit flag, not data inference
            # Filters that require enrichment data (not available during pre-screen)
            enrichment_dependent_filters = {"pe_ratio", "forward_pe", "peg_ratio", "price_to_fcf",
                                            "est_lt_growth", "target_price_upside",
                                            "institutional_transactions", "analyst_recommendation"}

            if filter_name == "sentiment_score":
                # Always skip sentiment — calculated in Step 6
                filter_results[filter_name] = {
                    "value": None, "threshold": threshold, "type": filter_type,
                    "passes": None, "skipped": True,
                }
                continue
            elif filter_config.get("deferred"):
                # Deferred filters: data source not yet available (e.g., paid API needed)
                # Skip without penalty. Easy to re-enable: remove "deferred" from config.
                filter_results[filter_name] = {
                    "value": None, "threshold": threshold, "type": filter_type,
                    "passes": None, "skipped": True, "reason": "deferred",
                }
                continue
            elif is_prescreen and filter_name in enrichment_dependent_filters:
                # Pre-screen: skip price-dependent filters (not available yet)
                filter_results[filter_name] = {
                    "value": None, "threshold": threshold, "type": filter_type,
                    "passes": None, "skipped": True,
                }
                continue
            else:
                # Full screen: missing data = FAIL
                filter_results[filter_name] = {
                    "value": None, "threshold": threshold, "type": filter_type,
                    "passes": False, "skipped": False,
                }
                evaluated_count += 1
                continue

        passes = apply_filter(value, filter_type, threshold, data_format)

        # INDUSTRY-RELATIVE P/E: Use industry lower quartile instead of hardcoded threshold.
        # A stock passes P/E if it's cheaper than 75% of companies in its industry.
        if filter_name == "pe_ratio":
            industry_q1 = stock.get("_pe_industry_q1")
            if industry_q1 is not None and value is not None:
                passes = value > 0 and value < industry_q1
                threshold = industry_q1  # Show actual industry threshold used

        # CONDITIONAL OVERRIDE: Debt/Equity can be overridden by Interest Coverage Ratio.
        # A company with high D/E but strong ability to service its debt (ICR > 3.0)
        # is fundamentally different from one that's overleveraged and struggling.
        if filter_name == "debt_to_equity" and not passes:
            icr = stock.get("interest_coverage_ratio")
            if icr is not None and icr > 3.0:
                passes = True  # Override: company earns 3x+ its interest payments
                filter_results[filter_name] = {
                    "value": value,
                    "threshold": threshold,
                    "type": filter_type,
                    "passes": True,
                    "skipped": False,
                    "override": "interest_coverage_ratio",
                    "override_value": round(icr, 2),
                    "override_reason": f"D/E {value:.2f} exceeds {threshold}, but ICR of {icr:.1f}x proves debt is serviceable",
                }
                evaluated_count += 1
                passed_count += 1
                continue

        filter_results[filter_name] = {
            "value": value,
            "threshold": threshold,
            "type": filter_type,
            "passes": passes,
            "skipped": False,
        }

        evaluated_count += 1
        if passes:
            passed_count += 1

    # A stock passes if it passes ALL evaluated filters
    # Must have at least 3 evaluated filters to be meaningful
    passes_screen = evaluated_count >= 3 and passed_count == evaluated_count

    # Calculate fundamental score
    score = calculate_fundamental_score(stock, filters)

    return {
        **stock,
        "passes_screen": passes_screen,
        "fundamental_score": score,
        "filter_results": filter_results,
        "filters_passed": passed_count,
        "filters_evaluated": evaluated_count,
        "filters_skipped": len(filter_results) - evaluated_count,
        "filters_total": len(filter_results),
    }


def compute_and_persist_industry_medians(stocks: list[dict]):
    """
    Compute TRUE industry medians from the full 5,097-stock universe.

    Architecture: "Static Reference Map" pattern.
    1. Load ticker_industry_map.json from S3 (200KB, maps every ticker to SIC industry)
    2. Join industry labels to all stocks in memory
    3. Compute median for each metric per industry
    4. Persist to DynamoDB as INDUSTRY_AVG#{industry} items

    This runs once per pipeline execution during pre-screen (Step 2).
    Uses the full unfiltered dataset — not just the 50-80 survivors.
    Zero additional API calls. ~15ms compute time.
    """
    import boto3
    from datetime import datetime, timezone
    from decimal import Decimal
    from collections import defaultdict

    bucket = os.environ.get("RAW_DATA_BUCKET", "")
    table_name = os.environ.get("DATA_TABLE_NAME", "")

    if not bucket or not table_name:
        print("  Warning: RAW_DATA_BUCKET or DATA_TABLE_NAME not set — skipping industry medians")
        return

    # Step 1: Load the static industry map from S3
    try:
        s3 = boto3.client("s3")
        resp = s3.get_object(Bucket=bucket, Key="reference/ticker_industry_map.json")
        industry_map = json.loads(resp["Body"].read().decode("utf-8"))
        print(f"  Loaded industry map: {len(industry_map)} tickers")
    except Exception as e:
        print(f"  Warning: Could not load industry map from S3: {e}")
        return

    # Step 2: Join industry to all stocks in memory
    metrics_to_compute = [
        "pe_ratio", "debt_to_equity", "quick_ratio", "operating_margin",
        "eps_growth_yoy", "revenue_growth_yoy",
    ]

    # Group metric values by industry
    industry_data: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    mapped_count = 0

    for stock in stocks:
        symbol = stock.get("symbol", "")
        entry = industry_map.get(symbol)
        if not entry:
            continue
        industry = entry.get("industry", "")
        if not industry:
            continue

        mapped_count += 1
        for metric in metrics_to_compute:
            val = stock.get(metric)
            if val is not None and isinstance(val, (int, float)):
                industry_data[industry][metric].append(val)

    print(f"  Mapped {mapped_count}/{len(stocks)} stocks to industries, "
          f"{len(industry_data)} unique industries")

    # Step 3: Compute medians (require at least 5 data points for statistical relevance)
    def median(values):
        s = sorted(values)
        n = len(s)
        mid = n // 2
        return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2

    industry_medians = {}
    for industry, metrics in industry_data.items():
        avgs = {}
        sample_size = 0
        for metric, values in metrics.items():
            if len(values) >= 5:
                avgs[metric] = round(median(values), 4)
                sample_size = max(sample_size, len(values))
        if avgs:
            avgs["sample_size"] = sample_size
            industry_medians[industry] = avgs

    print(f"  Computed medians for {len(industry_medians)} industries (min 5 stocks each)")

    # Step 4: Persist to DynamoDB
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(table_name)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now_iso = datetime.now(timezone.utc).isoformat()

    def to_decimal(val):
        if isinstance(val, float):
            return Decimal(str(round(val, 6)))
        return val

    with table.batch_writer() as batch:
        for industry, metrics in industry_medians.items():
            item = {
                "PK": f"INDUSTRY_AVG#{industry}",
                "SK": "METRICS",
                "industry": industry,
                "updated_date": today,
                "last_updated": now_iso,
            }
            for k, v in metrics.items():
                item[k] = to_decimal(v) if isinstance(v, float) else v
            batch.put_item(Item=item)

    print(f"  Persisted {len(industry_medians)} industry averages to DynamoDB")


def handler(event, context):
    """
    Lambda entry point. Called by Step Functions after fundamentals-fetcher.

    Input event:
        event["stocks"] — list of stock dicts from fundamentals-fetcher
        event["thresholds"] — (optional) custom filter thresholds for exploration

    Output:
        - passing_stocks: stocks that pass ALL filters (sorted by score)
        - near_misses: stocks that fail 1-2 filters (potential future passers)
        - all_screened: every stock with pass/fail status + score
        - metadata: summary stats
    """
    stocks = event.get("stocks") or event.get("enriched_stocks") or []
    custom_thresholds = event.get("thresholds")  # Optional: for slider exploration
    is_prescreen_mode = event.get("prescreen", False)  # Explicit flag from Step Functions

    if not stocks:
        # Try reading from S3 (pipeline passes s3_key between steps)
        from pipeline_io import read_pipeline_input
        pipeline_data = read_pipeline_input(event)
        stocks = pipeline_data.get("stocks") or pipeline_data.get("enriched_stocks") or []
        custom_thresholds = pipeline_data.get("thresholds") or custom_thresholds
        is_prescreen_mode = pipeline_data.get("prescreen", False)

    if not stocks:
        return {
            "passing_stocks": [],
            "near_misses": [],
            "metadata": {"error": "No stocks provided in event['stocks']"},
        }

    print(f"Screening {len(stocks)} stocks...")

    # Load filter configuration
    filters = load_default_filters()
    print(f"Loaded {len(filters)} filters")

    if custom_thresholds:
        print(f"Using custom thresholds for {len(custom_thresholds)} filters")

    # Screen each stock
    screened = [screen_stock(stock, filters, custom_thresholds, is_prescreen_mode) for stock in stocks]

    # Categorize results
    passing = [s for s in screened if s["passes_screen"]]
    # Near misses: fail only 1-2 of the evaluated filters
    near_misses = [
        s for s in screened
        if not s["passes_screen"]
        and s["filters_evaluated"] > 0
        and (s["filters_evaluated"] - s["filters_passed"]) <= 2
    ]

    # Sort by fundamental score (best first)
    passing.sort(key=lambda s: s["fundamental_score"], reverse=True)
    near_misses.sort(key=lambda s: s["fundamental_score"], reverse=True)

    print(f"Results: {len(passing)} passing, {len(near_misses)} near-misses, "
          f"{len(stocks) - len(passing) - len(near_misses)} rejected")

    result = {
        "passing_stocks": passing,
        "near_misses": near_misses,
        "all_screened": screened,
        "metadata": {
            "total_screened": len(stocks),
            "passing_count": len(passing),
            "near_miss_count": len(near_misses),
            "rejected_count": len(stocks) - len(passing) - len(near_misses),
            "filters_applied": len(filters),
            "custom_thresholds_used": custom_thresholds is not None,
        },
    }

    # Write to S3 for next step (avoids Step Functions 256KB limit)
    from pipeline_io import write_pipeline_output
    step_name = "step2_prescreen" if is_prescreen_mode else "step4_fullscreen"

    # When running as pre-screen (Step 2), compute and persist industry medians
    # from the FULL 5,097-stock universe before filtering narrows the pool.
    if is_prescreen_mode:
        compute_and_persist_industry_medians(stocks)

    return write_pipeline_output(result, step_name=step_name)
