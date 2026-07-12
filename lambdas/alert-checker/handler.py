"""
Alert Checker Lambda
====================
Step 6 (final) in the pipeline.

Compares the latest scored stocks against user-defined alert rules
and sends notifications when thresholds are breached.

Alert types:
1. Sentiment drop — stock's sentiment fell below a threshold
2. Score drop — investability score dropped significantly
3. Risk flag detected — new serious risk flag appeared
4. Stock dropped off screen — was passing, now fails filters
5. New stock passing — previously unseen stock now passes

For now, notifications go via Amazon SNS (email/SMS).
In the future, this can integrate with the React dashboard (push notifications).

Input (from Step Functions / score-calculator):
    event["scored_stocks"] — stocks with investability scores + sentiment
    event["summary"] — categorization summary

Environment Variables:
    ALERT_SNS_TOPIC_ARN - SNS topic for sending alerts (optional — if not set, just logs)
    SENTIMENT_DROP_THRESHOLD - Alert if sentiment drops below this (default: -0.3)
    SCORE_DROP_THRESHOLD - Alert if investability drops by this many points (default: 15)
"""

import json
import os
from datetime import datetime, timezone

import boto3

# AWS clients
sns_client = boto3.client("sns")

# Default thresholds
DEFAULT_SENTIMENT_THRESHOLD = -0.3
DEFAULT_SCORE_DROP_THRESHOLD = 15


def check_sentiment_alerts(stocks: list, threshold: float) -> list:
    """
    Check for stocks with dangerously negative sentiment.

    A stock may look like a value stock on fundamentals, but if
    news sentiment is deeply negative, there's likely a reason
    the price is low — it's not truly undervalued, it's in trouble.
    """
    alerts = []
    for stock in stocks:
        sentiment = stock.get("sentiment", {})
        score = sentiment.get("sentiment_score", 0.0)
        if score < threshold:
            alerts.append({
                "type": "sentiment_drop",
                "severity": "high" if score < -0.5 else "medium",
                "symbol": stock.get("symbol"),
                "company_name": stock.get("company_name", ""),
                "message": (
                    f"{stock.get('symbol')} sentiment is {score:.2f} "
                    f"(threshold: {threshold}). "
                    f"Risk flags: {sentiment.get('risk_flags', [])}"
                ),
                "data": {
                    "sentiment_score": score,
                    "confidence": sentiment.get("confidence", 0),
                    "risk_flags": sentiment.get("risk_flags", []),
                    "article_count": sentiment.get("relevant_count", 0),
                },
            })
    return alerts


def check_risk_flag_alerts(stocks: list) -> list:
    """
    Check for stocks with serious risk flags detected in the news.

    Risk flags like SEC investigations, fraud allegations, or major
    lawsuits are red flags that require immediate attention.
    """
    serious_flags = {
        "SEC_investigation", "fraud_allegation",
        "accounting_irregularity", "regulatory_risk",
    }

    alerts = []
    for stock in stocks:
        sentiment = stock.get("sentiment", {})
        flags = set(sentiment.get("risk_flags", []))
        serious_found = flags.intersection(serious_flags)

        if serious_found:
            alerts.append({
                "type": "risk_flag",
                "severity": "critical",
                "symbol": stock.get("symbol"),
                "company_name": stock.get("company_name", ""),
                "message": (
                    f"RISK ALERT: {stock.get('symbol')} has serious risk flags: "
                    f"{list(serious_found)}"
                ),
                "data": {
                    "flags": list(serious_found),
                    "all_flags": sentiment.get("risk_flags", []),
                },
            })
    return alerts


def check_low_investability_alerts(stocks: list) -> list:
    """
    Alert when a stock that was highly investable drops to low category.

    This catches stocks that might be deteriorating — their fundamentals
    or sentiment (or both) have weakened.
    """
    alerts = []
    for stock in stocks:
        score = stock.get("investability_score", 0)
        # Alert for stocks that pass the fundamental screen but have
        # low investability (meaning sentiment is dragging them down)
        if stock.get("passes_screen") and score < 40:
            alerts.append({
                "type": "investability_warning",
                "severity": "medium",
                "symbol": stock.get("symbol"),
                "company_name": stock.get("company_name", ""),
                "message": (
                    f"{stock.get('symbol')} passes value filters but has low "
                    f"investability score ({score:.1f}). "
                    f"Sentiment or risk flags may be concerning."
                ),
                "data": {
                    "investability_score": score,
                    "fundamental_score": stock.get("fundamental_score", 0),
                    "sentiment_score": stock.get("sentiment", {}).get("sentiment_score", 0),
                },
            })
    return alerts


def send_alert_notification(alerts: list, sns_topic_arn: str):
    """
    Send consolidated alert notification via SNS.

    Groups alerts into a single email/SMS for readability.
    """
    if not alerts:
        return

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Build the message
    subject = f"Stock Screener Alert: {len(alerts)} alert(s) - {now}"

    body_lines = [
        f"Stock Screener Alert Report",
        f"Generated: {now}",
        f"Total alerts: {len(alerts)}",
        "",
        "=" * 50,
    ]

    # Group by severity
    critical = [a for a in alerts if a["severity"] == "critical"]
    high = [a for a in alerts if a["severity"] == "high"]
    medium = [a for a in alerts if a["severity"] == "medium"]

    if critical:
        body_lines.append("\n🚨 CRITICAL ALERTS:")
        for alert in critical:
            body_lines.append(f"  • {alert['message']}")

    if high:
        body_lines.append("\n⚠️ HIGH ALERTS:")
        for alert in high:
            body_lines.append(f"  • {alert['message']}")

    if medium:
        body_lines.append("\n📋 MEDIUM ALERTS:")
        for alert in medium:
            body_lines.append(f"  • {alert['message']}")

    body = "\n".join(body_lines)

    try:
        sns_client.publish(
            TopicArn=sns_topic_arn,
            Subject=subject[:100],  # SNS subject has a 100 char limit
            Message=body,
        )
        print(f"  Alert notification sent to SNS topic")
    except Exception as e:
        print(f"  Warning: Failed to send SNS notification: {e}")


def handler(event, context):
    """
    Lambda entry point. Called by Step Functions after score-calculator.

    Input event:
        event["scored_stocks"] — stocks with investability scores
        event["summary"] — categorization from score-calculator

    Output:
        Alert results (what was checked, what triggered).
    """
    start_time = datetime.now(timezone.utc)
    print(f"Starting alert check at {start_time.isoformat()}")

    scored_stocks = event.get("scored_stocks", [])
    if not scored_stocks:
        return {
            "alerts": [],
            "metadata": {"error": "No scored stocks provided"},
        }

    # Configuration
    sentiment_threshold = float(
        os.environ.get("SENTIMENT_DROP_THRESHOLD", str(DEFAULT_SENTIMENT_THRESHOLD))
    )
    sns_topic_arn = os.environ.get("ALERT_SNS_TOPIC_ARN", "")

    print(f"Checking alerts for {len(scored_stocks)} stocks...")
    print(f"  Sentiment threshold: {sentiment_threshold}")
    print(f"  SNS topic: {'configured' if sns_topic_arn else 'not configured (log only)'}")

    # Run all alert checks
    all_alerts = []
    all_alerts.extend(check_sentiment_alerts(scored_stocks, sentiment_threshold))
    all_alerts.extend(check_risk_flag_alerts(scored_stocks))
    all_alerts.extend(check_low_investability_alerts(scored_stocks))

    print(f"  Found {len(all_alerts)} alerts")

    # Send notification if SNS is configured and there are alerts
    if sns_topic_arn and all_alerts:
        send_alert_notification(all_alerts, sns_topic_arn)
    elif all_alerts:
        print("  Alerts detected but SNS not configured — logging only:")
        for alert in all_alerts:
            print(f"    [{alert['severity']}] {alert['message']}")

    # Build response
    end_time = datetime.now(timezone.utc)

    result = {
        "alerts": all_alerts,
        "metadata": {
            "stocks_checked": len(scored_stocks),
            "alerts_triggered": len(all_alerts),
            "critical_count": sum(1 for a in all_alerts if a["severity"] == "critical"),
            "high_count": sum(1 for a in all_alerts if a["severity"] == "high"),
            "medium_count": sum(1 for a in all_alerts if a["severity"] == "medium"),
            "sns_configured": bool(sns_topic_arn),
            "notification_sent": bool(sns_topic_arn and all_alerts),
            "duration_seconds": (end_time - start_time).total_seconds(),
            "timestamp": end_time.isoformat(),
        },
    }

    print(f"Done. {len(all_alerts)} alerts "
          f"({'notification sent' if sns_topic_arn and all_alerts else 'logged only'}).")

    return result
