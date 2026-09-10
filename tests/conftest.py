"""
Shared pytest fixtures and helpers for the stock-screener test suite.

The Lambdas each live in their own folder with a module named `handler.py`.
Importing more than one by plain name collides, so `load_handler` imports a
handler module from its file path under a unique module name.
"""
import importlib.util
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAMBDAS_DIR = os.path.join(REPO_ROOT, "lambdas")


def load_handler(lambda_folder: str):
    """
    Import a Lambda's handler.py under a unique module name so multiple
    handlers can coexist in one test session.

    Example: load_handler("enrichment") -> module for lambdas/enrichment/handler.py
    """
    folder = os.path.join(LAMBDAS_DIR, lambda_folder)
    path = os.path.join(folder, "handler.py")
    # Put the Lambda's own folder on sys.path so sibling modules it imports
    # (pipeline_io, et_date, screener-filters, etc.) resolve exactly as they do
    # in the real Lambda runtime.
    if folder not in sys.path:
        sys.path.insert(0, folder)
    mod_name = f"handler_{lambda_folder.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def repo_root():
    return REPO_ROOT


@pytest.fixture
def aws_env(monkeypatch):
    """Provide dummy AWS env so boto3 clients construct without real creds."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-2")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("DATA_TABLE_NAME", "stock-screener-data")


@pytest.fixture
def dynamodb_table(aws_env):
    """
    A mocked DynamoDB table matching the production single-table schema
    (PK/SK + tracking-status-index GSI). Yields the boto3 Table resource.
    """
    from moto import mock_aws
    import boto3

    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-2")
        client.create_table(
            TableName="stock-screener-data",
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
                {"AttributeName": "tracking_status", "AttributeType": "S"},
                {"AttributeName": "last_updated", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "tracking-status-index",
                    "KeySchema": [
                        {"AttributeName": "tracking_status", "KeyType": "HASH"},
                        {"AttributeName": "last_updated", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield boto3.resource("dynamodb", region_name="us-east-2").Table("stock-screener-data")
