"""
Stock Screener Lambda
=====================
Step 2 in the pipeline.

Takes the enriched fundamental data from Step 1 (fundamentals-fetcher)
and applies value investing filter criteria to identify passing stocks.

For each stock that passes ALL filters, calculates a "fundamental score"
representing HOW STRONGLY it passes (a stock barely passing vs. one
that crushes every metric should be distinguished).

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

    A stock that barely passes every filter gets a low score.
    A stock that crushes every filter gets a high score.

    Scoring method:
    - For each filter, calculate how far beyond the threshold the stock is
    - Normalize each to a 0-1 scale based on realistic ranges
    - Average all individual scores → final fundamental score (0-100)

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

        # Calculate how far beyond the threshold this stock is
        if filter_type == "max":
            # Lower is better. Score = how far below threshold (normalized)
            # Best case: value = filter_min, Score = 1.0
            # Threshold case: value = threshold, Score = 0.5
            # Range: filter_min to threshold (maps to 1.0 to 0.5)
            if threshold == filter_min:
                score = 1.0 if value <= threshold else 0.0
            else:
                # How much room between filter_min and threshold did we use?
                score = max(0.0, min(1.0, (threshold - value) / (threshold - filter_min) * 0.5 + 0.5))

        elif filter_type == "min":
            # Higher is better. Score = how far above threshold (normalized)
            # Threshold case: value = threshold, Score = 0.5
            # Best case: value = filter_max, Score = 1.0
            if filter_max == threshold:
                score = 1.0 if value >= threshold else 0.0
            else:
                score = max(0.0, min(1.0, (value - threshold) / (filter_max - threshold) * 0.5 + 0.5))
        else:
            continue

        scores.append(score)

    if not scores:
        return 0.0

    # Average all individual filter scores, scale to 0-100
    return round((sum(scores) / len(scores)) * 100, 1)


def screen_stock(stock: dict, filters: dict, thresholds: Optional[dict] = None) -> dict:
    """
    Screen a single stock against all filters.

    A stock is evaluated ONLY on filters where data is available.
    If it passes all filters that have data, it passes the screen.
    Filters with missing data are skipped (not counted as pass or fail).

    This is important because:
    - Not all data is available on the free tier (growth, analyst targets)
    - Sentiment scores don't exist until Phase 2
    - We don't want to penalize stocks for data we haven't collected yet

    Args:
        stock: Dict with fundamental data (from fundamentals-fetcher output)
        filters: Filter config from screener-filters.json
        thresholds: Optional override thresholds (for slider exploration)

    Returns:
        Dict with:
        - "passes_screen": bool — passes all filters where data exists
        - "fundamental_score": float — 0-100 score
        - "filter_results": dict — per-filter detail
        - All original stock data preserved
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
            # No data for this filter — skip it (don't penalize)
            filter_results[filter_name] = {
                "value": None,
                "threshold": threshold,
                "type": filter_type,
                "passes": None,  # None = not evaluated (vs False = failed)
                "skipped": True,
            }
            continue

        passes = apply_filter(value, filter_type, threshold, data_format)
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

    # A stock passes if it passes ALL evaluated filters (where data exists)
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
    stocks = event.get("stocks", [])
    custom_thresholds = event.get("thresholds")  # Optional: for slider exploration

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
    screened = [screen_stock(stock, filters, custom_thresholds) for stock in stocks]

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

    return {
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
