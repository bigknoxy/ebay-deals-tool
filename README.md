# ebay-deals

Repeatable eBay deal hunting for the categories tracked in this project:
standalone CPUs, X99/C602 motherboards, CPU+motherboard combos, Dell Precision
workstations, mini-PC barebones, laptop panels for headless builds, and
datacenter GPUs.

Ships two ways to use the same engine:

* a **CLI** for manual runs and report generation;
* a **zero-dependency MCP server** so an assistant can call it as tools.

Read [METHODOLOGY.md](METHODOLOGY.md) before trusting a number. It documents the
pacing, the anti-bot policy, and the variation-pricing trap that caused earlier
reports to be wrong.

## Requirements

Python 3.11+ and `curl` on `PATH`. No third-party packages. (This box has Python
3.14 and no pip, so the MCP server speaks JSON-RPC directly instead of depending
on the `mcp` package.)

## Install

Nothing to install. Optionally set a state directory:

```bash
export EBAY_DEALS_STATE=~/.cache/ebay-deals   # default
```

## Optional: use the official API

With credentials the tool switches to the supported Browse Item API backend:

```bash
export EBAY_CLIENT_ID=...      # eBay developer app
export EBAY_CLIENT_SECRET=...
export EBAY_MARKETPLACE_ID=EBAY_US
```

Without them it uses the slow HTML path, which still works.

## CLI

```bash
# what we track and what to verify per category
python3 -m ebay_deals categories

# broad scan (real requests; prefer a couple of categories at a time)
python3 -m ebay_deals search cpu combo_x99 --per-category 8

# rank what has already been validated, from cache (no requests)
python3 -m ebay_deals deals --limit 10
python3 -m ebay_deals deals --category workstation

# one item, with the full variation table
python3 -m ebay_deals deal <ITEM_ID>

# markdown summary of cached deals
python3 -m ebay_deals report --limit 20 --out deals.md

# coverage of the latest run
python3 -m ebay_deals status
```

## MCP

Register with any MCP client. For opencode, add to `opencode.json`:

```json
{
  "mcp": {
    "ebay-deals": {
      "type": "local",
      "command": ["python3", "-m", "ebay_deals.mcp_server"],
      "environment": {
        "PYTHONPATH": "/path/to/ebay-deals-tool",
        "EBAY_DEALS_STATE": "~/.cache/ebay-deals"
      }
    }
  }
}
```

Tools:

| Tool | Purpose |
|---|---|
| `list_categories` | category keys, queries, and what to verify |
| `search_deals` | run a paced scan; returns ranked candidates |
| `get_deal` | one item page, resolved variation table |
| `top_deals` | best cached validated deals (no requests) |
| `scan_status` | coverage, request counts, blocked events |
| `methodology` | pacing, policy, and pricing rules as data |

Handshake check without a client:

```bash
printf '%s\n' \
 '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}' \
 '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
 '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
 | python3 -m ebay_deals.mcp_server
```

## Layout

```
ebay_deals/
  config.py        category profile loading, pacing constants, limits
  credentials.py   developer credential loading, env/file precedence
  transport.py     Browse API backend + polite curl/cookie-jar HTML backend
  parse.py         search-card and item-page parsers, variation join
  rank.py          transparent scoring and markdown summaries
  store.py         JSONL evidence store, raw HTML cache
  scan.py          two-phase orchestration
  cli.py           command line interface
  mcp_server.py    stdio MCP server (JSON-RPC 2.0, no dependencies)
```

## Policy

No CAPTCHA solving, no proxy/IP/host/user-agent/cookie rotation, no retrying
through a challenge, and no claiming a partial scan is exhaustive. A 403 ends the
run and is recorded as a `blocked` event so gaps stay visible.
