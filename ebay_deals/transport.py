"""Fetch transports.

Two backends, in order of preference:

1. ``browse_api`` -- the official eBay Browse Item API (OAuth 2.0 client
   credentials). This is the supported way to get listings and the one that
   scales. It needs ``EBAY_CLIENT_ID`` and ``EBAY_CLIENT_SECRET``.

2. ``html`` -- single-host HTML retrieval through the mobile endpoints using a
   persisted cookie jar and one curl subprocess per request.

Why curl and not urllib/requests: measured 2026-09-28, Python's urllib received
HTTP 403 on the very first request while the same URL, headers and cookie jar
succeeded with curl. Rather than fight TLS/HTTP fingerprint differences -- which
would edge toward evasion -- the tool uses the same client a browser uses and
keeps the request rate low.

Both backends obey the same rule: a challenge or 403 stops the run. There is no
CAPTCHA solving, no proxy rotation, no user-agent rotation, no cookie forging.
"""

from __future__ import annotations

import base64
import json
import os
import random
import subprocess
import time
import urllib.parse
from dataclasses import dataclass

from . import config
from .credentials import load_credentials

MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1"
)
DESKTOP_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36"
)

SEARCH_HOST = "https://m.ebay.com/sch/i.html"
ITEM_URL = "https://m.ebay.com/itm/{item_id}"
BROWSE_SEARCH_PATH = "/buy/browse/v1/item_summary/search"
BROWSE_ITEM_PATH = "/buy/browse/v1/item/{item_id}"
OAUTH_PATH = "/identity/v1/oauth2/token"

CHALLENGE_MARKERS = ("Pardon Our Interruption", "Robot or Human?", "Enter the characters you see")


class Blocked(RuntimeError):
    """Raised when eBay refuses to serve a request. Never worked around."""


@dataclass
class Response:
    url: str
    status: int
    text: str
    from_cache: bool = False

    @property
    def blocked(self) -> str | None:
        if self.status != 200:
            return f"http{self.status}"
        if len(self.text) < 8000:
            return "short"
        for marker in CHALLENGE_MARKERS:
            if marker in self.text:
                return "challenge"
        if "Error Page" in self.text[:4000]:
            return "error"
        return None


def _cookie_paths(state_dir: str) -> tuple[str, str]:
    cache_dir = os.path.join(state_dir, "cache")
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(state_dir, "cookies.txt"), cache_dir


def _curl(url: str, jar: str, ua: str, timeout: int = 35) -> tuple[int, str]:
    proc = subprocess.run(
        [
            "curl",
            "-s",
            "-L",
            "--compressed",
            "-m",
            str(timeout),
            "-c",
            jar,
            "-b",
            jar,
            "-A",
            ua,
            "-H",
            "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "-H",
            "Accept-Language: en-US,en;q=0.9",
            "-o",
            "-",
            "-w",
            "\n%{http_code}",
            url,
        ],
        capture_output=True,
        text=True,
    )
    out = proc.stdout or ""
    if "\n" in out:
        body, _, code = out.rpartition("\n")
        try:
            return int(code.strip() or 0), body
        except ValueError:
            return 0, body
    return 0, out


def _cache_get(cache_dir: str, key: str, max_age: int) -> str | None:
    path = os.path.join(cache_dir, f"{key}.html")
    if not os.path.exists(path):
        return None
    if max_age >= 0 and (time.time() - os.path.getmtime(path)) > max_age:
        return None
    with open(path, errors="replace") as handle:
        return handle.read()


def _cache_put(cache_dir: str, key: str, text: str) -> None:
    with open(os.path.join(cache_dir, f"{key}.html"), "w") as handle:
        handle.write(text)


class HtmlTransport:
    """Polite, single-host, single-session HTML retrieval."""

    def __init__(
        self,
        state_dir: str,
        delay: float | None = None,
        log=print,
        use_cache: bool = True,
        cache_max_age: int = 900,
    ):
        self.jar, self.cache_dir = _cookie_paths(state_dir)
        self.delay = config.SEARCH_DELAY_SECONDS if delay is None else delay
        self.log = log
        self.use_cache = use_cache
        self.cache_max_age = cache_max_age
        self.blocked_count = 0
        self.request_count = 0

    def search(
        self,
        query: str,
        sop: str = "15",
        ipg: int = 60,
        page: int = 1,
        cache_key: str | None = None,
    ) -> Response:
        url = (
            SEARCH_HOST
            + "?"
            + urllib.parse.urlencode({"_nkw": query, "_sop": sop, "_ipg": ipg, "_pgn": page})
        )
        return self._get(url, cache_key or f"search-{query}".replace(" ", "-")[:60], self.delay)

    def item(self, item_id: str) -> Response:
        return self._get(ITEM_URL.format(item_id=item_id), f"item-{item_id}", self.delay)

    def _get(self, url: str, key: str, delay: float) -> Response:
        if self.use_cache:
            cached = _cache_get(self.cache_dir, key, self.cache_max_age)
            if cached is not None:
                return Response(url=url, status=200, text=cached, from_cache=True)
        last = "unknown"
        for attempt, backoff in enumerate(config.BACKOFF_SECONDS):
            if backoff:
                self.log(f"    backing off {backoff}s before retry {attempt}")
                time.sleep(backoff)
            status, text = _curl(url, self.jar, MOBILE_UA)
            self.request_count += 1
            resp = Response(url=url, status=status, text=text)
            if not resp.blocked:
                _cache_put(self.cache_dir, key, text)
                time.sleep(delay + random.random() * 6)
                return resp
            last = resp.blocked or "unknown"
            self.blocked_count += 1
            self.log(f"    attempt {attempt}: http={status} bytes={len(text)} blocked={last}")
            if attempt < len(config.BACKOFF_SECONDS) - 1:
                time.sleep(15 + random.random() * 10)
        raise Blocked(f"eBay refused {url} ({last}); stopping instead of working around it")


class BrowseApiTransport:
    """Official Browse Item API. Preferred backend.

    OAuth 2.0 client-credentials grant, token cached until shortly before it
    expires. Only HTTP status and JSON error fields are surfaced; the client
    secret is never logged.
    """

    def __init__(self, log=print, path: str | None = None):
        creds = load_credentials(path)
        self.client_id = creds["client_id"]
        self.client_secret = creds["client_secret"]
        self.marketplace_id = creds["marketplace_id"] or "EBAY_US"
        self.log = log
        self._token: tuple[str, float] | None = None
        #: Base host, overridable so a diagnostic can probe sandbox too.
        self.host = "api.ebay.com"

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def _basic_auth_header(self) -> str:
        """HTTP Basic header for the token endpoint.

        eBay authenticates the client with an ``Authorization: Basic`` header
        built from the App ID and Client Secret, not with form fields in the
        body. Sending them as body parameters makes eBay answer
        ``invalid_client / client authentication failed`` even when the
        credential pair is perfectly valid.
        """
        raw = f"{self.client_id}:{self.client_secret}".encode()
        return "Basic " + base64.b64encode(raw).decode("ascii")

    def _access_token(self) -> str:
        if self._token and self._token[1] > time.time() + 60:
            return self._token[0]
        # client_credentials: POST, form-encoded body, Basic auth header.
        proc = subprocess.run(
            [
                "curl",
                "-s",
                "-X",
                "POST",
                f"https://{self.host}{OAUTH_PATH}",
                "-H",
                "Content-Type: application/x-www-form-urlencoded",
                "-H",
                f"Authorization: {self._basic_auth_header()}",
                "--data-urlencode",
                "grant_type=client_credentials",
                "--data-urlencode",
                f"scope=https://{self.host}/oauth/api_scope",
            ],
            capture_output=True,
            text=True,
        )
        try:
            data = json.loads(proc.stdout or "{}")
        except ValueError:
            # `from None`: the JSONDecodeError position is noise here, the
            # HTTP status is what matters.
            raise Blocked(f"OAuth response was not JSON (HTTP {proc.returncode})") from None
        token = data.get("access_token")
        if not token:
            # Report eBay's machine-readable code too: the description alone
            # ("client authentication failed") hides invalid_client, which is
            # the difference between bad credentials and a malformed request.
            # Only these two fields are surfaced, never request data.
            code = data.get("error") or "unknown_error"
            detail = data.get("error_description") or "no access_token"
            raise Blocked(f"OAuth failed: {code} ({detail})")
        self._token = (token, time.time() + float(data.get("expires_in", 7200)))
        return token

    def _api(self, url: str) -> dict:
        token = self._access_token()
        proc = subprocess.run(
            [
                "curl",
                "-s",
                "-H",
                f"Authorization: Bearer {token}",
                "-H",
                f"X-EBAY-C-MARKETPLACE-ID: {self.marketplace_id}",
                "-H",
                "Accept: application/json",
                "-w",
                "\n%{http_code}",
                url,
            ],
            capture_output=True,
            text=True,
        )
        out = proc.stdout or ""
        body, _, code = out.rpartition("\n")
        try:
            status = int(code.strip() or 0)
        except ValueError:
            status, body = 0, out
        try:
            data = json.loads(body or "{}")
        except ValueError:
            raise Blocked(f"Browse API returned non-JSON (HTTP {status})") from None
        if status in (401, 403):
            self._token = None
            raise Blocked(f"Browse API HTTP {status}: {data.get('message') or 'access denied'}")
        if status == 429:
            raise Blocked(f"Browse API HTTP 429: {data.get('message') or 'quota exceeded'}")
        if status >= 400:
            raise Blocked(f"Browse API HTTP {status}: {data.get('message') or body[:200]}")
        return data

    def search(self, query: str, limit: int = 50, page: int = 1, sort: str = "price_asc") -> dict:
        url = f"https://{self.host}{BROWSE_SEARCH_PATH}?" + urllib.parse.urlencode(
            {
                "q": query,
                "limit": limit,
                "offset": (page - 1) * limit,
                "sort": sort,
            }
        )
        return self._api(url)

    @staticmethod
    def normalise_item_id(item_id: str) -> str:
        """Build the identifier getItem actually accepts.

        The Browse API keys listings as ``v1|<legacyId>|<suffix>`` and
        ``getItem`` 404s on a bare legacy id. Search results already carry the
        full form, so keep it when present rather than reconstructing it from
        digits (which would silently drop listings whose suffix is not 0).
        """
        item_id = (item_id or "").strip()
        if item_id.startswith("v1|"):
            return item_id
        digits = "".join(ch for ch in item_id if ch.isdigit())
        return f"v1|{digits}|0" if digits else item_id

    def item(self, item_id: str) -> dict:
        path = BROWSE_ITEM_PATH.format(item_id=self.normalise_item_id(item_id))
        return self._api(f"https://{self.host}{path}")
