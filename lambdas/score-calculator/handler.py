"""
Score Calculator Lambda
========================
Step 5 in the pipeline.

Combines the fundamental score (from Step 2: stock-screener) with the
sentiment score (from Step 4: sentiment-analyzer) into a single
**Investability Score** per stock.

Also persists results to DynamoDB:
- LATEST item: current scores (overwritten each run)
- SCORE#date item: historical score (appended daily, never overwritten)
- TRACKING item: tracking status (ACTIVE if passes screen)

This is what enables retroactive analysis — every day's scores are preserved
so you can look back and see how a stock's investability changed over time.

Environment Variables:
    FUNDAMENTAL_WEIGHT - Weight for fundamental score (default: 0.7)
    SENTIMENT_WEIGHT   - Weight for sentiment adjustment (default: 0.3)
    DATA_TABLE_NAME    - DynamoDB table for persistence
"""

import json
import os
from datetime import datetime, timezone
from decimal import Decimal

import boto3

# DynamoDB client
dynamodb = boto3.resource("dynamodb")

# Risk flag penalties — severe issues get hard score reductions
RISK_FLAG_PENALTIES = {
    "SEC_investigation": -30,
    "fraud_allegation": -35,
    "accounting_irregularity": -25,
    "lawsuit": -10,
    "regulatory_risk": -15,
    "management_departure": -10,
    "product_recall": -10,
}


def calculate_investability_score(stock: dict) -> dict:
    """
    Calculate the final Investability Score for a single stock.

    Combines:
    1. Fundamental score (0-100) — how well it passes value filters
    2. Sentiment score (-1 to +1) — news/market perception
    3. Risk flag penalties — hard deductions for serious issues
    """
    fundamental_score = stock.get("fundamental_score", 0.0)
    sentiment_data = stock.get("sentiment", {})
    sentiment_score = sentiment_data.get("sentiment_score", 0.0)
    sentiment_confidence = sentiment_data.get("confidence", 0.0)
    risk_flags = sentiment_data.get("risk_flags", [])

    w_fundamental = float(os.environ.get("FUNDAMENTAL_WEIGHT", "0.7"))
    w_sentiment = float(os.environ.get("SENTIMENT_WEIGHT", "0.3"))

    max_sentiment_bonus = 25.0
    sentiment_adjustment = sentiment_score * max_sentiment_bonus * sentiment_confidence
    base_score = (w_fundamental * fundamental_score) + (w_sentiment * sentiment_adjustment)

    total_penalty = 0
    applied_penalties = []
    for flag in risk_flags:
        penalty = RISK_FLAG_PENALTIES.get(flag, -5)
        total_penalty += penalty
        applied_penalties.append({"flag": flag, "penalty": penalty})

    final_score = max(0.0, min(100.0, base_score + total_penalty))

    return {
        **stock,
        "investability_score": round(final_score, 1),
        "score_breakdown": {
            "fundamental_score": fundamental_score,
            "fundamental_weighted": round(w_fundamental * fundamental_score, 1),
            "sentiment_score": sentiment_score,
            "sentiment_confidence": sentiment_confidence,
            "sentiment_adjustment": round(sentiment_adjustment, 1),
            "sentiment_weighted": round(w_sentiment * sentiment_adjustment, 1),
            "risk_penalties": applied_penalties,
            "total_penalty": total_penalty,
            "base_score_before_penalty": round(base_score, 1),
        },
    }


def _to_decimal(obj):
    """
    Convert floats to Decimal for DynamoDB (DynamoDB doesn't accept float).
    Also handles None and nested structures.
    """
    if isinstance(obj, float):
        return Decimal(str(round(obj, 6)))
    elif isinstance(obj, dict):
        return {k: _to_decimal(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_to_decimal(i) for i in obj]
    return obj


def persist_to_dynamodb(scored_stocks: list, today: str):
    """
    Write scored stocks to DynamoDB.

    For each stock, writes:
    1. LATEST item — current state (overwritten each run)
       PK: STOCK#AAPL  SK: LATEST
       Contains: all current scores, fundamentals, sentiment summary

    2. SCORE#date item — historical record (one per day)
       PK: STOCK#AAPL  SK: SCORE#2026-07-12
       Contains: scores + key metrics for that day

    3. TRACKING item — tracking status
       PK: STOCK#AAPL  SK: TRACKING
       Contains: status (ACTIVE/GRACE), first_tracked, last_passed

    This is the data that powers:
    - The dashboard (reads LATEST items)
    - Trend charts (reads SCORE# items over a date range)
    - The tracked stocks list (queries GSI on tracking_status)
    """
    table_name = os.environ.get("DATA_TABLE_NAME")
    if not table_name:
        print("  Warning: DATA_TABLE_NAME not set — skipping persistence")
        return

    table = dynamodb.Table(table_name)
    written = 0

    with table.batch_writer() as batch:
        for stock in scored_stocks:
            symbol = stock.get("symbol", "")
            if not symbol:
                continue

            now_iso = datetime.now(timezone.utc).isoformat()

            # Item 1: LATEST (current state — overwritten each run)
            # Persist ALL screener metrics so the dashboard can display them
            latest_item = {
                "PK": f"STOCK#{symbol}",
                "SK": "LATEST",
                "symbol": symbol,
                "company_name": stock.get("company_name", ""),
                "sector": stock.get("sector", ""),
                "industry": stock.get("industry", ""),
                "price": stock.get("price"),
                "market_cap": stock.get("market_cap"),
                "investability_score": stock.get("investability_score"),
                "fundamental_score": stock.get("fundamental_score"),
                "sentiment_score": stock.get("sentiment", {}).get("sentiment_score"),
                "sentiment_confidence": stock.get("sentiment", {}).get("confidence"),
                "risk_flags": stock.get("sentiment", {}).get("risk_flags", []),
                "passes_screen": stock.get("passes_screen", False),
                # All screener filter metrics (must match screener-filters.json keys)
                "pe_ratio": stock.get("pe_ratio"),
                "forward_pe": stock.get("forward_pe"),
                "peg_ratio": stock.get("peg_ratio"),
                "price_to_fcf": stock.get("price_to_fcf"),
                "debt_to_equity": stock.get("debt_to_equity"),
                "quick_ratio": stock.get("quick_ratio"),
                "operating_margin": stock.get("operating_margin"),
                "eps_growth_yoy": stock.get("eps_growth_yoy"),
                "revenue_growth_yoy": stock.get("revenue_growth_yoy"),
                "est_lt_growth": stock.get("est_lt_growth"),
                "analyst_recommendation": stock.get("analyst_recommendation"),
                "target_price_upside": stock.get("target_price_upside"),
                "institutional_transactions": stock.get("institutional_transactions"),
                "last_updated": now_iso,
                # GSI attributes for querying by tracking status
                "tracking_status": "ACTIVE" if stock.get("passes_screen") else "GRACE",
            }
            batch.put_item(Item=_to_decimal(
                {k: v for k, v in latest_item.items() if v is not None}
            ))

            # Item 2: Historical score (one per day — never overwritten)
            score_item = {
                "PK": f"STOCK#{symbol}",
                "SK": f"SCORE#{today}",
                "symbol": symbol,
                "date": today,
                "investability_score": stock.get("investability_score"),
                "fundamental_score": stock.get("fundamental_score"),
                "sentiment_score": stock.get("sentiment", {}).get("sentiment_score"),
                "price": stock.get("price"),
                "pe_ratio": stock.get("pe_ratio"),
                "debt_to_equity": stock.get("debt_to_equity"),
                "risk_flags": stock.get("sentiment", {}).get("risk_flags", []),
                "last_updated": now_iso,
            }
            batch.put_item(Item=_to_decimal(
                {k: v for k, v in score_item.items() if v is not None}
            ))

            # Item 3: Tracking status
            tracking_item = {
                "PK": f"STOCK#{symbol}",
                "SK": "TRACKING",
                "symbol": symbol,
                "tracking_status": "ACTIVE" if stock.get("passes_screen") else "GRACE",
                "last_passed": today if stock.get("passes_screen") else None,
                "last_updated": now_iso,
                # GSI attributes
            }
            batch.put_item(Item=_to_decimal(
                {k: v for k, v in tracking_item.items() if v is not None}
            ))

            written += 1

    print(f"  Persisted {written} stocks to DynamoDB ({written * 3} items)")


def handler(event, context):
    """
    Lambda entry point. Called by Step Functions after sentiment-analyzer.

    Input event:
        event["stocks_with_sentiment"] — stocks with fundamental + sentiment data

    Output:
        Ranked list of stocks with investability scores.
    """
    from pipeline_io import read_pipeline_input, write_pipeline_output

    start_time = datetime.now(timezone.utc)
    today = start_time.strftime("%Y-%m-%d")
    print(f"Starting score calculation at {start_time.isoformat()}")

    # Read input from S3 if needed (Step Functions payload limit workaround)
    data = read_pipeline_input(event)

    stocks = data.get("stocks_with_sentiment", [])
    if not stocks:
        return {
            "scored_stocks": [],
            "metadata": {"error": "No stocks provided"},
        }

    print(f"Calculating investability scores for {len(stocks)} stocks...")

    # Calculate scores
    scored = [calculate_investability_score(stock) for stock in stocks]
    scored.sort(key=lambda s: s["investability_score"], reverse=True)

    # Categorize
    highly_investable = [s for s in scored if s["investability_score"] >= 70]
    moderately_investable = [s for s in scored if 40 <= s["investability_score"] < 70]
    low_investability = [s for s in scored if s["investability_score"] < 40]

    # Persist to DynamoDB
    persist_to_dynamodb(scored, today)

    # Log top results
    for s in scored[:5]:
        print(f"  {s['symbol']}: investability={s['investability_score']}, "
              f"fundamental={s.get('fundamental_score', 0)}, "
              f"sentiment={s.get('sentiment', {}).get('sentiment_score', 'N/A')}")

    end_time = datetime.now(timezone.utc)

    result = {
        "scored_stocks": scored,
        "summary": {
            "highly_investable": [s["symbol"] for s in highly_investable],
            "moderately_investable": [s["symbol"] for s in moderately_investable],
            "low_investability": [s["symbol"] for s in low_investability],
        },
        "metadata": {
            "total_scored": len(scored),
            "highly_investable_count": len(highly_investable),
            "moderately_investable_count": len(moderately_investable),
            "low_investability_count": len(low_investability),
            "persisted_to_dynamodb": True,
            "weights": {
                "fundamental": float(os.environ.get("FUNDAMENTAL_WEIGHT", "0.7")),
                "sentiment": float(os.environ.get("SENTIMENT_WEIGHT", "0.3")),
            },
            "duration_seconds": (end_time - start_time).total_seconds(),
            "timestamp": end_time.isoformat(),
        },
    }

    print(f"Done. {len(highly_investable)} highly investable, "
          f"{len(moderately_investable)} moderate, "
          f"{len(low_investability)} low.")

    return write_pipeline_output(result, step_name="step7_scores")
