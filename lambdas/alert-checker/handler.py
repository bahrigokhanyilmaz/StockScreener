"""
Alert Checker Lambda
====================
Step 6 in the pipeline.

Compares new scores against user-defined threshold rules.
If a stock breaches a threshold (e.g. sentiment drops below -0.3,
or P/E exceeds 50), sends a notification via SNS (email/SMS).

Also detects when a stock falls out of the screened list entirely.
"""


def handler(event, context):
    """AWS Lambda entry point."""
    # TODO: Implement in Phase 3
    return {"statusCode": 200, "body": "alert-checker placeholder"}
