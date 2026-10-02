"""Credential loading for the official eBay APIs.

Order of precedence:

1. environment variables ``EBAY_CLIENT_ID`` / ``EBAY_CLIENT_SECRET``
2. the JSON file named by ``EBAY_DEALS_CREDENTIALS``
3. ``~/.config/ebay-deals/credentials.json``

The file is expected to look like::

    {"client_id": "...", "client_secret": "...", "marketplace_id": "EBAY_US"}

Keep it out of version control; ``credentials.json`` is already in
``.gitignore``. Nothing in this package logs or echoes a secret: callers get
whether credentials are present, never their value.
"""

from __future__ import annotations

import json
import os

FIELDS = ("client_id", "client_secret", "marketplace_id", "dev_id")

#: Credential field -> environment variable. Explicit, so an unrelated
#: ``CLIENT_ID`` in the environment can never silently take effect.
ENV_NAMES = {
    "client_id": "EBAY_CLIENT_ID",
    "client_secret": "EBAY_CLIENT_SECRET",
    "marketplace_id": "EBAY_MARKETPLACE_ID",
    "dev_id": "EBAY_DEV_ID",
}


def credentials_path() -> str:
    return os.environ.get("EBAY_DEALS_CREDENTIALS") or os.path.join(
        os.path.expanduser("~"), ".config", "ebay-deals", "credentials.json"
    )


def load_credentials(path: str | None = None) -> dict:
    """Return credentials as a dict. Missing values are empty strings."""
    creds = {"client_id": "", "client_secret": "", "marketplace_id": "EBAY_US", "dev_id": ""}
    for key in creds:
        env = os.environ.get(ENV_NAMES[key])
        if env:
            creds[key] = env
    file_path = path or credentials_path()
    if os.path.exists(file_path):
        try:
            with open(file_path, errors="replace") as handle:
                data = json.load(handle)
            for key in creds:
                # An explicit environment variable always wins over the file.
                if os.environ.get(ENV_NAMES[key]):
                    continue
                if data.get(key):
                    creds[key] = str(data[key])
        except (OSError, ValueError):
            pass
    return creds


def has_credentials(path: str | None = None) -> bool:
    creds = load_credentials(path)
    return bool(creds["client_id"] and creds["client_secret"])


def describe() -> dict:
    """Report where credentials came from without revealing any value."""
    creds = load_credentials()
    return {
        "configured": bool(creds["client_id"] and creds["client_secret"]),
        "credentials_file": credentials_path(),
        "credentials_file_exists": os.path.exists(credentials_path()),
        "marketplace_id": creds["marketplace_id"],
        "from_environment": bool(os.environ.get("EBAY_CLIENT_ID")),
    }
