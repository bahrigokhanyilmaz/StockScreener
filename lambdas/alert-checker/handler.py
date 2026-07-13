"""
Alert Checker Lambda
====================
Step 6 (final) in the pipeline.

Compares today's scored stocks against:
1. User-defined thresholds (sentiment, risk flags)
2. Previous scores from DynamoDB (detects drops, new passers)
3. Tracking status (manages grace period lifecycle)

Alert types:
- sentiment_drop: stock's sentiment fell below threshold
- risk_flag: serious risk flag detected in news
- investability_warning: passes filters but low investability (sentiment dragging)
- new_passing: stock passes the screen for the first time
- dropped_off: stock that was ACTIVE no longer passes

Also manages tracking lifecycle:
- New passing stocks → status = ACTIVE
- Previously passing stocks that now fail → status = GRACE, start grace timer
- Stocks in GRACE for 90+ days → removed from tracking

Environment Variables:
    ALERT_SNS_TOPIC_ARN      - SNS topic for notifications
    SENTIMENT_DROP_THRESHOLD  - Alert below this (default: -0.3)
    DATA_TABLE_NAME           - DynamoDB table
    GRACE_PERIOD_DAYS         - Days before removing from tracking (default: 90)
"""

import json
import os
from datetime import datetime, timezone, timedelta
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

# AWS clients
sns_client = boto3.client("sns")
dynamodb = boto3.resource("dynamodb")

DEFAULT_SENTIMENT_THRESHOLD = -0.3
DEFAULT_GRACE_PERIOD_DAYS = 90


def get_previous_tracking_status(table, symbols: list[str]) -> dict[str, dict]:
    """
    Read previous tracking status for stocks from DynamoDB.

    Returns dict: symbol → {tracking_status, last_passed, ...}
    This lets us detect:
    - New stocks (no previous TRACKING item)
    - Stocks that dropped off (were ACTIVE, now fail)
    - Grace period expiration
    """
    previous = {}
    for symbol in symbols:
        try:
            response = table.get_item(
                Key={"PK": f"STOCK#{symbol}", "SK": "TRACKING"}
            )
            item = response.get("Item")
            if item:
                previous[symbol] = {
                    "tracking_status": item.get("tracking_status", ""),
                    "last_passed": item.get("last_passed", ""),
                    "first_tracked": item.get("first_tracked", ""),
                }
        except Exception:
            continue
    return previous


def check_tracking_changes(scored_stocks: list, previous_status: dict, today: str) -> tuple[list, list]:
    """
    Detect stocks that are newly passing or have dropped off the screen.

    Returns:
        (alerts, tracking_updates) — alerts to send + DynamoDB updates to make
    """
    alerts = []
    tracking_updates = []
    grace_days = int(os.environ.get("GRACE_PERIOD_DAYS", str(DEFAULT_GRACE_PERIOD_DAYS)))

    for stock in scored_stocks:
        symbol = stock.get("symbol", "")
        passes = stock.get("passes_screen", False)
        prev = previous_status.get(symbol)

        if passes and (not prev or prev["tracking_status"] != "ACTIVE"):
            # New passer (either brand new or was in GRACE)
            alerts.append({
                "type": "new_passing",
                "severity": "low",
                "symbol": symbol,
                "company_name": stock.get("company_name", ""),
                "message": (
                    f"NEW: {symbol} ({stock.get('company_name', '')}) now passes your value screen. "
                    f"Score: {stock.get('investability_score', 0):.1f}"
                ),
                "data": {
                    "investability_score": stock.get("investability_score"),
                    "fundamental_score": stock.get("fundamental_score"),
                },
            })
            tracking_updates.append({
                "symbol": symbol,
                "status": "ACTIVE",
                "last_passed": today,
                "first_tracked": prev.get("first_tracked", today) if prev else today,
            })

        elif passes and prev and prev["tracking_status"] == "ACTIVE":
            # Still passing — just update last_passed
            tracking_updates.append({
                "symbol": symbol,
                "status": "ACTIVE",
                "last_passed": today,
                "first_tracked": prev.get("first_tracked", today),
            })

        elif not passes and prev and prev["tracking_status"] == "ACTIVE":
            # Dropped off — move to GRACE
            alerts.append({
                "type": "dropped_off",
                "severity": "medium",
                "symbol": symbol,
                "company_name": stock.get("company_name", ""),
                "message": (
                    f"DROPPED: {symbol} no longer passes your value screen. "
                    f"Moving to {grace_days}-day grace period."
                ),
                "data": {
                    "investability_score": stock.get("investability_score"),
                    "previous_status": "ACTIVE",
                },
            })
            tracking_updates.append({
                "symbol": symbol,
                "status": "GRACE",
                "last_passed": prev.get("last_passed", ""),
                "first_tracked": prev.get("first_tracked", today),
                "grace_start": today,
            })

        elif not passes and prev and prev["tracking_status"] == "GRACE":
            # Already in grace — check if expired
            grace_start = prev.get("last_passed", today)
            try:
                grace_start_date = datetime.strptime(grace_start, "%Y-%m-%d")
                today_date = datetime.strptime(today, "%Y-%m-%d")
                days_in_grace = (today_date - grace_start_date).days
                if days_in_grace >= grace_days:
                    tracking_updates.append({
                        "symbol": symbol,
                        "status": "EXPIRED",
                    })
                else:
                    # Still in grace, keep tracking
                    tracking_updates.append({
                        "symbol": symbol,
                        "status": "GRACE",
                        "last_passed": prev.get("last_passed", ""),
                        "first_tracked": prev.get("first_tracked", ""),
                    })
            except (ValueError, TypeError):
                pass

    return alerts, tracking_updates


def update_tracking_in_dynamodb(table, tracking_updates: list, today: str):
    """Write tracking status updates to DynamoDB."""
    now_iso = datetime.now(timezone.utc).isoformat()

    with table.batch_writer() as batch:
        for update in tracking_updates:
            symbol = update["symbol"]
            status = update["status"]

            if status == "EXPIRED":
                # Delete tracking item (stock is no longer tracked)
                table.delete_item(Key={"PK": f"STOCK#{symbol}", "SK": "TRACKING"})
                continue

            item = {
                "PK": f"STOCK#{symbol}",
                "SK": "TRACKING",
                "symbol": symbol,
                "tracking_status": status,
                "last_passed": update.get("last_passed", ""),
                "first_tracked": update.get("first_tracked", today),
                "last_updated": now_iso,
            }
            if "grace_start" in update:
                item["grace_start"] = update["grace_start"]

            batch.put_item(Item=item)


def check_sentiment_alerts(stocks: list, threshold: float) -> list:
    """Check for stocks with dangerously negative sentiment."""
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
                    "risk_flags": sentiment.get("risk_flags", []),
                },
            })
    return alerts


def check_risk_flag_alerts(stocks: list) -> list:
    """Check for stocks with serious risk flags."""
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
                    f"RISK: {stock.get('symbol')} — {list(serious_found)}"
                ),
                "data": {"flags": list(serious_found)},
            })
    return alerts


def check_low_investability_alerts(stocks: list) -> list:
    """Alert when a passing stock has low investability (sentiment drag)."""
    alerts = []
    for stock in stocks:
        score = stock.get("investability_score", 0)
        if stock.get("passes_screen") and score < 40:
            alerts.append({
                "type": "investability_warning",
                "severity": "medium",
                "symbol": stock.get("symbol"),
                "company_name": stock.get("company_name", ""),
                "message": (
                    f"{stock.get('symbol')} passes filters but investability "
                    f"is low ({score:.1f}). Sentiment may be concerning."
                ),
                "data": {"investability_score": score},
            })
    return alerts


def send_alert_notification(alerts: list, sns_topic_arn: str):
    """Send consolidated alert notification via SNS."""
    if not alerts:
        return

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    subject = f"Stock Screener: {len(alerts)} alert(s) - {now}"

    body_lines = [
        "Stock Screener Alert Report",
        f"Generated: {now}",
        f"Total alerts: {len(alerts)}",
        "",
        "=" * 50,
    ]

    for severity in ["critical", "high", "medium", "low"]:
        group = [a for a in alerts if a["severity"] == severity]
        if group:
            labels = {"critical": "CRITICAL", "high": "HIGH", "medium": "MEDIUM", "low": "INFO"}
            body_lines.append(f"\n[{labels[severity]}]")
            for alert in group:
                body_lines.append(f"  - {alert['message']}")

    body = "\n".join(body_lines)

    try:
        sns_client.publish(
            TopicArn=sns_topic_arn,
            Subject=subject[:100],
            Message=body,
        )
        print(f"  Alert notification sent to SNS")
    except Exception as e:
        print(f"  Warning: Failed to send SNS: {e}")


def handler(event, context):
    """
    Lambda entry point. Final step in the pipeline.

    Checks alerts, manages tracking lifecycle, sends notifications.
    """
    start_time = datetime.now(timezone.utc)
    today = start_time.strftime("%Y-%m-%d")
    print(f"Starting alert check at {start_time.isoformat()}")

    scored_stocks = event.get("scored_stocks", [])
    if not scored_stocks:
        return {"alerts": [], "metadata": {"error": "No scored stocks provided"}}

    # Configuration
    sentiment_threshold = float(
        os.environ.get("SENTIMENT_DROP_THRESHOLD", str(DEFAULT_SENTIMENT_THRESHOLD))
    )
    sns_topic_arn = os.environ.get("ALERT_SNS_TOPIC_ARN", "")
    table_name = os.environ.get("DATA_TABLE_NAME", "")

    print(f"Checking {len(scored_stocks)} stocks...")

    # Read previous tracking status from DynamoDB
    tracking_alerts = []
    if table_name:
        table = dynamodb.Table(table_name)
        symbols = [s.get("symbol") for s in scored_stocks if s.get("symbol")]
        previous_status = get_previous_tracking_status(table, symbols)
        print(f"  Previous tracking data: {len(previous_status)} stocks")

        # Check for tracking changes (new passers, dropped off, grace expiry)
        tracking_alerts, tracking_updates = check_tracking_changes(
            scored_stocks, previous_status, today
        )
        # Update tracking in DynamoDB
        update_tracking_in_dynamodb(table, tracking_updates, today)
        print(f"  Tracking updates: {len(tracking_updates)}")
    else:
        print("  Warning: DATA_TABLE_NAME not set — skipping tracking logic")

    # Run threshold-based alert checks
    all_alerts = []
    all_alerts.extend(tracking_alerts)
    all_alerts.extend(check_sentiment_alerts(scored_stocks, sentiment_threshold))
    all_alerts.extend(check_risk_flag_alerts(scored_stocks))
    all_alerts.extend(check_low_investability_alerts(scored_stocks))

    print(f"  Total alerts: {len(all_alerts)}")

    # Send notification
    if sns_topic_arn and all_alerts:
        send_alert_notification(all_alerts, sns_topic_arn)
    elif all_alerts:
        for alert in all_alerts:
            print(f"    [{alert['severity']}] {alert['message']}")

    end_time = datetime.now(timezone.utc)

    return {
        "alerts": all_alerts,
        "metadata": {
            "stocks_checked": len(scored_stocks),
            "alerts_triggered": len(all_alerts),
            "critical_count": sum(1 for a in all_alerts if a["severity"] == "critical"),
            "high_count": sum(1 for a in all_alerts if a["severity"] == "high"),
            "medium_count": sum(1 for a in all_alerts if a["severity"] == "medium"),
            "low_count": sum(1 for a in all_alerts if a["severity"] == "low"),
            "tracking_updates": len(tracking_alerts),
            "sns_configured": bool(sns_topic_arn),
            "notification_sent": bool(sns_topic_arn and all_alerts),
            "duration_seconds": (end_time - start_time).total_seconds(),
            "timestamp": end_time.isoformat(),
        },
    }
