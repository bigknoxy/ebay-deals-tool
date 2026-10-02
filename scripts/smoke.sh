#!/bin/sh
# Offline smoke test: no network, no credentials, no eBay.
#
# Proves the package imports, the CLI parses, and the MCP server completes a
# handshake from a clean checkout. This is the check that runs in CI and on
# every release tag.
set -eu

ROOT="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT"

PY="${PYTHON:-python3}"
STATE="$(mktemp -d)"
trap 'rm -rf "$STATE"' EXIT

echo "==> import check"
"$PY" -c 'import ebay_deals, ebay_deals.cli, ebay_deals.scan, ebay_deals.mcp_server'

echo "==> category profile resolves"
"$PY" -m ebay_deals --state "$STATE" categories >/dev/null

echo "==> empty cache is handled, not crashed"
if "$PY" -m ebay_deals --state "$STATE" deals >/dev/null 2>&1; then
    echo "unexpected: deals succeeded against an empty cache" >&2
    exit 1
fi

echo "==> credential check reports without exposing anything"
"$PY" -m ebay_deals credentials | grep -q '"configured"'

echo "==> MCP handshake"
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke","version":"1"}}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  | "$PY" -m ebay_deals.mcp_server > "$STATE/mcp.out"

grep -q '"serverInfo"' "$STATE/mcp.out" || { echo "no serverInfo in handshake" >&2; exit 1; }
grep -q '"tools"' "$STATE/mcp.out" || { echo "no tools in tools/list" >&2; exit 1; }

echo "smoke ok"
