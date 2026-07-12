"""
Score Calculator Lambda
=======================
Step 5 in the pipeline.

Combines the fundamental score and sentiment score into
a single Investability Score per stock.

Formula: investability = (w1 * fundamental_score) + (w2 * sentiment_score)
Weights are configurable via the shared config.

Writes final scores to DynamoDB for the API/frontend to read.
"""


def handler(event, context):
    """AWS Lambda entry point."""
    # TODO: Implement in Phase 3
    return {"statusCode": 200, "body": "score-calculator placeholder"}
