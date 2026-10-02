"""HTML parsers for eBay search results and item pages.

The important lesson encoded here: on a variation listing the page title and the
JSON-LD ``price`` describe the *default or base* selection, which is frequently
not the configuration a buyer's search text implies. The 2026-09-23 audit found
listing titles like "... 64GB RAM" priced from $149.88 when 64GB was
actually $429.88. Always resolve a real variant row (or state that the listing
has no variations) before quoting a price.

Two data sources are used per item page:

* JSON-LD (``application/ld+json``) for title, buy-box price, availability and
  shipping. Robust because it is generated for search engines.
* The embedded pick-list JSON (``selectMenus`` / ``menuItemMap``) joined to the
  per-variation price map keyed by variation id, which yields a price per real
  configuration plus its stock count.
"""

from __future__ import annotations

import html
import json
import re

CHALLENGE_MARKERS = ("Pardon Our Interruption", "Robot or Human?")


def is_blocked(raw: str) -> str | None:
    if not raw:
        return "empty"
    if len(raw) < 8000:
        return "short"
    for marker in CHALLENGE_MARKERS:
        if marker in raw:
            return "challenge"
    if "Error Page" in raw[:4000]:
        return "error"
    return None


def _strip_tags(fragment: str) -> str:
    text = re.sub(r"<script.*?</script>", " ", fragment, flags=re.S)
    text = re.sub(r"<[^>]+>", "|", text)
    return html.unescape(re.sub(r"(\|\s*)+", "|", text)).strip("|")


CONDITIONS = {
    "brand new",
    "new",
    "pre-owned",
    "open box",
    "for parts or not working",
    "refurbished",
    "new other",
    "new open box",
    "pre-owned (refurbished)",
    "seller refurbished",
    "manufacturer refurbished",
}


def parse_cards(raw: str) -> list[dict]:
    """Parse search result cards.

    Cards are delimited by ``data-listingid=<id>`` attributes. Text extraction is
    used for the fields because the responsive markup renames classes freely;
    titles are taken as the longest text run that is not markup.
    """
    hits = [m.start() for m in re.finditer(r"data-listingid=(\d{9,12})(?![\d])", raw)]
    cards: list[dict] = []
    for index, start in enumerate(hits):
        end = hits[index + 1] if index + 1 < len(hits) else min(len(raw), start + 12000)
        chunk = raw[start:end]
        item_id = re.match(r"data-listingid=(\d{9,12})", chunk).group(1)
        text = _strip_tags(chunk)
        parts = [p.strip() for p in text.split("|") if p.strip() and p.strip() != "derosnopS"]
        prices = re.findall(r"\$([\d,]+(?:\.\d{2})?)", text)
        if not prices:
            continue
        shipping = None
        match = re.search(r"\+?\$([\d,]+\.\d{2})\s+delivery", text)
        if match:
            shipping = float(match.group(1).replace(",", ""))
        elif re.search(r"free delivery", text, re.I):
            shipping = 0.0
        elif re.search(r"local pickup", text, re.I):
            shipping = "pickup"
        candidates = [p for p in parts if "<li" not in p and "class=" not in p and len(p) > 12]
        title = max(candidates, key=len) if candidates else (parts[0] if parts else "")
        bids = None
        match = re.search(r"(\d+)\s+bid", text, re.I)
        if match:
            bids = int(match.group(1))
        condition = next((p for p in parts if p.lower() in CONDITIONS), None)
        cards.append(
            {
                "item_id": item_id,
                "title": title,
                "price": float(prices[0].replace(",", "")),
                "other_prices": [float(p.replace(",", "")) for p in prices[1:4]],
                "shipping": shipping,
                "bids": bids,
                "condition": condition,
                "url": f"https://www.ebay.com/itm/{item_id}",
            }
        )
    return cards


def _json_after(raw: str, key: str):
    """Extract the JSON value that follows ``"key":`` using bracket matching."""
    match = re.search(r'"' + re.escape(key) + r'"\s*:\s*', raw)
    if not match:
        return None
    start = match.end()
    if raw[start] not in "[{":
        return None
    opener = raw[start]
    closer = "]" if opener == "[" else "}"
    depth = 0
    in_string = False
    escaped = False
    for pos in range(start, len(raw)):
        char = raw[pos]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(raw[start : pos + 1])
                except Exception:
                    return None
    return None


def _json_ld_nodes(raw: str) -> list[dict]:
    nodes: list[dict] = []
    for match in re.finditer(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", raw, re.S):
        try:
            data = json.loads(match.group(1).strip())
        except Exception:
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                kind = node.get("@type")
                types = kind if isinstance(kind, list) else [kind]
                if "Product" in types:
                    nodes.append(node)
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
    return nodes


def _offers(product: dict | None):
    if not product:
        return None
    offers = product.get("offers")
    if isinstance(offers, dict):
        return offers
    if isinstance(offers, list) and offers:
        return offers[0]
    return None


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_item(raw: str, item_id: str) -> dict:
    """Parse an item page into a deal record with a resolved variation table."""
    record: dict = {
        "item_id": item_id,
        "url": f"https://www.ebay.com/itm/{item_id}",
        "source": "html",
    }
    products = _json_ld_nodes(raw)
    product = products[0] if products else None
    if product:
        record["title"] = product.get("name")
        record["description"] = (product.get("description") or "")[:400]
    offer = _offers(product)
    if isinstance(offer, dict):
        if "lowPrice" in offer:
            record["list_low"] = _to_float(offer.get("lowPrice"))
            record["list_high"] = _to_float(offer.get("highPrice"))
            record["offer_count"] = offer.get("offerCount")
        else:
            record["price"] = _to_float(offer.get("price"))
            record["currency"] = offer.get("priceCurrency")
        availability = offer.get("availability") or ""
        record["availability"] = availability.rsplit("/", 1)[-1] if availability else None
        details = offer.get("shippingDetails")
        if isinstance(details, list):
            details = details[0] if details else None
        if isinstance(details, dict):
            record["shipping"] = _to_float((details.get("shippingRate") or {}).get("value"))
        condition = offer.get("itemCondition") or ""
        record["item_condition"] = condition.rsplit("/", 1)[-1]
        seller = offer.get("seller")
        record["seller"] = seller.get("name") if isinstance(seller, dict) else seller

    menus = _json_after(raw, "selectMenus")
    menu_items = _json_after(raw, "menuItemMap")
    labels: dict[str, list[str]] = {}
    if menus and menu_items:
        for menu in menus:
            group = menu.get("displayLabel")
            for value_id in menu.get("menuItemValueIds") or []:
                entry = menu_items.get(str(value_id))
                if not entry or not group:
                    continue
                display = entry.get("displayName") or entry.get("valueName")
                for var_id in entry.get("matchingVariationIds") or []:
                    labels.setdefault(str(var_id), []).append(f"{group}: {display}")

    pattern = re.compile(
        r'"(\d{9,12})":\{"binModel":\{"price":\{"_type":"TextualDisplayValue",'
        r'"value":\{"value":([\d.]+),"currency":"([A-Z]{3})"\}'
    )
    matches = list(pattern.finditer(raw))
    variants = []
    for index, match in enumerate(matches):
        seg_end = matches[index + 1].start() if index + 1 < len(matches) else match.end() + 6000
        segment = raw[match.start() : seg_end]
        var_id = match.group(1)
        qty = re.search(r'"maxQuantity":\{"_type":"TextualDisplayValue","value":(\d+)', segment)
        variants.append(
            {
                "variation_id": var_id,
                "price": float(match.group(2)),
                "currency": match.group(3),
                "qty": int(qty.group(1)) if qty else None,
                "out_of_stock": '"outOfStock":true' in segment[:4000],
                "config": labels.get(var_id, []),
            }
        )
    if variants:
        variants = sorted(variants, key=lambda v: v["price"])
        record["variants"] = variants
        record["has_variations"] = True
        # Cheapest in-stock row is the honest floor for the listing. Note the
        # floor is often the least useful configuration, which is exactly the
        # trap the base-price rule warns about.
        in_stock = [v for v in variants if not v["out_of_stock"]]
        if in_stock:
            record["cheapest_variant"] = in_stock[0]
            record["price_floor"] = in_stock[0]["price"]
        record["price_ceiling"] = max((v["price"] for v in variants), default=None)
    else:
        record["has_variations"] = False
    if record.get("price") is None and len(record.get("variants") or []) == 1:
        record["price"] = record["variants"][0]["price"]
    match = re.search(r'"sellerName":"([^"]{2,60})"', raw)
    if match:
        record["seller_name"] = match.group(1)
    match = re.search(r"([\d,]+)\s+feedback score", raw)
    if match:
        record["feedback"] = match.group(1).replace(",", "")
    record["ends"] = None
    if "this listing has ended" in raw.lower() or "listing ended" in raw.lower():
        record["ends"] = "ended"
    if record.get("shipping") is not None and record.get("price") is not None:
        record["delivered"] = round(record["price"] + record["shipping"], 2)
    if record.get("price_floor") is not None and record.get("shipping") is not None:
        record["delivered_floor"] = round(record["price_floor"] + record["shipping"], 2)
    return record
