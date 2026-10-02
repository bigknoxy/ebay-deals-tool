"""ebay-deals: repeatable, auditable eBay deal hunting.

Public API is the CLI (:mod:`ebay_deals.cli`) and the MCP server
(:mod:`ebay_deals.mcp_server`). Both wrap the same scan engine.
"""

from __future__ import annotations

import os
import re
import subprocess

__all__ = ["__version__", "resolve_version"]

_FALLBACK_VERSION = "0.0.0+unknown"

# Only PEP 440 pre-release spellings. `9.9.9-test` is valid semver but not a
# valid distribution version, so a tag like that must not reach the metadata.
_TAG_VERSION = re.compile(
    r"v?(\d+\.\d+\.\d+"
    r"(?:(?:\.dev|-dev|-rc|-a|-alpha|-b|-beta)\d*|\+[0-9A-Za-z.]+)?)"
)


def _installed_version() -> str | None:
    """Version of the installed distribution, if this is an installed copy."""
    try:
        from importlib.metadata import PackageNotFoundError, version
    except ImportError:  # pragma: no cover - Python < 3.8 only
        return None
    try:
        return version("ebay-deals-tool")
    except PackageNotFoundError:
        return None


def _tag_version() -> str | None:
    """Version of the tag `HEAD` sits on, when running from a git checkout.

    The tag is the single source of truth for a release: main is
    PR-only, so the version cannot be committed to the branch without a bot
    push that branch protection would (correctly) reject.
    """
    if not os.path.isdir(os.path.join(os.path.dirname(__file__), "..", ".git")):
        return None
    try:
        described = subprocess.run(
            ["git", "describe", "--tags", "--exact-match"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    match = _TAG_VERSION.fullmatch(described)
    return match.group(1) if match else None


def resolve_version(installed=None, tagged=None) -> str:
    """Resolve the version, preferring an explicit value then the git tag.

    Split out so the precedence order is testable without a real install or a
    real tag.
    """
    if installed:
        return installed
    if tagged:
        return tagged
    return _FALLBACK_VERSION


__version__ = resolve_version(_installed_version(), _tag_version())
