"""A read-only client for the Endute Connect API (https://api.endute.com/v1).

Endute serves the cards connected in its portal: accounts, cached balances and booked
transactions, newest first, with cursor pagination. Every endpoint is a GET; this client
never writes anything at Endute.

The API key (``edk_...``) comes from the ``ENDUTE_API_KEY`` environment variable, then an
``ENDUTE_API_KEY=`` line in ``.env`` in the working directory (git-ignored), then, on macOS,
the login keychain item ``pennywise-endute``. It is sent only to the API host, never
logged, and never stored in the database.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path

BASE_URL = "https://api.endute.com/v1"
KEY_ENV = "ENDUTE_API_KEY"
KEYCHAIN_SERVICE = "pennywise-endute"
KEYCHAIN_ACCOUNT = "endute"
TIMEOUT_SECONDS = 30
RETRY_DELAYS = (2, 5, 15)  # seconds, for 429 and 5xx


class EnduteError(RuntimeError):
    """The Endute API refused a request or couldn't be reached."""


def keychain_command() -> list[str]:
    """The command that stores the key in the login keychain. `security` prompts for the
    key itself, so it never appears in a command line or the shell history."""
    return [
        "security", "add-generic-password", "-U",
        "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w",
    ]  # fmt: skip


def _from_dotenv(path: Path = Path(".env")) -> str:
    """ENDUTE_API_KEY from a .env file; only that line is read, nothing else is loaded."""
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return ""
    for line in lines:
        name, _, value = line.strip().removeprefix("export ").partition("=")
        if name.strip() == KEY_ENV:
            return value.strip().strip("'\"")
    return ""


def api_key() -> str:
    key = os.environ.get(KEY_ENV, "").strip() or _from_dotenv()
    if not key:
        try:
            found = subprocess.run(
                ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:  # not macOS
            found = None
        if found is not None and found.returncode == 0:
            key = found.stdout.strip()
    if not key:
        raise EnduteError(
            "no Endute API key; run `pennywise endute key` to store it in the keychain "
            f"(or put {KEY_ENV}=... in .env)"
        )
    return key


Fetch = Callable[[str, dict[str, str]], object]


@dataclass(frozen=True)
class Client:
    """Endute's read-only endpoints. `fetch(path, params)` returns parsed JSON; tests pass
    a fake one, and `Client.connect()` makes the real one."""

    fetch: Fetch

    @classmethod
    def connect(cls, key: str | None = None, base_url: str = BASE_URL) -> Client:
        key = key or api_key()

        def fetch(path: str, params: dict[str, str]) -> object:
            url = base_url + path
            if params:
                url += "?" + urllib.parse.urlencode(params)
            return _get(url, key)

        return cls(fetch)

    def accounts(self) -> list[dict]:
        return list(self.fetch("/accounts", {}))

    def connections(self) -> list[dict]:
        return list(self.fetch("/connections", {}))

    def balance(self, account_id: str) -> dict:
        return dict(self.fetch(f"/accounts/{_safe_id(account_id)}/balances", {}))

    def transactions(
        self, account_id: str, *, since: date | None = None, until: date | None = None
    ) -> Iterator[dict]:
        """Every booked transaction with a booking date in [since, until], newest first,
        following the cursor until the last page."""
        params: dict[str, str] = {}
        if since is not None:
            params["from"] = since.isoformat()
        if until is not None:
            params["to"] = until.isoformat()
        path = f"/accounts/{_safe_id(account_id)}/transactions"
        while True:
            page = self.fetch(path, params)
            yield from page["results"]
            cursor = _cursor(page.get("next"))
            if cursor is None:
                return
            params = {**params, "cursor": cursor}


def _safe_id(account_id: str) -> str:
    if not account_id or not all(c.isalnum() or c == "-" for c in account_id):
        raise EnduteError(f"{account_id!r} is not an Endute account id")
    return account_id


def _cursor(next_url: str | None) -> str | None:
    """The cursor from a page's `next` link. Only the cursor is taken: the next request
    goes to the configured API host, so the key is never sent anywhere a link points."""
    if not next_url:
        return None
    values = urllib.parse.parse_qs(urllib.parse.urlsplit(next_url).query).get("cursor")
    if not values:
        raise EnduteError("a page's next link has no cursor")
    return values[0]


def _get(url: str, key: str) -> object:
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": "pennywise",
        },
    )
    for attempt, delay in enumerate((*RETRY_DELAYS, None)):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if delay is not None and (exc.code == 429 or exc.code >= 500):
                time.sleep(delay)
                continue
            raise EnduteError(_describe(exc)) from None
        except (urllib.error.URLError, TimeoutError) as exc:
            if delay is not None and attempt < len(RETRY_DELAYS):
                time.sleep(delay)
                continue
            reason = getattr(exc, "reason", exc)
            raise EnduteError(f"couldn't reach Endute: {reason}") from None
    raise AssertionError("unreachable")


def _describe(exc: urllib.error.HTTPError) -> str:
    """Endute's error envelope is {"error": {"code": ..., "message": ...}}."""
    try:
        error = json.loads(exc.read()).get("error", {})
        detail = f"{error.get('code', '')}: {error.get('message', '')}".strip(": ")
    except (ValueError, AttributeError):
        detail = ""
    if exc.code == 401:
        return "Endute rejected the API key (401); check it with `pennywise endute key`"
    return f"Endute returned {exc.code}" + (f" ({detail})" if detail else "")
