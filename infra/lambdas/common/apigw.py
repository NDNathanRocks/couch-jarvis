"""API Gateway management API wrapper: send to a connection, prune it if it is gone."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from botocore.exceptions import ClientError

from common.ddb import Store

log = logging.getLogger(__name__)


def endpoint_url(event: dict[str, Any]) -> str:
    rc = event["requestContext"]
    return f"https://{rc['domainName']}/{rc['stage']}"


class Gateway:
    def __init__(self, client: Any, store: Store) -> None:
        self._client = client
        self._store = store

    def send(self, connection_id: str, data: str) -> bool:
        """Deliver one message. Returns False (and forgets the connection) if it is gone."""
        try:
            self._client.post_to_connection(ConnectionId=connection_id, Data=data.encode("utf-8"))
            return True
        except ClientError as err:
            code = err.response.get("Error", {}).get("Code")
            if code in ("GoneException", "410"):
                self._store.delete_connection(connection_id)
                return False
            log.exception("post_to_connection failed for %s", connection_id)
            return False

    def broadcast(self, connection_ids: Iterable[str], data: str) -> int:
        return sum(1 for cid in connection_ids if self.send(cid, data))

    def close(self, connection_id: str) -> None:
        try:
            self._client.delete_connection(ConnectionId=connection_id)
        except ClientError:
            log.info("connection %s already closed", connection_id)
        self._store.delete_connection(connection_id)
