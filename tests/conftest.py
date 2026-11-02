import json
import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

TOKEN = "test-token-0123456789"


@pytest.fixture(autouse=True)
def aws(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("TABLE_NAME", "applications")
    monkeypatch.setenv("BUCKET_NAME", "attachments-test")
    monkeypatch.setenv("API_TOKEN", TOKEN)
    with mock_aws():
        gsi = lambda name, pk: {  # noqa: E731
            "IndexName": name,
            "KeySchema": [{"AttributeName": pk, "KeyType": "HASH"}, {"AttributeName": "appliedOn", "KeyType": "RANGE"}],
            "Projection": {"ProjectionType": "ALL"},
        }
        boto3.client("dynamodb").create_table(
            TableName="applications",
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": n, "AttributeType": "S"} for n in ("id", "entity", "status", "appliedOn")
            ],
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
            GlobalSecondaryIndexes=[gsi("byDate", "entity"), gsi("byStatus", "status")],
        )
        boto3.client("s3").create_bucket(Bucket="attachments-test")
        yield


@pytest.fixture
def api():
    from app.handler import lambda_handler

    def call(route, *, body=None, path=None, query=None, token=TOKEN):
        event = {
            "routeKey": route,
            "headers": {"authorization": f"Bearer {token}"} if token else {},
            "pathParameters": path,
            "queryStringParameters": query,
            "body": json.dumps(body) if body is not None else None,
        }
        res = lambda_handler(event, None)
        return res["statusCode"], (json.loads(res["body"]) if res["body"] else None)

    return call
