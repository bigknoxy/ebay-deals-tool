"""Scoring and ranking.

Scores are a transparent weighted sum so a buyer can see why a listing ranked
where it did. Heuristics encode the traps found in the 2026-09-23 audit:
variation listings quoted at their base price, "barebone" listings priced like
complete machines, and auction lots that are really parts.
"""

from __future__ import annotations

import re

# What a usable configuration is worth, used only for relative ordering.
# Recognised hardware families. These give a baseline value when a title omits
# the spec that actually matters (a Xeon SR code implies a server CPU; a
# workstation model implies a Xeon platform), so an honest "SR2N7 CPU" listing
# is not scored as if it had no value at all.
FAMILY_HINTS = (
    (re.compile(r"\bxeon\b|\bsr2[nk][0-9a-z]\b|\bsr1[0-9a-z]{2}\b", re.I), 0.8),
    (re.compile(r"\bt5810\b|\bt3600\b|\bt1700\b|\b5820\b|\bz440\b|\bz420\b|\bz640\b", re.I), 0.7),
    (re.compile(r"\boptiplex\b|\belitedesk\b|\bthinkcentre\b|\bprodesk\b", re.I), 0.45),
    (re.compile(r"\btesla [mp]\d0\b|\bquadro\b", re.I), 0.7),
    (re.compile(r"\bx99\b|\bc602\b|\bx79\b|\bx58\b|motherboard|mainboard", re.I), 0.5),
)

# eBay titles hyphenate specs constantly ("14-Core", "64-GB"), so every
# separator here is [\s-]? rather than \s?.
VALUE_HINTS = (
    (re.compile(r"\b(64|128)[\s-]?gb\b", re.I), 1.0),
    (re.compile(r"\b(32)[\s-]?gb\b", re.I), 0.8),
    (re.compile(r"\b(16)[\s-]?gb\b", re.I), 0.6),
    (re.compile(r"\b(24[\s-]?gb|24g)\b", re.I), 1.0),
    (re.compile(r"\b(16[\s-]?gb|16g)\b.*\b(gpu|quadro|tesla|radeon)", re.I), 0.9),
    (re.compile(r"\b(10|12|14|16|20|24|28)[\s-]?core\b", re.I), 0.9),
    (re.compile(r"\b(8)[\s-]?core\b", re.I), 0.5),
)

# Cost of risk, subtracted from the score.
PENALTIES = (
    (re.compile(r"for parts|not working|untested|broken|as is|for repair|project", re.I), 0.55),
    (re.compile(r"\blot\b|\bcollection\b|\bparts only\b", re.I), 0.5),
    (re.compile(r"repair|refurb(?!ished)?\b", re.I), 0.3),
    (re.compile(r"^tested", re.I), -0.1),
    (re.compile(r"bench ?test", re.I), -0.15),
)

# Signals that raise confidence.
BONUSES = (
    (re.compile(r"tested|works|working|bench ?test", re.I), 0.2),
    (re.compile(r"ssd|nvme|m\.2", re.I), 0.12),
    (re.compile(r"\b\d{4,5}\s?w\b|\b\d{4,5}w\b", re.I), 0.05),
)


def _text(record: dict) -> str:
    parts = [record.get("title") or "", (record.get("description") or "")[:200]]
    for variant in (record.get("variants") or [])[:12]:
        parts.extend(variant.get("config") or [])
    return " ".join(parts)


def score(record: dict, delivered: float | None = None) -> float:
    """Score a listing. Higher is better. Delivered cost drives the penalty."""
    text = _text(record)
    value = 0.0
    for pattern, weight in VALUE_HINTS:
        if pattern.search(text):
            value = max(value, weight)
    if value == 0.0:
        for pattern, weight in FAMILY_HINTS:
            if pattern.search(text):
                value = max(value, weight)
        if value == 0.0:
            # No recognisable value signal at all: rank below anything identified.
            value -= 0.25
    for pattern, weight in PENALTIES:
        if pattern.search(text):
            value -= weight
    for pattern, weight in BONUSES:
        if pattern.search(text):
            value += weight
    if record.get("availability") in (None, "InStock"):
        value += 0.05
    else:
        value -= 0.3
    if delivered is None:
        delivered = record.get("delivered_floor") or record.get("delivered")
    if delivered is None:
        return value
    # Log-ish cost curve: cheap listings keep most of the value, expensive ones
    # need to be much better to outrank them.
    if delivered <= 0:
        return value
    cost_penalty = min(1.0, (delivered ** 0.35) / 9.0)
    return round(value - cost_penalty, 4)


def delivered_price(record: dict) -> float | None:
    for key in ("delivered_floor", "delivered"):
        if record.get(key) is not None:
            return float(record[key])
    return None


def rank(records: list[dict]) -> list[dict]:
    scored = []
    for record in records:
        price = delivered_price(record)
        if price is None:
            continue
        if record.get("availability") not in (None, "InStock"):
            continue
        row = dict(record)
        row["delivered_total"] = price
        row["score"] = score(record, price)
        row["title_short"] = (record.get("title") or "")[:110]
        scored.append(row)
    return sorted(scored, key=lambda r: (-r["score"], r["delivered_total"]))


def summarize(record: dict) -> str:
    lines = [f"{record.get('title') or record.get('item_id')}"]
    lines.append(f"  item {record.get('item_id')}  {record.get('url', '')}")
    price = delivered_price(record)
    if price is not None:
        lines.append(f"  delivered ${price:,.2f} (pre-tax)")
    if record.get("has_variations"):
        lines.append(f"  variations ({len(record.get('variants') or [])}):")
        for variant in (record.get("variants") or [])[:8]:
            flag = " OUT OF STOCK" if variant.get("out_of_stock") else ""
            qty = "" if variant.get("qty") is None else f" qty={variant['qty']}"
            lines.append(
                f"    ${variant['price']:,.2f}{qty}{flag} - "
                f"{'; '.join(variant.get('config') or []) or 'unlabelled'}"
            )
    else:
        lines.append("  no variations: single price applies")
    meta = []
    for key, label in (("item_condition", "condition"), ("seller", "seller"),
                       ("feedback", "feedback"), ("availability", "availability")):
        if record.get(key):
            meta.append(f"{label}={record[key]}")
    if meta:
        lines.append("  " + ", ".join(meta))
    return "\n".join(lines)
