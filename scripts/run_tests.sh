#!/bin/bash
# Run the full automated test suite. MUST pass before any deploy.
# Runs pytest, then the CDK Jest tests. Usage: ./scripts/run_tests.sh [pytest args]
#   ./scripts/run_tests.sh                 # all tests
#   ./scripts/run_tests.sh -m regression   # only regression tests
#   ./scripts/run_tests.sh -m unit         # only unit tests
set -e
cd "$(dirname "$0")/.."
export AWS_DEFAULT_REGION=us-east-2
.venv/bin/python3 -m pytest "$@"
# CDK assertion tests (Jest): the daily Scheduler schedule, etc.
npx jest
