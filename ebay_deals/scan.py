"""Scan orchestration: search categories, then validate the best candidates.

A run is two phases with different pacing:

* search phase -- one request per category, ~25s apart;
* validate phase -- one item page per candidate, ~18s apart.

The two phases are separate on purpose. A blocked session costs minutes, and the
validated item pages are what the report depends on, so the valuable phase runs
with the smallest number of requests.
"""

from __future__ import annotations

import os
import time

from . import config, rank
from .parse import parse_cards, parse_item
from .store import Store
from .transport import Blocked, BrowseApiTransport, HtmlTransport

# -- Browse API payload mapping ------------------------------------------------
# The Browse API is the primary backend, so its records must carry the same
# fields the HTML parser produces; otherwise ranking and reports silently drop
# every API hit (a None price means no delivered total, so no rank).


def _money(node) -> float | None:
    """Float from a Browse API money block (``{"value": ...}`` or a bare number)."""
    if isinstance(node, dict):
        node = node.get("value")
    try:
        return float(node)
    except (TypeError, ValueError):
        return None


def _api_shipping(item: dict) -> float | None:
    """Lowest shipping cost offered for an item. None means unknown."""
    costs = []
    for option in item.get("shippingOptions") or []:
        cost = _money(option.get("shippingCost") or {})
        if cost is not None:
            costs.append(cost)
    return min(costs) if costs else None


def _is_shortlistable(card: dict) -> bool:
    """A card is worth an item fetch only if it has a real price and is not pickup."""
    if card.get("shipping") == "pickup":
        return False
    return card.get("price") is not None and card["price"] > 0


def _cheapest_first(cards: list[dict]) -> list[dict]:
    """Sort by delivered cost. Unknown shipping sorts as free, so a priced card
    with no shipping figure is not hidden behind one that costs money."""
    return sorted(cards, key=lambda c: c["price"] + (c.get("shipping") or 0.0))


def _api_card(item: dict) -> dict:
    return {
        "item_id": item.get("itemId"),
        "title": item.get("title"),
        "price": _money(item.get("price")),
        "shipping": _api_shipping(item),
        "url": item.get("itemWebUrl"),
        "condition": item.get("condition"),
    }


def _api_item(payload: dict, item_id: str) -> dict:
    price = _money(payload.get("price"))
    low = _money(payload.get("lowPrice"))
    high = _money(payload.get("highPrice"))
    record = {
        "kind": "item",
        "source": "browse_api",
        "item_id": item_id,
        "title": payload.get("title"),
        "url": payload.get("itemWebUrl"),
        "price": price,
        "list_low": low,
        "list_high": high,
        "offer_count": payload.get("offerCount"),
        "shipping": _api_shipping(payload),
        "item_condition": payload.get("condition"),
        "description": (payload.get("shortDescription") or "")[:400],
        "seller": payload.get("seller"),
        "has_variations": bool(payload.get("variations")),
    }
    if payload.get("imageDescriptions"):
        record["image"] = (payload["imageDescriptions"][0] or {}).get("imageUrl")
    # A lowPrice/highPrice range means variations exist even when the summary
    # omitted them, so treat the range floor as the comparable price.
    if record["has_variations"] or (low is not None and high is not None and low != high):
        record["has_variations"] = True
        record["price_floor"] = low if low is not None else price
        record["price_ceiling"] = high
    if record["shipping"] is not None and record["price"] is not None:
        record["delivered"] = round(record["price"] + record["shipping"], 2)
    if record["shipping"] is not None and record.get("price_floor") is not None:
        record["delivered_floor"] = round(record["price_floor"] + record["shipping"], 2)
    return record


class DealScanner:
    def __init__(
        self,
        state_dir: str | None = None,
        delay: float | None = None,
        log=print,
        backend: str = "auto",
    ):
        self.state_dir = state_dir or os.environ.get(
            "EBAY_DEALS_STATE", os.path.join(os.path.expanduser("~"), ".cache", "ebay-deals")
        )
        self.store = Store(self.state_dir)
        self.log = log
        self.api = BrowseApiTransport(log=log)
        if backend == "api" or (backend == "auto" and self.api.configured):
            self.backend = "api"
        else:
            self.backend = "html"
        self.html = HtmlTransport(self.state_dir, delay=delay, log=log)

    # -- phase 1 -----------------------------------------------------------
    def search_category(self, category_key: str, run_path: str) -> list[dict]:
        category = config.get_category(category_key)
        if self.backend == "api":
            payload = self.api.search(category.query)
            cards = [_api_card(item) for item in payload.get("itemSummaries") or []]
        else:
            response = self.html.search(category.query, cache_key=f"search-{category.key}")
            cards = parse_cards(response.text)
        for card in cards:
            card["category"] = category.key
            card["category_label"] = category.label
        self.store.append(
            {"kind": "search", "category": category.key, "query": category.query, "cards": cards},
            run_path,
        )
        self.log(f"  [{category.key}] {len(cards)} cards")
        return cards

    # -- phase 2 -----------------------------------------------------------
    def validate(self, item_ids: list[str], run_path: str) -> list[dict]:
        records: list[dict] = []
        budget = config.MAX_ITEM_FETCHES_PER_RUN
        for position, item_id in enumerate(item_ids[:budget], 1):
            self.log(f"  [{position}/{min(len(item_ids), budget)}] {item_id}")
            try:
                if self.backend == "api":
                    payload = self.api.item(item_id)
                    record = _api_item(payload, item_id)
                else:
                    response = self.html.item(item_id)
                    record = parse_item(response.text, item_id)
                    record["kind"] = "item"
            except Blocked as exc:
                self.log(f"  blocked, stopping validation: {exc}")
                self.store.append(
                    {"kind": "blocked", "item_id": item_id, "error": str(exc)}, run_path
                )
                break
            self.store.append(record, run_path)
            records.append(record)
            time.sleep(config.ITEM_DELAY_SECONDS)
        return records

    # -- full run ----------------------------------------------------------
    def run(
        self, categories: list[str] | None = None, per_category: int = 8, validate: bool = True
    ) -> dict:
        run_path = self.store.run_path()
        config.load_categories()
        keys = categories or [c.key for c in config.all_categories()]
        self.log(f"run -> {run_path}  backend={self.backend}")
        summary: dict = {
            "run": run_path,
            "backend": self.backend,
            "categories": {},
            "blocked": None,
            "validated": [],
        }
        shortlist: list[str] = []
        for key in keys:
            try:
                cards = self.search_category(key, run_path)
            except Blocked as exc:
                summary["blocked"] = {"category": key, "error": str(exc)}
                self.log(f"blocked during search of {key}: {exc}")
                # Persist the gap: a partial run must leave a record of where it
                # stopped, or the report reads as exhaustive when it is not.
                self.store.append(
                    {"kind": "blocked", "phase": "search", "category": key, "error": str(exc)},
                    run_path,
                )
                break
            picked = _cheapest_first([c for c in cards if _is_shortlistable(c)])[:per_category]
            summary["categories"][key] = {
                "label": config.get_category(key).label,
                "cards": len(cards),
                "shortlist": [
                    {
                        "item_id": c["item_id"],
                        "title": c["title"],
                        "search_price": c["price"],
                        "search_shipping": c["shipping"],
                    }
                    for c in picked
                ],
            }
            shortlist.extend(c["item_id"] for c in picked)
        if validate and shortlist:
            # dict.fromkeys de-duplicates while preserving first-seen order
            ordered = list(dict.fromkeys(shortlist))
            self.log(f"validating {len(ordered)} unique candidates")
            records = self.validate(ordered, run_path)
            summary["validated"] = [r.get("item_id") for r in records]
            summary["top"] = [
                {
                    "item_id": r.get("item_id"),
                    "title": r.get("title"),
                    "delivered": rank.delivered_price(r),
                    "score": rank.score(r),
                }
                for r in rank.rank(records)
            ]
        return summary
