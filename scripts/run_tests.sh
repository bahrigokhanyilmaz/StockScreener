#!/bin/bash
# Run the full automated test suite. MUST pass before any deploy.
# Usage: ./scripts/run_tests.sh [pytest args]
#   ./scripts/run_tests.sh                 # all tests
#   ./scripts/run_tests.sh -m regression   # only regression tests
#   ./scripts/run_tests.sh -m unit         # only unit tests
set -e
cd "$(dirname "$0")/.."
export AWS_DEFAULT_REGION=us-east-2
exec .venv/bin/python3 -m pytest "$@"
