"""Zero-dependency MCP server exposing the deal scanner over stdio.

Why not the official SDK: this box has no pip and Python 3.14, so a
dependency-based install is not reproducible. MCP is JSON-RPC 2.0 over stdio,
and the subset needed for tools is small, so it is implemented directly. Swap in
``mcp.server.fastmcp`` if you later want the SDK; ``TOOLS`` below maps 1:1 onto
tool functions.

Tools exposed:

* ``list_categories``   - the category/query matrix
* ``search_deals``      - run a paced scan of one or more categories
* ``get_deal``          - validated detail for one item id (variation table)
* ``top_deals``         - best cached, validated deals, ranked
* ``scan_status``       - coverage, request counts, blocked events
* ``methodology``       - how collection works and its limits

Run with::

    python3 -m ebay_deals.mcp_server
"""

from __future__ import annotations

import json
import sys
import traceback

from . import config, rank
from .parse import parse_item
from .scan import DealScanner
from .store import Store
from .transport import Blocked

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "ebay-deals", "version": "1.0.0"}

TOOLS = [
    {
        "name": "list_categories",
        "description": "List deal categories with the eBay query and what to verify for each.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "search_deals",
        "description": (
            "Run a paced, polite scan of the given categories and return ranked "
            "candidates. This performs real requests to eBay; prefer small "
            "category sets. Stops on 403/challenge rather than evading it."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "categories": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "category keys; omit for all",
                },
                "per_category": {"type": "integer", "default": 8},
                "validate": {"type": "boolean", "default": True},
            },
        },
    },
    {
        "name": "get_deal",
        "description": (
            "Fetch and parse one item page, including the resolved variation "
            "table so the quoted price matches a real configuration."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"item_id": {"type": "string"}},
            "required": ["item_id"],
        },
    },
    {
        "name": "top_deals",
        "description": "Best validated deals from the evidence cache, ranked by score.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
                "max_age": {"type": "integer", "default": 86400},
            },
        },
    },
    {
        "name": "scan_status",
        "description": "Coverage, request counts and any blocked events for the latest run.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "methodology",
        "description": "How collection, pacing, anti-bot handling and variation pricing work.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

METHODOLOGY = {
    "backend_preference": [
        "browse_api (official, needs EBAY_CLIENT_ID/EBAY_CLIENT_SECRET)",
        "html (mobile endpoints, curl, persisted cookie jar)",
    ],
    "pacing": {
        "search_delay_s": config.SEARCH_DELAY_SECONDS,
        "item_delay_s": config.ITEM_DELAY_SECONDS,
        "backoff_s": list(config.BACKOFF_SECONDS),
        "max_item_fetches_per_run": config.MAX_ITEM_FETCHES_PER_RUN,
    },
    "measured_2026_09_28": {
        "www.ebay.com_search": "HTTP 403 consistently",
        "m.ebay.com_search": "works from a cold session; ~13-16 successful requests before 403",
        "urllib_vs_curl": "urllib got 403 on first request; identical curl request succeeded",
        "backoff": "90s did not clear a block; 420s did",
    },
    "never": [
        "solve CAPTCHAs",
        "rotate IPs, hosts, user agents, cookies or accounts",
        "retry through a challenge",
        "label a partial scan as exhaustive",
    ],
    "pricing_rule": (
        "A variation listing's page title and base price describe the default "
        "selection. Always quote a resolved variation row; the audit found titles "
        "claiming 64GB priced from the no-RAM base."
    ),
}


def _scanner() -> DealScanner:
    return DealScanner()


def tool_list_categories(_arguments: dict | None = None) -> dict:
    config.load_categories()
    return {
        "source": config.CATEGORIES_SOURCE,
        "categories": [
            {"key": c.key, "label": c.label, "query": c.query, "verify": list(c.watch)}
            for c in config.all_categories()
        ],
    }


def tool_search_deals(arguments: dict) -> dict:
    scanner = _scanner()
    keys = arguments.get("categories") or None
    per_category = int(arguments.get("per_category", 8))
    validate = bool(arguments.get("validate", True))
    return scanner.run(keys, per_category=per_category, validate=validate)


def tool_get_deal(arguments: dict) -> dict:
    item_id = str(arguments.get("item_id", "")).strip()
    if not item_id.isdigit():
        raise ValueError("item_id must be numeric")
    scanner = _scanner()
    response = scanner.html.item(item_id)
    return parse_item(response.text, item_id)


def tool_top_deals(arguments: dict) -> dict:
    scanner = _scanner()
    store = Store(scanner.state_dir)
    records = list(store.cached_items(max_age=int(arguments.get("max_age", 86400))).values())
    category = arguments.get("category")
    if category:
        records = [r for r in records if r.get("category") == category]
    limit = int(arguments.get("limit", 10))
    return {
        "deals": [
            {
                "item_id": r.get("item_id"),
                "title": (r.get("title") or "")[:120],
                "url": r.get("url"),
                "delivered": rank.delivered_price(r),
                "score": r.get("score"),
                "has_variations": r.get("has_variations"),
                "summary": rank.summarize(r),
            }
            for r in rank.rank(records)[:limit]
        ]
    }


def tool_scan_status(_arguments: dict | None = None) -> dict:
    scanner = _scanner()
    store = Store(scanner.state_dir)
    latest = store.latest_run()
    rows = store.read(latest) if latest else []
    return {
        "state_dir": scanner.state_dir,
        "backend": scanner.backend,
        "latest_run": latest,
        "searches": sum(1 for r in rows if r.get("kind") == "search"),
        "items_validated": sum(1 for r in rows if r.get("kind") == "item"),
        "blocked_events": [r for r in rows if r.get("kind") == "blocked"],
        "html_requests_this_process": scanner.html.request_count,
    }


def tool_methodology(_arguments: dict | None = None) -> dict:
    return METHODOLOGY


HANDLERS = {
    "list_categories": tool_list_categories,
    "search_deals": tool_search_deals,
    "get_deal": tool_get_deal,
    "top_deals": tool_top_deals,
    "scan_status": tool_scan_status,
    "methodology": tool_methodology,
}


def _send(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def handle(request: dict) -> dict | None:
    method = request.get("method")
    request_id = request.get("id")
    if method == "initialize":
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        }
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = request.get("params") or {}
        name = params.get("name")
        handler = HANDLERS.get(name)
        if not handler:
            result = {
                "isError": True,
                "content": [{"type": "text", "text": f"unknown tool {name}"}],
            }
        else:
            try:
                payload = handler(params.get("arguments") or {})
                result = {
                    "content": [
                        {"type": "text", "text": json.dumps(payload, indent=1, default=str)}
                    ]
                }
            except Blocked as exc:
                result = {
                    "isError": True,
                    "content": [{"type": "text", "text": f"eBay refused the request: {exc}"}],
                }
            except Exception as exc:  # surfaced to the caller, not swallowed
                result = {
                    "isError": True,
                    "content": [
                        {
                            "type": "text",
                            "text": f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
                        }
                    ],
                }
    elif method in ("notifications/initialized", "initialized"):
        return None
    elif method == "ping":
        result = {}
    else:
        if request_id is None:
            return None
        _send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"method not found: {method}"},
            }
        )
        return None
    if request_id is None:
        return None
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": f"parse error: {exc}"},
                }
            )
            continue
        response = handle(request)
        if response is not None:
            _send(response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
