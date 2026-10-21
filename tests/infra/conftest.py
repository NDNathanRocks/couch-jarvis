from __future__ import annotations

from collections.abc import Iterator

import boto3
import pytest
from moto import mock_aws

from common import runtime
from common.apigw import Gateway
from common.ddb import Store, TableNames
from infra_support import FakeApiClient, World


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    for name, value in {
        "AWS_ACCESS_KEY_ID": "x",
        "AWS_SECRET_ACCESS_KEY": "x",
        "AWS_DEFAULT_REGION": "us-east-1",
    }.items():
        monkeypatch.setenv(name, value)
    with mock_aws():
        res = boto3.resource("dynamodb")
        res.create_table(
            TableName="rooms",
            KeySchema=[{"AttributeName": "room_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "room_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        res.create_table(
            TableName="connections",
            KeySchema=[{"AttributeName": "connection_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "connection_id", "AttributeType": "S"},
                {"AttributeName": "room_id", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "room-index",
                    "KeySchema": [{"AttributeName": "room_id", "KeyType": "HASH"}],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        res.create_table(
            TableName="devices",
            KeySchema=[
                {"AttributeName": "room_id", "KeyType": "HASH"},
                {"AttributeName": "device_id", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "room_id", "AttributeType": "S"},
                {"AttributeName": "device_id", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        for name in ("idempotency", "config"):
            res.create_table(
                TableName=name,
                KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
                AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
                BillingMode="PAY_PER_REQUEST",
            )
        tables = TableNames("rooms", "connections", "devices", "idempotency", "config")
        store = Store(res, tables)
        api = FakeApiClient()
        runtime.override(store, lambda event, s: Gateway(api, s))
        yield World(store, api)
        runtime.override(None, None)
