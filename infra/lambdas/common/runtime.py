"""Lazily-built singletons shared by the handlers. Tests replace them via ``override``."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from common.apigw import Gateway, endpoint_url
from common.ddb import Store, TableNames

_store: Store | None = None
_gateway_factory: Callable[[dict[str, Any], Store], Gateway] | None = None


def get_store() -> Store:
    global _store
    if _store is None:
        import boto3

        tables = TableNames(
            rooms=os.environ["ROOMS_TABLE"],
            connections=os.environ["CONNECTIONS_TABLE"],
            devices=os.environ["DEVICES_TABLE"],
            idempotency=os.environ["IDEMPOTENCY_TABLE"],
            config=os.environ["CONFIG_TABLE"],
        )
        _store = Store(boto3.resource("dynamodb"), tables)
    return _store


def get_gateway(event: dict[str, Any]) -> Gateway:
    store = get_store()
    if _gateway_factory is not None:
        return _gateway_factory(event, store)
    import boto3

    return Gateway(boto3.client("apigatewaymanagementapi", endpoint_url=endpoint_url(event)), store)


def override(
    store: Store | None = None,
    gateway_factory: Callable[[dict[str, Any], Store], Gateway] | None = None,
) -> None:
    global _store, _gateway_factory
    _store, _gateway_factory = store, gateway_factory
