"""Command line interface.

Examples::

    python3 -m ebay_deals categories
    python3 -m ebay_deals search cpu combo_x99 --per-category 8
    python3 -m ebay_deals deals --category cpu --limit 5
    python3 -m ebay_deals deal 236870149132
    python3 -m ebay_deals report --out report.md
    python3 -m ebay_deals status
"""

from __future__ import annotations

import argparse
import json
import sys

from . import config, rank
from .parse import parse_item
from .scan import DealScanner
from .store import Store
from .transport import Blocked


def cmd_categories(args) -> int:
    config.load_categories()
    for category in config.all_categories():
        print(f"{category.key:24s} {category.label}")
        print(f"{'':24s} query: {category.query}")
        if category.watch:
            print(f"{'':24s} check: {', '.join(category.watch)}")
    return 0


def cmd_search(args) -> int:
    scanner = DealScanner(delay=args.delay, backend=args.backend)
    config.load_categories()
    keys = args.categories or [c.key for c in config.all_categories()]
    summary = scanner.run(keys, per_category=args.per_category, validate=not args.no_validate)
    print(json.dumps(summary, indent=1, default=str)[:4000])
    return 0


def cmd_deals(args) -> int:
    store = Store(store_path(args))
    records = []
    for record in store.cached_items(max_age=args.max_age).values():
        if args.category:
            cat = record.get("category")
            if cat != args.category:
                continue
        records.append(record)
    ranked = rank.rank(records)
    if not ranked:
        print("no validated records in cache; run `search` first", file=sys.stderr)
        return 1
    for row in ranked[: args.limit]:
        print(rank.summarize(row))
        print()
    return 0


def store_path(args) -> str:
    """Explicit --state wins, then $EBAY_DEALS_STATE, then the default location."""
    import os

    explicit = getattr(args, "state", None)
    if explicit:
        return explicit
    return os.environ.get(
        "EBAY_DEALS_STATE", os.path.join(os.path.expanduser("~"), ".cache", "ebay-deals")
    )


def cmd_deal(args) -> int:
    scanner = DealScanner(backend=args.backend)
    try:
        response = scanner.html.item(args.item_id)
    except Blocked as exc:
        print(f"blocked: {exc}", file=sys.stderr)
        return 1
    record = parse_item(response.text, args.item_id)
    print(json.dumps(record, indent=1, default=str))
    return 0


def cmd_report(args) -> int:
    store = Store(store_path(args))
    records = list(store.cached_items(max_age=args.max_age).values())
    ranked = rank.rank(records)
    lines = [f"# eBay deals (cache, max age {args.max_age}s)", ""]
    for row in ranked[: args.limit]:
        lines.append(rank.summarize(row))
        lines.append("")
    text = "\n".join(lines)
    if args.out:
        with open(args.out, "w") as handle:
            handle.write(text)
        print(f"wrote {args.out} ({len(ranked)} deals)")
    else:
        print(text)
    return 0


def cmd_status(args) -> int:
    store = Store(store_path(args))
    latest = store.latest_run()
    print(f"state dir : {store.state_dir}")
    print(f"latest run: {latest}")
    rows = store.read(latest) if latest else []
    searches = sum(1 for r in rows if r.get("kind") == "search")
    items = sum(1 for r in rows if r.get("kind") == "item")
    blocked = [r for r in rows if r.get("kind") == "blocked"]
    print(f"searches  : {searches}")
    print(f"items     : {items}")
    print(f"blocked   : {len(blocked)}")
    for row in blocked[:3]:
        print(f"  {row.get('item_id')}: {row.get('error')}")
    return 0


def cmd_credentials(args) -> int:
    """Report whether developer credentials resolve, without revealing them."""
    from .credentials import describe

    report = describe()
    if args.check:
        from .transport import Blocked, BrowseApiTransport

        api = BrowseApiTransport()
        results = {}
        for host in ("api.ebay.com", "api.sandbox.ebay.com"):
            api.host = host
            api._token = None
            try:
                api._access_token()
                results[host] = "OK"
            except Blocked as exc:
                results[host] = str(exc)
            except Exception as exc:  # network, curl missing
                results[host] = f"{type(exc).__name__}: {exc}"
        report["oauth"] = results
    print(json.dumps(report, indent=2))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="ebay_deals")
    parser.add_argument("--state", default=None, help="state directory")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("categories").set_defaults(func=cmd_categories)
    p_creds = sub.add_parser("credentials")
    p_creds.add_argument(
        "--check",
        action="store_true",
        help="attempt a real OAuth token request; prints status only",
    )
    p_creds.set_defaults(func=cmd_credentials)

    p_search = sub.add_parser("search")
    p_search.add_argument("categories", nargs="*")
    p_search.add_argument("--per-category", type=int, default=8)
    p_search.add_argument("--delay", type=float, default=None)
    p_search.add_argument("--no-validate", action="store_true")
    p_search.add_argument("--backend", choices=("auto", "html", "api"), default="auto")
    p_search.set_defaults(func=cmd_search)

    p_deals = sub.add_parser("deals")
    p_deals.add_argument("--category", default=None)
    p_deals.add_argument("--limit", type=int, default=10)
    p_deals.add_argument("--max-age", type=int, default=86400)
    p_deals.set_defaults(func=cmd_deals)

    p_deal = sub.add_parser("deal")
    p_deal.add_argument("item_id")
    p_deal.add_argument("--backend", choices=("auto", "html", "api"), default="auto")
    p_deal.set_defaults(func=cmd_deal)

    p_report = sub.add_parser("report")
    p_report.add_argument("--limit", type=int, default=20)
    p_report.add_argument("--max-age", type=int, default=86400)
    p_report.add_argument("--out", default=None)
    p_report.set_defaults(func=cmd_report)

    p_status = sub.add_parser("status")
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    if args.state:
        import os

        os.environ["EBAY_DEALS_STATE"] = args.state
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
