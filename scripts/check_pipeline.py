#!/usr/bin/env python3
"""
Check the most recent pipeline run — shows step-by-step results.

Usage:
    python3 scripts/check_pipeline.py
"""

import subprocess
import json

PROFILE = "stock-screener"
REGION = "us-east-2"
STATE_MACHINE = "arn:aws:states:us-east-2:116488731375:stateMachine:stock-screener-pipeline"


def aws_cmd(args):
    result = subprocess.run(
        ["aws"] + args + ["--profile", PROFILE, "--region", REGION, "--output", "json"],
        capture_output=True, text=True
    )
    return json.loads(result.stdout) if result.stdout.strip() else {}


# Get most recent execution
executions = aws_cmd([
    "stepfunctions", "list-executions",
    "--state-machine-arn", STATE_MACHINE,
    "--max-results", "1",
])
if not executions.get("executions"):
    print("No executions found")
    exit()

exec_info = executions["executions"][0]
arn = exec_info["executionArn"]
print(f"Latest execution: {exec_info['status']}")
print(f"Started: {exec_info['startDate']}")
print()

# Get history
history = aws_cmd(["stepfunctions", "get-execution-history", "--execution-arn", arn])

step_names = ["EDGAR Fetch", "Pre-Screen", "Enrichment", "Full Screen", "News", "Sentiment", "Scores", "Alerts"]
step_num = 0

for event in history.get("events", []):
    if event["type"] == "LambdaFunctionSucceeded":
        output = json.loads(event["lambdaFunctionSucceededEventDetails"]["output"])
        meta = output.get("metadata", {})
        name = step_names[step_num] if step_num < len(step_names) else f"Step {step_num + 1}"

        info = ""
        if "stocks_with_data" in meta:
            info = f"{meta['stocks_with_data']} stocks"
        elif "passing_count" in meta:
            info = f"{meta['passing_count']} pass, {meta.get('near_miss_count', 0)} near-miss, {meta.get('rejected_count', 0)} rejected"
        elif "prices_matched" in meta:
            info = f"prices={meta['prices_matched']}, finnhub={meta.get('finnhub_enriched', 0)}, P/E={meta.get('pe_available', 0)}, PEG={meta.get('peg_available', 0)}"
        elif "articles_fetched" in meta:
            info = f"{meta['articles_fetched']} articles for {meta['stocks_requested']} stocks"
        elif "total_articles_analyzed" in meta:
            info = f"{meta['total_articles_analyzed']} articles analyzed"
        elif "total_scored" in meta:
            info = f"{meta['total_scored']} scored, highly={meta.get('highly_investable_count', 0)}, moderate={meta.get('moderately_investable_count', 0)}"
        elif "alerts_triggered" in meta:
            info = f"{meta['alerts_triggered']} alerts, sent={meta.get('notification_sent', False)}"
        else:
            info = json.dumps(meta)[:80]

        print(f"  {name}: {info}")
        step_num += 1

    elif event["type"] == "LambdaFunctionFailed":
        details = event["lambdaFunctionFailedEventDetails"]
        name = step_names[step_num] if step_num < len(step_names) else f"Step {step_num + 1}"
        print(f"  {name}: FAILED — {details.get('error')}: {details.get('cause', '')[:100]}")
