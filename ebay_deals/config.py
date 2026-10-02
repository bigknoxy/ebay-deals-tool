"""Category definitions, config loading, and conservative fetch defaults.

Categories are **data, not code**. A repository checkout ships a generic example
(``categories.example.json``); anything personal belongs in a config file
outside the checkout, so that a shared repository never encodes one buyer's
hardware list or shipping address.

Resolution order for the category file:

1. ``$EBAY_DEALS_CATEGORIES``
2. ``~/.config/ebay-deals/categories.json``
3. the built-in generic defaults in this module

Every number here is a deliberate, conservative choice. See METHODOLOGY.md for
the measurements behind them. Do not lower the pacing values to "go faster":
eBay answers a burst with HTTP 403 for the whole session, which costs more time
than slow scanning ever saves.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

# Ship-to ZIP is optional and personal; leave it unset unless you want it echoed
# into reports. Shipping itself comes from the item page as returned by the
# server and is reported regardless.
SHIP_TO_ZIP = os.environ.get("EBAY_SHIP_ZIP") or None

# --- Pacing -----------------------------------------------------------------
# Measured on 2026-09-28 from a cold session: ~13-16 successful requests before
# eBay returns 403 for the session. 90s of backoff was not enough to clear it;
# 420s was. We therefore stay well under the burst threshold and treat 403 as a
# hard stop.
SEARCH_DELAY_SECONDS = float(os.environ.get("EBAY_SEARCH_DELAY", "25"))
ITEM_DELAY_SECONDS = float(os.environ.get("EBAY_ITEM_DELAY", "18"))
BACKOFF_SECONDS = (0, 90, 420)

# Conservative defaults. Overridable per call, not globally.
DEFAULT_ITEMS_PER_SEARCH = 60
MAX_ITEM_FETCHES_PER_RUN = int(os.environ.get("EBAY_MAX_ITEM_FETCHES", "30"))


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    query: str
    #: Fields a human reviewer should check before trusting a hit.
    watch: tuple[str, ...] = field(default_factory=tuple)


# Generic defaults: common second-hand server/desktop categories. No personal
# hardware models, no shipping address, no buyer's shortlist.
DEFAULT_CATEGORIES = (
    # Intentionally generic. Add or change searches in your own profile
    # (~/.config/ebay-deals/categories.json) rather than editing this tuple:
    # the defaults describe shopping *shapes*, not anyone's hardware wishlist.
    Category("cpu", "Standalone server CPUs", "server CPU LGA2011",
             ("cores", "SR code", "TDP")),
    Category("mobo", "Workstation motherboards", "LGA2011-v3 motherboard",
             ("socket", "DDR3 vs DDR4", "ECC support")),
    Category("combo", "CPU + motherboard combos", "CPU motherboard combo LGA2011",
             ("exact CPU model", "is RAM included?")),
    Category("workstation", "Workstation barebones", "Dell Precision barebone",
             ("base price trap", "what is actually included", "PSU included?")),
    Category("mini", "Mini PC barebones", "OptiPlex Micro barebone",
             ("barebone vs complete", "power adapter included?")),
    Category("gpu", "Datacenter GPUs", "Tesla datacenter GPU",
             ("VRAM size", "cooler type", "warranty")),
    Category("ram_server", "Server DDR4 ECC RAM", "DDR4 ECC 32GB 1600 UDIMM",
             ("ECC vs non-ECC", "rank", "speed")),
    Category("ram_laptop", "Laptop DDR4 SO-DIMM", "DDR4 SO-DIMM 2666 laptop memory 260 pin",
             ("260-pin SO-DIMM, not a desktop DIMM", "non-ECC unbuffered 1.2V",
              "single stick, not a multi-pack", "PC4-2400 CL17 or PC4-2666 CL19")),
    Category("laptop_parts", "Laptop display panels", "Dell Latitude LCD panel",
             ("panel vs back cover vs bezel", "exact model match")),
)
CATEGORIES_BY_KEY = {c.key: c for c in DEFAULT_CATEGORIES}

# Absolute path of the config file in use, for diagnostics.
CATEGORIES_SOURCE = "built-in defaults"


def _load(path: str) -> tuple[Category, ...]:
    with open(path, errors="replace") as handle:
        data = json.load(handle)
    if isinstance(data, dict):
        data = data.get("categories", [])
    out = []
    for entry in data:
        out.append(Category(
            key=entry["key"],
            label=entry.get("label", entry["key"]),
            query=entry["query"],
            watch=tuple(entry.get("watch", ())),
        ))
    if not out:
        raise ValueError(f"{path} defines no categories")
    return tuple(out)


def load_categories(path: str | None = None) -> tuple[Category, ...]:
    """Load categories from the first available source and refresh the registry."""
    global CATEGORIES_BY_KEY, CATEGORIES_SOURCE
    candidates = []
    if path:
        candidates.append(path)
    env_path = os.environ.get("EBAY_DEALS_CATEGORIES")
    if env_path:
        candidates.append(env_path)
    user_path = os.path.join(os.path.expanduser("~"), ".config", "ebay-deals", "categories.json")
    candidates.append(user_path)
    for candidate in candidates:
        if os.path.exists(candidate):
            categories = _load(candidate)
            CATEGORIES_BY_KEY = {c.key: c for c in categories}
            CATEGORIES_SOURCE = candidate
            return categories
    return DEFAULT_CATEGORIES


def all_categories() -> tuple[Category, ...]:
    if CATEGORIES_SOURCE == "built-in defaults":
        return DEFAULT_CATEGORIES
    return tuple(CATEGORIES_BY_KEY.values())


def get_category(key: str) -> Category:
    if key not in CATEGORIES_BY_KEY:
        load_categories()
    if key not in CATEGORIES_BY_KEY:
        raise KeyError(f"unknown category {key!r}; known: {sorted(CATEGORIES_BY_KEY)}")
    return CATEGORIES_BY_KEY[key]
