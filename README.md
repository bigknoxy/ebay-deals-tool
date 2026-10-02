# ebay-deals

Repeatable, auditable eBay deal hunting. A CLI for manual runs, and a
zero-dependency MCP server so an assistant can call the same engine as tools.

<p>
  <a href="https://github.com/bigknoxy/ebay-deals-tool/actions/workflows/ci.yml">
    <img src="https://img.shields.io/github/actions/workflow/status/bigknoxy/ebay-deals-tool/ci.yml?branch=main&style=for-the-badge&logo=github&label=CI" alt="CI">
  </a>
  <a href="https://github.com/bigknoxy/ebay-deals-tool/actions/workflows/security.yml">
    <img src="https://img.shields.io/github/actions/workflow/status/bigknoxy/ebay-deals-tool/security.yml?branch=main&style=for-the-badge&logo=github&label=Security" alt="Security">
  </a>
  <a href="https://github.com/bigknoxy/ebay-deals-tool/releases/latest">
    <img src="https://img.shields.io/github/v/release/bigknoxy/ebay-deals-tool?include_prereleases&style=for-the-badge&logo=python&color=blue" alt="Release">
  </a>
  <a href="https://github.com/bigknoxy/ebay-deals-tool/blob/main/LICENSE">
    <img src="https://img.shields.io/github/license/bigknoxy/ebay-deals-tool?style=for-the-badge&logo=opensourceinitiative&label=License" alt="License">
  </a>
</p>

Every badge above is a live endpoint that reads GitHub state, so none of them
needs editing by hand.

## Install

```bash
pipx install git+https://github.com/bigknoxy/ebay-deals-tool.git
```

Or run it straight from a clone with no install at all:

```bash
git clone https://github.com/bigknoxy/ebay-deals-tool.git && python3 -m ebay_deals --help
```

Python 3.11+ and `curl`. No third-party packages at runtime.

## Configure

The tool ships generic defaults. Point it at your own categories to scan
something specific:

```bash
export EBAY_DEALS_CATEGORIES=~/.config/ebay-deals/categories.json
```

Start from [`categories.example.json`](categories.example.json). Keep that file
outside the repository: it is your shopping list, not project data. See
[Privacy](#privacy).

Optional, for the supported Browse API instead of the slow HTML path:

```bash
export EBAY_CLIENT_ID=...       # eBay developer app: App ID
export EBAY_CLIENT_SECRET=...   # Client Secret
export EBAY_MARKETPLACE_ID=EBAY_US
```

Without credentials the HTML backend still works.

## CLI

```bash
# categories, their queries, and what to verify per category
ebay-deals categories

# paced scan (real requests; a couple of categories at a time)
ebay-deals search cpu combo_x99 --per-category 8

# rank already-validated results from cache, no requests
ebay-deals deals --limit 10
ebay-deals deals --category workstation

# one item with its full variation table
ebay-deals deal <ITEM_ID>

# markdown report of cached deals
ebay-deals report --limit 20 --out deals.md

# coverage of the latest run
ebay-deals status
```

`python3 -m ebay_deals <command>` is equivalent to `ebay-deals <command>`.

## MCP

```json
{
  "mcp": {
    "ebay-deals": {
      "type": "local",
      "command": ["python3", "-m", "ebay_deals.mcp_server"],
      "environment": { "EBAY_DEALS_CATEGORIES": "~/.config/ebay-deals/categories.json" }
    }
  }
}
```

| Tool | Purpose |
|---|---|
| `list_categories` | category keys, queries, and what to verify |
| `search_deals` | run a paced scan; returns ranked candidates |
| `get_deal` | one item page with its resolved variation table |
| `top_deals` | best cached validated deals, no requests |
| `scan_status` | coverage, request counts, blocked events |
| `methodology` | pacing, policy, and pricing rules as data |

## Privacy

This repository is public, so it ships nothing personal. A repo scanner runs on
every commit and in CI and fails the build on credentials, home paths, shipping
ZIP codes, or specific machine models:

```bash
python3 -m tools.privacy_scan
```

Your categories, credentials, reports, and HTML cache all live under
`~/.config/ebay-deals/` and `~/.cache/ebay-deals/`, outside the checkout.

## Development

```bash
python3 -m tests.run                    # 54 offline tests
python3 -m tools.privacy_scan           # secret and PII gate
PYTHON=path/to/python sh scripts/coverage.sh   # 80% floor
sh scripts/smoke.sh                     # end-to-end without network
```

## Design notes

- **Zero runtime dependencies.** Standard library only, so it installs anywhere
  and the MCP server speaks JSON-RPC directly.
- **Variations are the real price.** A card showing "$27.15" often hides four
  other price rows. The parser joins the variation table and the ranker scores
  the delivered price you would actually pay.
- **A blocked scan stays visible.** HTTP 403 ends the run and is recorded as a
  `blocked` event rather than being retried through a challenge.
- **No CAPTCHA solving, no proxy or user-agent rotation.** See
  [METHODOLOGY.md](METHODOLOGY.md) for pacing and the pricing traps.

## License

MIT. See [LICENSE](LICENSE).
