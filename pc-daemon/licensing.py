"""Is this copy licensed? One answer, from the source that fits the build (plan.md 13.3, 5F).

- ``store`` build inside its MSIX package: the Microsoft Store says (paid, trial with days left,
  or expired). Buying opens the Store's page for the app.
- ``direct`` build: a licence key (Ed25519-signed, checked offline against the public key below),
  otherwise a free trial counted from the first run.
- ``dev`` (source checkout): always licensed.
- A ``direct`` build before 1.0.0 (the free preview): always licensed, and no trial clock
  is started, so the 1.0.0 trial counts from the first run of 1.0.0 (``is_preview``).

A licence that cannot be checked (no internet, the Store service down) keeps the last answer,
and a first check that fails counts as licensed: a glitch must never lock out someone who paid.
When the trial has ended the app still pairs phones and shows the TV state, but commands are
refused with a message that says how to buy (``LicenceManager.allows``).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import datetime as dt
import json
import logging
import math
import os
import tempfile
import time
import webbrowser
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from brand import APP_NAME, BUY_URL, STORE_PRODUCT_ID
from build_info import Channel

log = logging.getLogger(__name__)

Status = Literal["active", "trial", "expired"]
Source = Literal["store", "key", "trial", "dev", "preview"]
TRIAL_DAYS = 14
KEY_PREFIX = "ARMREST1."
DAY_S = 86_400
# Ed25519 public key that checks licence keys (scripts/license_keys.py makes the pair; the private
# half never enters this repository). Replacing it invalidates every key issued so far.
PUBLIC_KEY_B64 = "O8kB2-iAedL-NRTMvMAsdLtZEg7ULTmXtAT3bRO8ZFA"


@dataclass(frozen=True)
class Licence:
    status: Status
    source: Source
    days_left: int | None = None
    holder: str | None = None  # the name on a licence key

    @property
    def usable(self) -> bool:
        return self.status != "expired"


LICENSED_DEV = Licence("active", "dev")
LICENSED_PREVIEW = Licence("active", "preview")


def is_preview(version: str) -> bool:
    """Versions before 1.0.0 are a free preview: no trial, no key."""
    try:
        return int(version.split(".")[0]) < 1
    except ValueError:
        return False


# ----------------------------------------------------------------------------- keys


@dataclass(frozen=True)
class KeyInfo:
    name: str
    order: str
    issued: str


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def verify_key(text: str, public_key_b64: str = PUBLIC_KEY_B64) -> KeyInfo | None:
    """The details of a genuine key, or None (typo, forged, or for another public key)."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    text = "".join(text.split())  # keys pasted from an email may be wrapped
    if not text.startswith(KEY_PREFIX) or text.count(".") != 2:
        return None
    _, body, sig = text.split(".")
    try:
        public = Ed25519PublicKey.from_public_bytes(_b64d(public_key_b64))
        public.verify(_b64d(sig), body.encode("ascii"))
        data = json.loads(_b64d(body))
        return KeyInfo(name=str(data["n"])[:64], order=str(data["o"])[:64], issued=str(data["i"]))
    except (InvalidSignature, ValueError, KeyError, TypeError):
        return None


def issue_key(private_key: Any, name: str, order: str, issued: dt.date) -> str:
    """Sign a key (scripts/license_keys.py; needs the private key, which never ships)."""
    body = _b64e(
        json.dumps(
            {"n": name[:64], "o": order[:64], "i": issued.isoformat()}, separators=(",", ":")
        ).encode("utf-8")
    )
    return f"{KEY_PREFIX}{body}.{_b64e(private_key.sign(body.encode('ascii')))}"


# ----------------------------------------------------------------------------- local record


class LicenceFile:
    """%APPDATA%\\CouchJarvis\\license.json: first run, the key, the last Store answer."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".license-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.replace(tmp, self.path)  # atomic: a crash never leaves half a file
        except OSError:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise


def trial_licence(first_run: float, now: float, days: int = TRIAL_DAYS) -> Licence:
    left = math.ceil(days - max(0.0, now - first_run) / DAY_S)
    if left <= 0:
        return Licence("expired", "trial", 0)
    return Licence("trial", "trial", min(left, days))


def direct_licence(record: dict[str, Any], now: float, public_key_b64: str) -> Licence:
    key = record.get("key")
    if isinstance(key, str):
        info = verify_key(key, public_key_b64)
        if info is not None:
            return Licence("active", "key", holder=info.name or None)
    first = record.get("first_run")
    seen = record.get("last_seen")
    first = float(first) if isinstance(first, (int, float)) else now
    # a clock turned back does not restart the trial
    effective_now = max(now, float(seen)) if isinstance(seen, (int, float)) else now
    return trial_licence(first, effective_now)


# ----------------------------------------------------------------------------- the Store


StoreQuery = Callable[[], Awaitable[Licence]]


async def query_store() -> Licence:
    """The Store's view of this app's licence (only meaningful inside the MSIX package)."""
    from winrt.windows.services.store import StoreContext

    lic = await StoreContext.get_default().get_app_license_async()
    if not lic.is_active:
        return Licence("expired", "store", 0)
    if lic.is_trial:
        remaining: dt.timedelta = lic.trial_time_remaining
        return Licence("trial", "store", max(0, math.ceil(remaining.total_seconds() / DAY_S)))
    return Licence("active", "store")


def store_page_url(product_id: str | None = None) -> str | None:
    pid = STORE_PRODUCT_ID if product_id is None else product_id
    return f"ms-windows-store://pdp/?ProductId={pid}" if pid else None


# ----------------------------------------------------------------------------- manager


class LicenceManager:
    REFRESH_S = 6 * 3600  # paid: the Store answer rarely changes
    TRIAL_S = 600  # in trial or expired: notice a purchase or the end of the trial
    FAST_S = 30  # just after Buy
    FAST_FOR_S = 600

    def __init__(
        self,
        channel: Channel,
        packaged: bool,
        record: LicenceFile,
        *,
        store_query: StoreQuery = query_store,
        public_key_b64: str = PUBLIC_KEY_B64,
        clock: Callable[[], float] = time.time,
        open_url: Callable[[str], object] = webbrowser.open,
        preview: bool = False,
    ) -> None:
        self._channel = channel
        self._uses_store = channel == "store" and packaged
        self._preview = preview and not self._uses_store
        self._record = record
        self._store_query = store_query
        self._public_key = public_key_b64
        self._clock = clock
        self._open_url = open_url
        self._fast_until = 0.0
        self._poke = asyncio.Event()
        self.current: Licence = self._free() or self._from_record()

    def _free(self) -> Licence | None:
        if self._channel == "dev":
            return LICENSED_DEV
        return LICENSED_PREVIEW if self._preview else None

    @property
    def uses_store(self) -> bool:
        return self._uses_store

    def _from_record(self) -> Licence:
        data = self._record.load()
        if self._uses_store:
            cached = data.get("store")
            if isinstance(cached, dict) and cached.get("status") in ("active", "trial", "expired"):
                days = cached.get("days_left")
                return Licence(cached["status"], "store", days if isinstance(days, int) else None)
            return Licence("active", "store")  # never checked yet: do not lock anyone out
        return direct_licence(data, self._clock(), self._public_key)

    def _touch(self) -> dict[str, Any]:
        """Record the first run and the latest time seen (the trial clock)."""
        data = self._record.load()
        now = self._clock()
        changed = False
        if not isinstance(data.get("first_run"), (int, float)):
            data["first_run"], changed = now, True
        seen = data.get("last_seen")
        if not isinstance(seen, (int, float)) or now > seen:
            data["last_seen"], changed = now, True
        if changed:
            try:
                self._record.save(data)
            except OSError:
                log.warning("could not save the licence record", exc_info=True)
        return data

    async def refresh(self) -> Licence:
        free = self._free()
        if free is not None:  # nothing recorded: the trial clock has not started
            self.current = free
            return self.current
        data = self._touch()
        if self._uses_store:
            try:
                fresh = await self._store_query()
            except Exception:  # noqa: BLE001 - offline / Store down: keep the last answer
                log.warning("the Store licence could not be checked", exc_info=True)
                return self.current
            data["store"] = {"status": fresh.status, "days_left": fresh.days_left}
            with contextlib.suppress(OSError):
                self._record.save(data)
            self.current = fresh
        else:
            self.current = direct_licence(data, self._clock(), self._public_key)
        return self.current

    def enter_key(self, text: str) -> Licence | None:
        """Save a licence key if it is genuine (direct download). None when it is not."""
        if verify_key(text, self._public_key) is None:
            return None
        data = self._touch()
        data["key"] = "".join(text.split())
        self._record.save(data)
        self.current = direct_licence(data, self._clock(), self._public_key)
        return self.current

    def allows(self) -> bool:
        return self.current.usable

    def refusal(self) -> str:
        return f"Your free {APP_NAME} trial has ended. Tap Buy to keep using it."

    def buy(self) -> str:
        """Open where to buy, on the PC's screen (the TV). Returns what to tell the phone."""
        url = store_page_url() if self._uses_store else None
        self._open_url(url or BUY_URL)
        self._fast_until = self._clock() + self.FAST_FOR_S  # notice the purchase quickly
        self._poke.set()
        if url:
            return "The Microsoft Store page is open on the TV."
        return f"The {APP_NAME} website is open on the TV."

    def state(self) -> dict[str, Any] | None:
        """What the phones see (RoomState.license); None = nothing to show (paid or dev)."""
        if self.current.status == "active":
            return None
        return {"status": self.current.status, "days_left": self.current.days_left}

    def next_check_s(self) -> float:
        """Seconds until the next check: often just after Buy, rarely once paid."""
        if self._clock() < self._fast_until:
            return self.FAST_S
        return self.REFRESH_S if self.current.status == "active" else self.TRIAL_S

    async def run(self, on_change: Callable[[Licence], None]) -> None:
        """Re-check now and then; tell the app when the answer changes (a purchase, expiry)."""
        last: Licence | None = None
        while True:
            fresh = await self.refresh()
            if fresh != last:
                on_change(fresh)
                last = fresh
            self._poke.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._poke.wait(), self.next_check_s())
