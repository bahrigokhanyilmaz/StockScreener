"""
Pipeline I/O Helper
====================
Shared utility for reading/writing pipeline data via S3.

Step Functions has a 256KB payload limit between steps. When processing
thousands of stocks, the data exceeds this. Solution: each Lambda writes
its output to S3 and returns only the S3 key. The next Lambda reads from
that key.

S3 path convention:
    pipeline/{date}/{execution_id}/{step_name}.json

Usage in a Lambda handler:
    from pipeline_io import read_pipeline_input, write_pipeline_output

    def handler(event, context):
        # Read large input from S3 (or directly from event if small)
        data = read_pipeline_input(event)

        # ... process data ...

        # Write large output to S3, return small reference
        return write_pipeline_output(result, step_name="fundamentals")
"""

import json
import os
from datetime import datetime, timezone

import boto3

s3_client = boto3.client("s3")
BUCKET = os.environ.get("RAW_DATA_BUCKET", "")


def read_pipeline_input(event: dict) -> dict:
    """
    Read pipeline input — either from S3 (if event contains 's3_key') or directly from event.

    When a previous step wrote its output to S3, it passes:
        {"s3_key": "pipeline/2026-07-13/.../step_name.json", "metadata": {...}}

    When input is small (e.g., manual trigger), data is passed directly in the event.
    """
    s3_key = event.get("s3_key")

    if s3_key and BUCKET:
        # Read from S3
        response = s3_client.get_object(Bucket=BUCKET, Key=s3_key)
        body = response["Body"].read().decode("utf-8")
        return json.loads(body)

    # Data is directly in the event (small payload or manual invocation)
    return event


def write_pipeline_output(data: dict, step_name: str) -> dict:
    """
    Write pipeline output to S3 and return a small reference.

    Returns a dict with:
        - s3_key: path to the data in S3
        - metadata: summary info from the output (always small)

    If RAW_DATA_BUCKET is not set, falls back to returning data directly
    (useful for local testing, but will hit payload limits in production).
    """
    if not BUCKET:
        # Fallback for local testing — return data directly
        return data

    now = datetime.now(timezone.utc)
    s3_key = (
        f"pipeline/{now.strftime('%Y-%m-%d')}/"
        f"{step_name}_{now.strftime('%H%M%S')}.json"
    )

    # Write to S3
    s3_client.put_object(
        Bucket=BUCKET,
        Key=s3_key,
        Body=json.dumps(data, default=str),
        ContentType="application/json",
    )

    # Return small reference + metadata summary
    metadata = data.get("metadata", {})
    return {
        "s3_key": s3_key,
        "metadata": metadata,
    }
