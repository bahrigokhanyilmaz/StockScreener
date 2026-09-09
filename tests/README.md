# Stock Screener — Test Suite

Automated tests that let us understand the blast radius of a change and catch
regressions **before** deploying. Per the architecture steering doc (Design
Principle #14), the suite MUST pass before every deploy.

## Setup (one time)

```bash
.venv/bin/python3 -m pip install -r requirements-dev.txt
```

## Run

```bash
./scripts/run_tests.sh                 # everything
./scripts/run_tests.sh -m regression   # only past-bug regression tests
./scripts/run_tests.sh -m unit         # only fast unit tests
./scripts/run_tests.sh -m integration  # cross-step / DynamoDB tests
```

## Layout

| File | Covers |
|------|--------|
| `test_enrichment_trading_day.py` | `get_last_trading_day` picks a day with real prices; skips weekends AND holidays (regression: Labor Day → 0 prices → all GRACE) |
| `test_tracking_preservation.py` | `mark_price`/`mark_date`/`first_tracked` survive the alert-checker (Step 8) TRACKING rewrite (regression: mark wipe + Days reset) |
| `test_market_gate.py` | Step 0 skips weekends/holidays, proceeds on trading days, fails open on API error |
| `test_screening_filters.py` | `apply_filter`: missing data = FAIL, percent_as_decimal conversion |

## How the tests import Lambdas

Each Lambda has its own `handler.py`. `tests/conftest.py` provides
`load_handler("<folder>")` to import a handler by path under a unique module
name so multiple handlers coexist in one session.

Mocking:
- **DynamoDB** — `moto` (`dynamodb_table` fixture builds the real single-table
  schema + `tracking-status-index` GSI).
- **HTTP (Polygon/FMP)** — `requests-mock`.
- **Time** — freeze `handler.datetime.now(...)` via `monkeypatch`.

## Markers

- `unit` — fast, isolated, no network/AWS
- `integration` — cross-step invariants over mocked AWS/HTTP
- `regression` — reproduces a specific past production bug; must fail against the
  old buggy code and pass against the fix

## Adding tests for a change (required workflow)

1. Identify the blast radius: which steps/functions/DynamoDB items/UI fields the
   change touches.
2. Add/extend unit tests for the changed logic.
3. If it touches TRACKING/scoring/pipeline flow, add/extend an integration test.
4. If fixing a bug, first write a `regression` test that FAILS on the current
   (broken) code, then fix until it passes.
5. `./scripts/run_tests.sh` must be green before deploying.
