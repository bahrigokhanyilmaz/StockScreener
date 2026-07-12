"""
Score Calculator Lambda
========================
Step 5 in the pipeline.

Combines the fundamental score (from Step 2: stock-screener) with the
sentiment score (from Step 4: sentiment-analyzer) into a single
**Investability Score** per stock.

The Investability Score answers: "Is this stock genuinely undervalued,
or is there a reason (bad news, declining fundamentals) for its low price?"

Formula:
    investability = (w1 × fundamental_score) + (w2 × sentiment_adjustment)

    Where:
    - fundamental_score: 0-100 from the screener (how well it passes value filters)
    - sentiment_adjustment: -25 to +25 bonus/penalty based on news sentiment
    - Risk flags can apply a hard penalty (e.g., fraud allegation = -30)

Weights are configurable and will be tunable in the UI.

Input (from Step Functions / sentiment-analyzer):
    event["stocks_with_sentiment"] — stocks with fundamental data + sentiment scores

Output:
    Stocks with final investability scores, ranked.

Environment Variables:
    FUNDAMENTAL_WEIGHT - Weight for fundamental score (default: 0.7)
    SENTIMENT_WEIGHT   - Weight for sentiment adjustment (default: 0.3)
"""

import json
import os
from datetime import datetime, timezone


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

    Returns the stock dict with investability_score added.
    """
    fundamental_score = stock.get("fundamental_score", 0.0)
    sentiment_data = stock.get("sentiment", {})
    sentiment_score = sentiment_data.get("sentiment_score", 0.0)
    sentiment_confidence = sentiment_data.get("confidence", 0.0)
    risk_flags = sentiment_data.get("risk_flags", [])

    # Weights (configurable via env vars)
    w_fundamental = float(os.environ.get("FUNDAMENTAL_WEIGHT", "0.7"))
    w_sentiment = float(os.environ.get("SENTIMENT_WEIGHT", "0.3"))

    # Sentiment adjustment: maps (-1, +1) range to (-25, +25) bonus points
    # Scaled by confidence — low-confidence sentiment has less impact
    max_sentiment_bonus = 25.0
    sentiment_adjustment = sentiment_score * max_sentiment_bonus * sentiment_confidence

    # Base score: weighted combination
    base_score = (w_fundamental * fundamental_score) + (w_sentiment * sentiment_adjustment)

    # Risk flag penalties
    total_penalty = 0
    applied_penalties = []
    for flag in risk_flags:
        penalty = RISK_FLAG_PENALTIES.get(flag, -5)  # Default -5 for unknown flags
        total_penalty += penalty
        applied_penalties.append({"flag": flag, "penalty": penalty})

    # Final score (clamped to 0-100)
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


def handler(event, context):
    """
    Lambda entry point. Called by Step Functions after sentiment-analyzer.

    Input event:
        event["stocks_with_sentiment"] — stocks with fundamental + sentiment data

    Output:
        Ranked list of stocks with investability scores.
    """
    start_time = datetime.now(timezone.utc)
    print(f"Starting score calculation at {start_time.isoformat()}")

    stocks = event.get("stocks_with_sentiment", [])
    if not stocks:
        return {
            "scored_stocks": [],
            "metadata": {"error": "No stocks provided"},
        }

    print(f"Calculating investability scores for {len(stocks)} stocks...")

    # Calculate scores for each stock
    scored = [calculate_investability_score(stock) for stock in stocks]

    # Rank by investability score (highest first)
    scored.sort(key=lambda s: s["investability_score"], reverse=True)

    # Categorize by investability
    highly_investable = [s for s in scored if s["investability_score"] >= 70]
    moderately_investable = [s for s in scored if 40 <= s["investability_score"] < 70]
    low_investability = [s for s in scored if s["investability_score"] < 40]

    # Summary
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

    return result
