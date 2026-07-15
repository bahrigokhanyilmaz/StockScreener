#!/usr/bin/env python3
"""
Manually trigger the pipeline (full universe scan).

Usage:
    python3 scripts/trigger_pipeline.py
    python3 scripts/trigger_pipeline.py --universe AAPL,MSFT,GOOGL  # Specific stocks only
"""

import subprocess
import json
import sys

PROFILE = "stock-screener"
REGION = "us-east-2"
STATE_MACHINE = "arn:aws:states:us-east-2:116488731375:stateMachine:stock-screener-pipeline"

# Build input
input_data = {}
if "--universe" in sys.argv:
    idx = sys.argv.index("--universe")
    if idx + 1 < len(sys.argv):
        tickers = sys.argv[idx + 1].split(",")
        input_data["universe"] = tickers
        print(f"Running with custom universe: {tickers}")
    else:
        print("Error: --universe requires a comma-separated list of tickers")
        exit(1)
else:
    print("Running full universe scan...")

result = subprocess.run([
    "aws", "stepfunctions", "start-execution",
    "--state-machine-arn", STATE_MACHINE,
    "--input", json.dumps(input_data),
    "--profile", PROFILE, "--region", REGION,
    "--output", "json"
], capture_output=True, text=True)

data = json.loads(result.stdout)
print(f"Execution started: {data.get('executionArn', 'ERROR')}")
print(f"Check status with: python3 scripts/check_pipeline.py")
