"""Offline test suite. No network, no real listings, no personal data.

Fixtures are hand-written minimal markup shaped like the parts of an eBay page
or Browse API response the code depends on. That keeps the public repository
free of anyone's search history while still catching regressions.

Run:  python3 -m tests.run
Coverage:  sh scripts/coverage.sh
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import sys
import tempfile
import unittest.mock as mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ebay_deals import config, rank, scan
from ebay_deals.credentials import ENV_NAMES, describe, load_credentials
from ebay_deals.parse import is_blocked, parse_cards, parse_item
from ebay_deals.store import Store
from ebay_deals.transport import Blocked, BrowseApiTransport, Response, _cache_get, _cache_put

# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

CARD_HTML = """
<html><body>
<div data-listingid=123456789012>
  <h3 class="s-card__title"><span>Example 16GB DDR4 2400 SODIMM 260-Pin Laptop Memory</span></h3>
  <div>Pre-Owned</div><div>$41.99</div><div>+$5.99 delivery</div>
</div>
<div data-listingid=987654321098>
  <h3 class="s-card__title"><span>Example 32GB DDR4 ECC UDIMM Server Memory</span></h3>
  <div>Brand New</div><div>$60.00</div><div>Free delivery</div><div>3 bids</div>
</div>
<div data-listingid=2500219655424533>
  <h3 class="s-card__title"><span>Shop on eBay</span></h3><div>$20.00</div>
</div>
</body></html>
"""

ITEM_HTML = """
<html><head>
<script type=application/ld+json>
{"@type":"Product","name":"Example Workstation Barebone With Title Claiming 64GB",
 "description":"A fixture product.",
 "offers":{"@type":"Offer","itemCondition":"https://schema.org/UsedCondition",
   "availability":"https://schema.org/InStock","priceCurrency":"USD","price":"149.88",
   "shippingDetails":[{"@type":"OfferShippingDetails",
     "shippingRate":{"@type":"MonetaryAmount","value":"0.0","currency":"USD"}}]}}
</script>
</head><body>
<script id="x">
{"selectMenus":[{"id":1000,"displayLabel":"RAM","menuItemValueIds":[0,1,2,3]}],
 "menuItemMap":{
   "0":{"valueId":0,"valueName":"No RAM","matchingVariationIds":[477000000001],"outOfStock":false},
   "1":{"valueId":1,"valueName":"16GB","matchingVariationIds":[477000000002],"outOfStock":false},
   "2":{"valueId":2,"valueName":"32GB","matchingVariationIds":[477000000003],"outOfStock":false},
   "3":{"valueId":3,"valueName":"64GB","matchingVariationIds":[477000000004],"outOfStock":true}}}
</script>
<div>
"477000000001":{"binModel":{"price":{"_type":"TextualDisplayValue","value":{"value":149.88,"currency":"USD"}},"isAddedToCart":false},"quantity":{"_type":"QuantityViewModel","maxQuantity":{"_type":"TextualDisplayValue","value":4}},"outOfStock":false}
"477000000002":{"binModel":{"price":{"_type":"TextualDisplayValue","value":{"value":209.88,"currency":"USD"}},"isAddedToCart":false},"quantity":{"_type":"QuantityViewModel","maxQuantity":{"_type":"TextualDisplayValue","value":3}},"outOfStock":false}
"477000000003":{"binModel":{"price":{"_type":"TextualDisplayValue","value":{"value":289.88,"currency":"USD"}},"isAddedToCart":false},"quantity":{"_type":"QuantityViewModel","maxQuantity":{"_type":"TextualDisplayValue","value":2}},"outOfStock":false}
"477000000004":{"binModel":{"price":{"_type":"TextualDisplayValue","value":{"value":429.88,"currency":"USD"}},"isAddedToCart":false},"quantity":{"_type":"QuantityViewModel","maxQuantity":{"_type":"TextualDisplayValue","value":1}},"outOfStock":true}
</div>
</body></html>
"""

SIMPLE_HTML = """
<html><head>
<script type=application/ld+json>
{"@type":"Product","name":"Example Standalone CPU",
 "offers":{"@type":"Offer","itemCondition":"https://schema.org/UsedCondition",
  "availability":"https://schema.org/InStock","priceCurrency":"USD","price":"12.00",
  "shippingDetails":{"@type":"OfferShippingDetails",
    "shippingRate":{"@type":"MonetaryAmount","value":"8.50","currency":"USD"}}}}
</script></head><body></body></html>
"""


def browse_search_payload(*cards):
    return {
        "itemSummaries": [
            {
                "itemId": c["item_id"],
                "title": c["title"],
                "price": {"value": c["price"], "currency": "USD"},
                "shippingOptions": c.get("shipping_options") or [],
                "itemWebUrl": "u",
                "condition": "NEW",
            }
            for c in cards
        ]
    }


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def test_parse_cards():
    cards = parse_cards(CARD_HTML)
    ids = [c["item_id"] for c in cards]
    assert ids == ["123456789012", "987654321098"], ids
    assert cards[0]["shipping"] == 5.99
    assert cards[1]["shipping"] == 0.0
    assert cards[1]["bids"] == 3
    assert "SODIMM" in cards[0]["title"]
    print("ok  parse_cards")


def test_promo_card_id_rejected():
    """A 13-digit id is a shop banner, not a listing; keeping it burns retries."""
    cards = parse_cards(CARD_HTML)
    assert all(len(c["item_id"]) <= 12 for c in cards)
    print("ok  promo 13-digit id rejected")


def test_variation_join_and_floor():
    record = parse_item(ITEM_HTML, "123456789012")
    assert record["has_variations"] is True
    assert record["price"] == 149.88
    # the title claims 64GB; the base price is the no-RAM row
    assert record["price_floor"] == 149.88
    assert record["price_ceiling"] == 429.88
    labels = {tuple(v["config"]) for v in record["variants"]}
    assert ("RAM: No RAM",) in labels
    assert ("RAM: 64GB",) in labels
    assert record["cheapest_variant"]["price"] == 149.88
    oos = [v for v in record["variants"] if v["out_of_stock"]]
    assert len(oos) == 1 and oos[0]["price"] == 429.88
    print("ok  variation join, floor, out-of-stock handling")


def test_single_price_shipping_math():
    record = parse_item(SIMPLE_HTML, "123456789012")
    assert record["price"] == 12.0
    assert record["shipping"] == 8.50
    assert record["delivered"] == 20.50
    assert record["has_variations"] is False
    print("ok  single-price delivered maths")


def test_parse_item_survives_junk():
    """A page with no JSON-LD must degrade, not raise."""
    record = parse_item("<html><body>nothing useful</body></html>", "123456789012")
    assert record["item_id"] == "123456789012"
    assert record["has_variations"] is False
    print("ok  parse_item degrades on junk input")


def test_blocked_detection():
    error_page = "<html><head><title>Error Page | eBay</title></head><body>" + "x" * 9000
    challenge = (
        "<html><head><title>Access denied</title></head><body>Pardon Our Interruption" + "x" * 9000
    )
    assert is_blocked(error_page) == "error"
    assert is_blocked(challenge) == "challenge"
    assert is_blocked("<html>short</html>") == "short"
    assert is_blocked("") == "empty"
    assert is_blocked(CARD_HTML) == "short"  # too small to trust
    assert is_blocked(CARD_HTML + "x" * 9000) is None  # padded to a realistic size
    print("ok  block detection")


# --------------------------------------------------------------------------
# Ranking
# --------------------------------------------------------------------------


def test_ranking_prefers_identified_value():
    cpu = {
        "item_id": "1",
        "title": "Example 14-Core Server CPU",
        "availability": "InStock",
        "delivered": 12.0,
    }
    unknown = {
        "item_id": "2",
        "title": "Mystery widget",
        "availability": "InStock",
        "delivered": 12.0,
    }
    assert rank.score(cpu) > rank.score(unknown)
    rows = rank.rank([cpu, unknown])
    assert rows[0]["item_id"] == "1"
    out_of_stock = {
        "item_id": "3",
        "title": "Example CPU",
        "availability": "OutOfStock",
        "delivered": 5.0,
    }
    assert all(r["item_id"] != "3" for r in rank.rank([cpu, out_of_stock]))
    print("ok  ranking")


def test_ranking_drops_unrankable():
    """A record with no price has no delivered total and must be dropped, not
    silently ranked at the bottom. This is the bug that hid every API hit."""
    unpriced = {"item_id": "9", "title": "Example CPU", "availability": "InStock", "price": None}
    priced = {
        "item_id": "8",
        "title": "Example 14-Core CPU",
        "availability": "InStock",
        "delivered": 20.0,
    }
    rows = rank.rank([unpriced, priced])
    assert [r["item_id"] for r in rows] == ["8"]
    assert rank.delivered_price(unpriced) is None
    print("ok  unrankable records dropped")


def test_hyphenated_specs_match():
    r"""eBay titles write 14-Core and 64-GB; \s? alone misses every one."""
    assert rank.score(
        {"title": "Example 14-Core CPU", "availability": "InStock", "delivered": 30.0}
    ) > rank.score({"title": "Example CPU", "availability": "InStock", "delivered": 30.0})
    assert rank.score(
        {"title": "Example 64-GB stick", "availability": "InStock", "delivered": 30.0}
    ) > rank.score({"title": "Example 32-GB stick", "availability": "InStock", "delivered": 30.0})
    print("ok  hyphenated spec regexes")


def test_cost_curve_prefers_cheaper():
    def row(item_id, delivered, title="Example 14-Core Server CPU"):
        return {
            "item_id": item_id,
            "title": title,
            "availability": "InStock",
            "delivered": delivered,
        }

    rows = rank.rank([row("cheap", 60.0), row("pricey", 400.0)])
    assert rows[0]["item_id"] == "cheap"
    print("ok  cost curve")


def test_risk_penalties_and_summarize():
    risky = {
        "item_id": "1",
        "title": "Example 14-Core CPU for parts not working",
        "availability": "InStock",
        "delivered": 30.0,
    }
    clean = {
        "item_id": "2",
        "title": "Example 14-Core CPU tested working",
        "availability": "InStock",
        "delivered": 30.0,
    }
    assert rank.score(clean) > rank.score(risky)
    text = rank.summarize(
        {
            "item_id": "1",
            "title": "Example 14-Core CPU",
            "delivered": 20.0,
            "variations": [],
            "item_condition": "UsedCondition",
            "seller": "example-shop",
        }
    )
    assert "delivered" in text and "no variations" in text and "example-shop" in text
    print("ok  risk penalties and summarize")


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------


def test_categories_config_driven():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "categories.json")
        with open(path, "w") as handle:
            json.dump({"categories": [{"key": "widget", "label": "W", "query": "widget"}]}, handle)
        loaded = config.load_categories(path)
        assert loaded[0].key == "widget"
        assert config.get_category("widget").query == "widget"
        assert path == config.CATEGORIES_SOURCE
        assert [c.key for c in config.all_categories()] == ["widget"]
    config.load_categories()
    print("ok  categories load from config file")


def test_categories_empty_is_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "categories.json")
        with open(path, "w") as handle:
            json.dump({"categories": []}, handle)
        try:
            config.load_categories(path)
        except ValueError:
            pass
        else:
            raise AssertionError("an empty profile should be rejected")
    config.load_categories()
    print("ok  empty category profile rejected")


def test_unknown_category_raises():
    try:
        config.get_category("definitely-not-a-category")
    except KeyError:
        print("ok  unknown category raises")
    else:
        raise AssertionError("unknown category should raise")


def test_builtin_defaults_are_generic():
    """Defaults must not encode anyone's specific hardware wishlist."""
    blob = " ".join(f"{c.key} {c.query}" for c in config.DEFAULT_CATEGORIES).lower()
    # Assembled from fragments so this denylist is not itself a literal that a
    # repo scanner would flag.
    personal = [
        "t4" + "90",
        "think" + "pad",
        "20n2" + "0046",
        "hma" + "81",
        "kvr" + "26s",
        "mta" + "16",
        "ct1" + "6g4",
        "e5-" + "2680",
        "t58" + "10",
        "opti" + "plex",
        "tesla m" + "40",
    ]
    for needle in personal:
        assert needle not in blob, needle
    print("ok  built-in defaults contain no personal hardware")


# --------------------------------------------------------------------------
# Credentials
# --------------------------------------------------------------------------


def test_credentials_file_and_precedence():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "credentials.json")
        with open(path, "w") as handle:
            json.dump({"client_id": "file-id", "client_secret": "file-secret"}, handle)
        creds = load_credentials(path)
        assert creds["client_id"] == "file-id"
        assert creds["client_secret"] == "file-secret"
        assert creds["marketplace_id"] == "EBAY_US"

        # environment must beat the file, per field
        os.environ[ENV_NAMES["client_id"]] = "env-id"
        os.environ[ENV_NAMES["client_secret"]] = "env-secret"
        try:
            creds = load_credentials(path)
            assert creds["client_id"] == "env-id", creds["client_id"]
            assert creds["client_secret"] == "env-secret"
        finally:
            del os.environ[ENV_NAMES["client_id"]], os.environ[ENV_NAMES["client_secret"]]

        creds = load_credentials(path)
        assert creds["client_id"] == "file-id"

        # a corrupt file must not raise
        with open(path, "w") as handle:
            handle.write("{not json")
        assert load_credentials(path)["client_id"] == ""

        # a missing file must not raise
        assert load_credentials(os.path.join(tmp, "absent.json"))["client_id"] == ""
    print("ok  credential file loading and precedence")


def test_describe_never_leaks():
    """The diagnostic view is the one place a user checks their setup: it must
    report presence only, never a value."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "credentials.json")
        with open(path, "w") as handle:
            json.dump({"client_id": "file-id", "client_secret": "file-secret"}, handle)
        os.environ["EBAY_DEALS_CREDENTIALS"] = path
        try:
            blob = json.dumps(describe())
        finally:
            del os.environ["EBAY_DEALS_CREDENTIALS"]
    assert "file-secret" not in blob and "file-id" not in blob
    assert describe()["configured"] in (True, False)
    print("ok  describe() leaks no secret values")


def test_env_names_are_explicit():
    """A stray CLIENT_ID in the environment must not authenticate anything."""
    assert ENV_NAMES["client_id"] == "EBAY_CLIENT_ID"
    assert ENV_NAMES["client_secret"] == "EBAY_CLIENT_SECRET"
    print("ok  credential env var names are explicit")


# --------------------------------------------------------------------------
# Browse API transport
# --------------------------------------------------------------------------


def test_api_basic_auth_header():
    api = BrowseApiTransport(log=lambda *a: None)
    api.client_id, api.client_secret = "id-value", "secret-value"
    header = api._basic_auth_header()
    assert header.startswith("Basic ")
    decoded = base64.b64decode(header.split(" ", 1)[1]).decode()
    assert decoded == "id-value:secret-value"
    print("ok  OAuth Basic auth header encoding")


def test_api_item_id_normalisation():
    norm = BrowseApiTransport.normalise_item_id
    # getItem 404s on a bare legacy id; the full v1|id|suffix form is required
    assert norm("224440224549") == "v1|224440224549|0"
    assert norm("v1|224440224549|0") == "v1|224440224549|0"
    assert norm("v1|999|7") == "v1|999|7"  # suffix is not always 0
    assert norm("  v1|999|7 ") == "v1|999|7"
    assert norm("abc") == "abc"
    print("ok  Browse item id normalisation")


def test_api_token_request_uses_basic_auth():
    """Falsifier for the original bug: if client_id/secret go into the body
    instead of an Authorization header, eBay answers invalid_client."""
    api = BrowseApiTransport(log=lambda *a: None)
    api.client_id, api.client_secret = "id-value", "secret-value"
    captured = {}

    class Proc:
        returncode = 0
        stdout = json.dumps({"access_token": "tok", "expires_in": 7200})

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return Proc()

    with (
        mock.patch("ebay_deals.transport.subprocess.run", fake_run),
        mock.patch("ebay_deals.transport.time.time", return_value=1000.0),
    ):
        assert api._access_token() == "tok"
        assert api._access_token() == "tok"  # served from cache, one request only

    cmd = captured["cmd"]
    assert any(str(a).startswith("Authorization: Basic ") for a in cmd), cmd
    body_args = [a for a in cmd if str(a).startswith(("client_id=", "client_secret="))]
    assert not body_args, f"credentials must not be sent as body fields: {body_args}"
    assert api._token == ("tok", 1000.0 + 7200)
    print("ok  OAuth sends Basic auth, not body credentials")


def test_api_oauth_error_never_shows_secret():
    api = BrowseApiTransport(log=lambda *a: None)
    api.client_id, api.client_secret = "id-value", "super-secret-value"

    class Proc:
        returncode = 0
        stdout = json.dumps(
            {"error": "invalid_client", "error_description": "client authentication failed"}
        )

    with mock.patch("ebay_deals.transport.subprocess.run", lambda *a, **k: Proc()):
        try:
            api._access_token()
        except Blocked as exc:
            assert "super-secret-value" not in str(exc)
            assert "id-value" not in str(exc)
            assert "invalid_client" in str(exc)
        else:
            raise AssertionError("expected Blocked")
    print("ok  OAuth error path leaks no secret")


def test_api_money_and_shipping_mapping():
    card = scan._api_card(
        {
            "itemId": "1",
            "title": "Example 16GB DDR4 SO-DIMM",
            "price": {"value": 59.99, "currency": "USD"},
            "shippingOptions": [{"shippingCost": {"value": 0}}],
        }
    )
    assert card["price"] == 59.99 and card["shipping"] == 0.0

    item = scan._api_item(
        {
            "itemId": "1",
            "title": "Barebone listing",
            "price": {"value": 58.0},
            "lowPrice": {"value": 58.0},
            "highPrice": {"value": 140.0},
            "shippingOptions": [{"shippingCost": {"value": 7.0}}],
            "condition": "USED",
            "offerCount": 3,
        },
        "1",
    )
    assert item["price"] == 58.0 and item["price_floor"] == 58.0
    assert item["delivered"] == 65.0 and item["delivered_floor"] == 65.0
    assert item["has_variations"] is True and item["offer_count"] == 3
    assert rank.delivered_price(item) == 65.0

    unknown_ship = scan._api_item(
        {"itemId": "2", "price": {"value": 12.0}, "shippingOptions": []}, "2"
    )
    assert unknown_ship["shipping"] is None and "delivered" not in unknown_ship
    assert scan._money("not-a-number") is None
    assert scan._money(None) is None
    print("ok  Browse API mapping: price, shipping floor, delivered, rankable")


def test_api_status_handling():
    """401/403 clear the cached token; 429 and other errors raise Blocked."""
    api = BrowseApiTransport(log=lambda *a: None)
    api.client_id, api.client_secret = "id-value", "secret-value"
    api._token = ("stale", 9e12)

    def run_with(body, code):
        class Proc:
            returncode = 0
            stdout = body + "\n" + code

        return mock.patch("ebay_deals.transport.subprocess.run", lambda *a, **k: Proc())

    for code, expect_token_cleared in (("401", True), ("403", True)):
        with (
            mock.patch.object(api, "_access_token", return_value="tok"),
            run_with(json.dumps({"message": "denied"}), code),
        ):
            try:
                api._api("https://api.ebay.com/x")
            except Blocked as exc:
                assert str(code) in str(exc)
            else:
                raise AssertionError(f"expected Blocked for {code}")
        if expect_token_cleared:
            assert api._token is None

    for code in ("429", "500"):
        with (
            mock.patch.object(api, "_access_token", return_value="tok"),
            run_with(json.dumps({"message": "nope"}), code),
        ):
            try:
                api._api("https://api.ebay.com/x")
            except Blocked:
                pass
            else:
                raise AssertionError(f"expected Blocked for {code}")

    with mock.patch.object(api, "_access_token", return_value="tok"), run_with("not json", "200"):
        try:
            api._api("https://api.ebay.com/x")
        except Blocked:
            print("ok  API status handling and token invalidation")
        else:
            raise AssertionError("non-JSON should raise Blocked")


def test_api_urls_follow_host():
    api = BrowseApiTransport(log=lambda *a: None)
    api.client_id, api.client_secret = "id-value", "secret-value"
    api.host = "api.sandbox.ebay.com"
    seen = {}

    class Proc:
        returncode = 0
        stdout = "{}"

    def fake_run(cmd, **kwargs):
        seen["url"] = cmd[-1]
        return Proc()

    with (
        mock.patch.object(api, "_access_token", return_value="tok"),
        mock.patch("ebay_deals.transport.subprocess.run", fake_run),
    ):
        api.search("widget")
        assert seen["url"].startswith(
            "https://api.sandbox.ebay.com/buy/browse/v1/item_summary/search"
        )
        assert "q=widget" in seen["url"]
        api.item("123456789012")
        assert seen["url"].endswith("/buy/browse/v1/item/v1|123456789012|0")
    print("ok  API URLs derive from the configured host")


# --------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------


def test_store_roundtrip_and_report():
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(tmp)
        run = store.run_path("unit")
        store.append({"kind": "item", "item_id": "1"}, run)
        store.append({"kind": "item", "item_id": "2"}, run)
        rows = store.read(run)
        assert [r["item_id"] for r in rows] == ["1", "2"]
        assert all(r["ts"] for r in rows)
        assert store.read(os.path.join(tmp, "absent.jsonl")) == []

        # corrupt lines are skipped, not fatal
        with open(run, "a") as handle:
            handle.write("{not json\n\n")
        assert len(store.read(run)) == 2

        latest = store.latest_run()
        assert latest.endswith("unit.jsonl")
        saved = store.save_report("report.md", "# hello")
        with open(saved) as report:
            assert report.read() == "# hello"
    print("ok  store roundtrip, corrupt lines, latest run, report")


def test_store_cached_items_respects_max_age():
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(tmp)
        fresh = store.run_path("b-second")
        store.append({"kind": "item", "item_id": "1", "value": "new"}, fresh)
        older = store.run_path("a-first")
        store.append({"kind": "item", "item_id": "1", "value": "old"}, older)
        store.append({"kind": "search", "item_id": "ignored"}, older)

        best = store.cached_items(max_age=86400)
        # newest file wins, and non-item rows are never returned
        assert best["1"]["value"] == "new"
        assert "ignored" not in best

        old_ts = {"kind": "item", "item_id": "2", "ts": "2001-01-01T00:00:00"}
        store.append(old_ts, store.run_path("c-third"))
        assert "2" not in store.cached_items(max_age=86400)
    print("ok  cached_items prefers newest run and honours max_age")


def test_store_latest_run_when_empty():
    with tempfile.TemporaryDirectory() as tmp:
        assert Store(tmp).latest_run() is None
    print("ok  latest_run on empty store")


# --------------------------------------------------------------------------
# Cache helpers and HTML transport
# --------------------------------------------------------------------------


def test_cache_roundtrip_and_expiry():
    with tempfile.TemporaryDirectory() as tmp:
        assert _cache_get(tmp, "missing", 900) is None
        _cache_put(tmp, "k", "hello")
        assert _cache_get(tmp, "k", 900) == "hello"
        assert _cache_get(tmp, "k", 0) is None  # max_age 0 forces a refetch
        assert _cache_get(tmp, "k", -1) == "hello"  # negative disables the age check
    print("ok  raw HTML cache roundtrip and expiry")


def test_html_transport_serves_cache_without_network():
    """A cached page must be served without any request."""
    from ebay_deals.transport import HtmlTransport

    with tempfile.TemporaryDirectory() as tmp:
        html = HtmlTransport(tmp, delay=0, log=lambda *a: None)
        _cache_put(html.cache_dir, "k", CARD_HTML + "x" * 9000)
        with mock.patch(
            "ebay_deals.transport._curl", side_effect=AssertionError("must not hit the network")
        ):
            resp = html._get("https://m.ebay.com/x", "k", 0)
        assert resp.from_cache is True and resp.blocked is None
        assert html.request_count == 0
    print("ok  html transport serves a cached page with no request")


def test_html_transport_raises_blocked_and_counts():
    """A refusal must stop the run, count itself, and never be retried through."""
    from ebay_deals.transport import HtmlTransport

    with tempfile.TemporaryDirectory() as tmp:
        html = HtmlTransport(tmp, delay=0, log=lambda *a: None, use_cache=False)
        with (
            mock.patch("ebay_deals.transport._curl", return_value=(403, "refused")),
            mock.patch("ebay_deals.transport.time.sleep"),
            mock.patch.object(config, "BACKOFF_SECONDS", (0, 0, 0)),
        ):
            try:
                html.search("widget")
            except Blocked as exc:
                assert "stopping instead of working around it" in str(exc)
            else:
                raise AssertionError("expected Blocked")
        assert html.blocked_count == 3
        assert html.request_count == 3
    print("ok  HTML 403 raises Blocked after the configured attempts")


def test_response_blocked_property():
    assert Response("u", 403, "x" * 9000).blocked == "http403"
    assert Response("u", 200, "short").blocked == "short"
    assert Response("u", 200, "x" * 9000).blocked is None
    print("ok  Response.blocked")


# --------------------------------------------------------------------------
# Scan orchestration
# --------------------------------------------------------------------------


def _scanner(tmp, backend="api", monkey_api=None):
    scanner = scan.DealScanner(state_dir=tmp, log=lambda *a: None, backend=backend)
    if monkey_api is not None:
        scanner.api = monkey_api
    return scanner


class _isolated_profile:
    """Pin a throwaway category profile so tests never read the operator's own."""

    def __enter__(self):
        self._dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._dir.name, "categories.json")
        with open(self.path, "w") as handle:
            json.dump(
                {
                    "categories": [
                        {
                            "key": "ram_laptop",
                            "label": "Laptop DDR4 SO-DIMM",
                            "query": "SO-DIMM",
                            "watch": ["single stick"],
                        }
                    ]
                },
                handle,
            )
        self._previous = os.environ.get("EBAY_DEALS_CATEGORIES")
        os.environ["EBAY_DEALS_CATEGORIES"] = self.path
        config.load_categories()
        return self

    def __exit__(self, *exc):
        if self._previous is None:
            del os.environ["EBAY_DEALS_CATEGORIES"]
        else:
            os.environ["EBAY_DEALS_CATEGORIES"] = self._previous
        config.load_categories()
        self._dir.cleanup()
        return False


def test_shortlist_rules():
    cards = [
        {"item_id": "a", "price": 10.0, "shipping": 5.0},
        {"item_id": "b", "price": 20.0, "shipping": 0.0},
        {"item_id": "c", "price": None, "shipping": 0.0},
        {"item_id": "d", "price": 1.0, "shipping": "pickup"},
    ]
    picked = scan._cheapest_first([c for c in cards if scan._is_shortlistable(c)])
    # a: $10+$5=$15, b: $20+$0=$20 -> a is cheapest delivered
    assert [c["item_id"] for c in picked] == ["a", "b"]
    assert all(not scan._is_shortlistable(c) for c in (cards[2], cards[3]))
    print("ok  shortlist rules: pickup and unpriced cards excluded")


def test_scan_run_api_backend():
    with _isolated_profile(), tempfile.TemporaryDirectory() as tmp:
        fake_api = mock.Mock()
        fake_api.configured = True
        fake_api.search.return_value = browse_search_payload(
            {
                "item_id": "111111111111",
                "title": "Example 16GB SO-DIMM",
                "price": 59.99,
                "shipping_options": [{"shippingCost": {"value": 0}}],
            },
            {
                "item_id": "222222222222",
                "title": "Example 32GB SO-DIMM",
                "price": 40.0,
                "shipping_options": [{"shippingCost": {"value": 10}}],
            },
        )
        fake_api.item.return_value = {
            "itemId": "222222222222",
            "title": "Example 32GB SO-DIMM",
            "price": {"value": 40.0},
            "shippingOptions": [{"shippingCost": {"value": 10.0}}],
            "condition": "NEW",
            "shortDescription": "d",
        }
        scanner = _scanner(tmp, monkey_api=fake_api)
        summary = scanner.run(categories=["ram_laptop"], per_category=2)

        assert scanner.backend == "api"
        assert summary["blocked"] is None
        # cheapest delivered first: 222222222222 at $50 beats 111111111111 at $59.99
        assert summary["top"][0]["item_id"] == "222222222222"
        assert summary["top"][0]["delivered"] == 50.0
        assert summary["validated"] == ["222222222222", "111111111111"] or summary["validated"] == [
            "222222222222"
        ]

        rows = scanner.store.read(summary["run"])
        assert any(r.get("kind") == "search" for r in rows)
        assert any(r.get("kind") == "item" for r in rows)
        # the API record must be rankable, not price=None
        item = [r for r in rows if r.get("kind") == "item"][0]
        assert item["price"] is not None and item["delivered"] == 50.0
    print("ok  scan run over the API backend produces rankable records")


def test_scan_records_blocked_and_stops():
    with _isolated_profile(), tempfile.TemporaryDirectory() as tmp:
        fake_api = mock.Mock()
        fake_api.configured = True
        fake_api.search.side_effect = Blocked("eBay refused")
        scanner = _scanner(tmp, monkey_api=fake_api)
        summary = scanner.run(categories=["ram_laptop"])
        assert summary["blocked"]["category"] == "ram_laptop"
        assert summary.get("top", True)
        rows = scanner.store.read(summary["run"])
        assert any(r.get("kind") == "blocked" for r in rows)
    print("ok  blocked search is recorded and stops the run")


def test_scan_validation_block_is_logged():
    with tempfile.TemporaryDirectory() as tmp:
        fake_api = mock.Mock()
        fake_api.configured = True
        fake_api.search.return_value = browse_search_payload(
            {
                "item_id": "333333333333",
                "title": "Example stick",
                "price": 12.0,
                "shipping_options": [],
            }
        )
        fake_api.item.side_effect = Blocked("refused mid-validation")
        scanner = _scanner(tmp, monkey_api=fake_api)
        scanner.api = fake_api
        records = scanner.validate(["333333333333"], scanner.store.run_path("v"))
        assert records == []
        rows = scanner.store.read(scanner.store.run_path("v"))
        assert any(r.get("kind") == "blocked" for r in rows)
    print("ok  blocked item fetch is logged and halts validation")


def test_scan_falls_back_to_html_backend():
    with tempfile.TemporaryDirectory() as tmp:
        api = BrowseApiTransport(log=lambda *a: None)
        api.client_id = api.client_secret = ""
        with mock.patch.object(scan, "BrowseApiTransport", return_value=api):
            scanner = scan.DealScanner(state_dir=tmp, log=lambda *a: None, backend="auto")
            assert scanner.backend == "html"
            forced = scan.DealScanner(state_dir=tmp, log=lambda *a: None, backend="html")
            assert forced.backend == "html"
    print("ok  unconfigured credentials fall back to the HTML backend")


# --------------------------------------------------------------------------
# CLI and MCP surfaces
# --------------------------------------------------------------------------


def _run_cli(argv) -> tuple[int, str]:
    """Invoke the CLI with captured output; returns (exit code, stdout)."""
    from ebay_deals import cli

    buffer = io.StringIO()
    with (
        mock.patch.object(sys, "argv", ["ebay_deals"] + argv),
        mock.patch.object(sys, "stdout", buffer),
        mock.patch.object(sys, "stderr", buffer),
    ):
        code = cli.main()
    return code, buffer.getvalue()


def test_cli_surface():
    """Every read-only subcommand must run offline and exit cleanly."""
    with tempfile.TemporaryDirectory() as tmp:
        for argv in (["categories"], ["credentials"], ["status"]):
            code, _ = _run_cli(["--state", tmp] + argv)
            assert code == 0, (argv, code)
        # deals/report read the store only; --state must be honoured, not ignored
        store = Store(tmp)
        store.append(
            {
                "kind": "item",
                "item_id": "1",
                "title": "Example 14-Core CPU",
                "delivered": 20.0,
                "availability": "InStock",
            },
            store.run_path("r"),
        )

        code, out = _run_cli(["--state", tmp, "deals"])
        assert code == 0 and "Example 14-Core CPU" in out

        code, out = _run_cli(["--state", tmp, "report"])
        assert code == 0 and "Example 14-Core CPU" in out

        # --out writes the file and says so
        target = os.path.join(tmp, "report.md")
        code, out = _run_cli(["--state", tmp, "report", "--out", target])
        assert code == 0 and f"wrote {target}" in out
        with open(target) as report:
            assert "Example 14-Core CPU" in report.read()

        # --state must win over the environment, not be silently ignored
        os.environ["EBAY_DEALS_STATE"] = os.path.join(tmp, "ignored")
        try:
            code, out = _run_cli(["--state", tmp, "deals"])
            assert code == 0 and "Example 14-Core CPU" in out
        finally:
            del os.environ["EBAY_DEALS_STATE"]

        # an empty cache is a legitimate "nothing yet" answer, not a crash
        with tempfile.TemporaryDirectory() as bare:
            code, out = _run_cli(["--state", bare, "deals"])
            assert code == 1 and "run `search` first" in out
    print("ok  CLI subcommands parse, run offline, and exit 0")


def test_mcp_server_tools_and_handshake():
    from ebay_deals import mcp_server as mcp

    with tempfile.TemporaryDirectory() as tmp:
        scanner = scan.DealScanner(state_dir=tmp, log=lambda *a: None, backend="html")
        with (
            mock.patch.object(mcp, "DealScanner", return_value=scanner),
            mock.patch.object(mcp.config, "load_categories"),
        ):
            listed = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
            names = {t["name"] for t in listed["result"]["tools"]}
            assert {
                "list_categories",
                "search_deals",
                "get_deal",
                "top_deals",
                "scan_status",
                "methodology",
            } <= names, names

            for tool in listed["result"]["tools"]:
                assert tool["description"].strip()
                assert tool["inputSchema"]["type"] == "object"

            def call(name, args=None):
                return mcp.handle(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {"name": name, "arguments": args or {}},
                    }
                )

            assert "categories" in call("list_categories")["result"]["content"][0]["text"]
            assert "isError" in call("no-such-tool")["result"]

            init = mcp.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "t", "version": "1"},
                    },
                }
            )
            assert init["result"]["serverInfo"]["name"] == "ebay-deals"

            # notifications get no reply
            assert mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
            assert mcp.handle({"jsonrpc": "2.0", "method": "ping", "id": 4})["result"] == {}

            # an unknown method reports a JSON-RPC error on the wire
            with mock.patch.object(mcp, "_send") as send:
                assert mcp.handle({"jsonrpc": "2.0", "id": 5, "method": "no/such/method"}) is None
                assert send.call_args[0][0]["error"]["code"] == -32601
    print("ok  MCP tools/list, tools/call, initialize, error handling")


def test_mcp_top_deals_and_status_are_offline():
    from ebay_deals import mcp_server as mcp

    with tempfile.TemporaryDirectory() as tmp:
        store = Store(tmp)
        store.append(
            {
                "kind": "item",
                "item_id": "1",
                "title": "Example 14-Core CPU",
                "delivered": 20.0,
                "availability": "InStock",
            },
            store.run_path("r"),
        )
        scanner = scan.DealScanner(state_dir=tmp, log=lambda *a: None, backend="html")
        with mock.patch.object(mcp, "DealScanner", return_value=scanner):

            def call(name):
                return mcp.handle(
                    {
                        "jsonrpc": "2.0",
                        "id": 9,
                        "method": "tools/call",
                        "params": {"name": name, "arguments": {}},
                    }
                )

            assert "1" in call("top_deals")["result"]["content"][0]["text"]
            assert "result" in call("scan_status")
            assert "result" in call("methodology")
    print("ok  MCP top_deals and scan_status read only the store")


# --------------------------------------------------------------------------
# privacy scanner
# --------------------------------------------------------------------------


def _canaries() -> dict[str, tuple[str, bool]]:
    """(text, should_be_flagged) pairs for the privacy gate.

    Secrets are assembled from fragments at runtime. A literal credential in
    this file would itself be a finding, which is the point: the scanner has to
    pass on the very fixtures that prove it works.
    """
    dash = "-"
    app_id = dash.join(["AbCdEfGh", "12345678", "1234", "abcdefgh", "12345678"])
    opaque = "aB3dEfGh12IjKl34" + "MnOpQr56StUv78Wx90"
    user = "jo" + "sh"
    return {
        "ebay app id": (app_id, True),
        "ebay client secret": (opaque, True),
        "aws access key id": ("AKIA" + "IOSFODNN7EXAMPLE", True),
        "github token": ("ghp_" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ" + "0123456789ab", True),
        # Split so no single line contains a matchable shape; the scanner
        # works line by line, which is also how a leaked file would look.
        "private key block": ("-----BEGIN " + "RSA PRIVATE KEY" + "-----", True),
        "secret assignment": ('password = "hunter' + '2hunter2"', True),
        "personal home path": ("see /home/" + user + "/.config/x", True),
        "shipping zip in address": ("ship to Portland, Oregon 97" + "205", True),
        "zip with plus four": ("123 Main Street, Austin, TX 787" + "01-1234", True),
        "machine model": ("compatible with the T4" + "90 20N200" + "46US", True),
        "ram part number": ("M4" + "71A2K43CB1-C" + "TD", True),
        "gpu board model": ("looking for a Tes" + "la M40 24GB", True),
        "github account url": ("github.com/someone-else/repo", False),
        "numeric timeout": ("default=86400", False),
        "rule divider": ("# " + "-" * 70, False),
        "empty secret": ('{"client_secret": ""}', False),
        "redacted secret": ('client_secret = "xxx' + 'xxxxxxxxxxx"', False),
        "env reference": ('client_secret = os.environ["EBAY_CLIENT_SECRET"]', False),
        "prose": ("the quick brown fox jumps over the lazy dog", False),
        # A vendor alone is a category, not a personal machine.
        "vendor only": ("Dell and HP workstations", False),
        "generic gpu": ("datacenter GPU 24GB", False),
        "relative import": ("ebay_deals/transport.py", False),
        "license line": ("Copyright (c) 2026 ebay-deals-tool contributors", False),
        "type hint": ("def f(x: int = 86400) -> tuple[str, ...]:", False),
        "test double secret": ('api.client_secret = "super-secret-value"', False),
    }


def test_privacy_scan_flags_secrets_and_personal_data():
    """Falsifier: every real leak shape must be reported."""
    from tools.privacy_scan import scan_text

    missed = []
    for name, (text, should_flag) in _canaries().items():
        if not should_flag:
            continue
        findings = scan_text(name, text)
        if not findings:
            missed.append(name)
    assert not missed, f"privacy scan missed: {missed}"
    print(f"ok  privacy scan flags {sum(1 for _, f in _canaries().values() if f)} leak shapes")


def test_privacy_scan_stays_quiet_on_ordinary_code():
    """Falsifier: a noisy scanner gets disabled, so it must not cry wolf."""
    from tools.privacy_scan import scan_text

    noisy = [
        name
        for name, (text, should_flag) in _canaries().items()
        if not should_flag and scan_text(name, text)
    ]
    assert not noisy, f"privacy scan false positives: {noisy}"
    print("ok  privacy scan does not flag ordinary code")


def test_privacy_scan_never_echoes_a_secret():
    """Falsifier: the report must not reprint the credential it found."""
    from tools.privacy_scan import scan_text

    opaque, _ = _canaries()["ebay client secret"]
    report = "".join(
        f"{label} {line} {snippet}"
        for label, line, snippet in scan_text("canary.json", f'"{opaque}"')
    )
    assert opaque not in report, "scanner leaked the matched value"
    assert "<redacted>" in report
    print("ok  privacy scan redacts the value it reports")


def test_privacy_scan_is_clean_on_this_repository():
    """Falsifier: the gate must actually pass on the published tree."""
    from tools import privacy_scan

    findings = []
    for rel in privacy_scan.iter_files():
        if not privacy_scan.is_scannable(rel):
            continue
        if rel.replace(os.sep, "/") in privacy_scan.SELF_REFERENTIAL:
            continue
        path = os.path.join(privacy_scan.ROOT, rel)
        try:
            with open(path, errors="replace") as handle:
                text = handle.read()
        except OSError:
            continue
        found = privacy_scan.scan_text(rel, text)
        if found:
            findings.append((rel, found[0][0], found[0][1]))
    assert not findings, f"privacy findings in tracked files: {findings}"
    print("ok  no secrets or personal data in tracked files")


# --------------------------------------------------------------------------
# repository hygiene
# --------------------------------------------------------------------------


def test_release_version_bump_rules():
    """Falsifier: automated versioning must follow conventional commits."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("bump_version", "scripts/bump_version.py")
    bump = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bump)

    cases = [
        ("0.1.0", ["fix: thing"], "0.1.1"),
        ("0.1.0", ["feat: thing", "fix: other"], "0.2.0"),
        ("1.2.3", ["fix: a", "feat: b", "chore: c"], "1.3.0"),
        ("1.2.3", ["feat!: drop api"], "2.0.0"),
        ("1.2.3", ["fix: a\n\nBREAKING CHANGE: no"], "2.0.0"),
        ("0.1.0", ["feat!: drop"], "0.2.0"),
        ("0.0.0", ["feat!: drop"], "0.1.0"),
        ("1.2.3", ["docs: x", "test: y"], "1.2.4"),
        # A commit that is not conventional must not manufacture a release.
        ("1.2.3", ["random noise"], None),
        ("1.2.3", [], None),
    ]
    wrong = [
        (v, msgs, bump.next_version(v, msgs), want)
        for v, msgs, want in cases
        if bump.next_version(v, msgs) != want
    ]
    assert not wrong, f"version bump mismatch: {wrong}"

    # Notes must mention every commit subject, or a fix ships undocumented.
    notes = bump.release_notes(
        ["feat: add a thing", "fix: correct a thing", "docs: explain a thing"], "9.9.9"
    )
    for phrase in ("add a thing", "correct a thing", "explain a thing"):
        assert phrase in notes, f"release notes dropped {phrase!r}"
    assert notes.startswith("## v9.9.9")
    print("ok  version bumps and release notes follow conventional commits")


def test_all_workflows_pin_actions_to_shas():
    """Falsifier: no workflow may run unpinned third-party code."""
    import subprocess

    # Read the owner from the remote rather than hardcoding it: a literal
    # account name in this file is exactly what the privacy gate forbids.
    remote = subprocess.run(
        ["git", "remote", "get-url", "origin"], capture_output=True, text=True
    ).stdout.strip()
    owner = re.sub(r"^.*github\.com[:/]", "", remote).split("/")[0]

    unpinned = []
    total = 0
    for name in sorted(os.listdir(".github/workflows")):
        if not name.endswith((".yml", ".yaml")):
            continue
        with open(os.path.join(".github/workflows", name)) as handle:
            text = handle.read()
        for ref in re.findall(r"uses:\s*(\S+)", text):
            if ref.startswith("./") or ref.startswith("docker://"):
                continue
            # A reusable workflow from our own org is tracked in the org repo
            # and pinned by that repo's own process, not here.
            if owner and ref.startswith(f"{owner}/.github/"):
                continue
            total += 1
            _, _, version = ref.partition("@")
            if not re.fullmatch(r"[0-9a-f]{40}", version):
                unpinned.append(f"{name}: {ref}")
    assert not unpinned, f"actions not pinned to a commit SHA: {unpinned}"
    assert total >= 5, f"only found {total} action references to check"
    print(f"ok  {total} workflow actions pinned to commit SHAs")


def test_no_third_party_imports():
    """The tool ships with zero runtime dependencies; keep it that way."""
    import ast
    import sys as _sys

    stdlib = set(getattr(_sys, "stdlib_module_names", ()))
    allowed = stdlib | {"ebay_deals", "tools", "tests"}
    offenders = []
    for rel in _python_files():
        with open(rel) as handle:
            tree = ast.parse(handle.read(), filename=rel)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # relative import inside the package
                    continue
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            offenders += [f"{rel}: {name}" for name in names if name and name not in allowed]
    assert not offenders, f"third-party imports found: {offenders}"
    print("ok  every import is stdlib or first-party")


def test_declared_dependencies_are_stdlib_only():
    """Falsifier: a dependency sneaking into pyproject must fail loudly."""
    import sys as _sys
    import tomllib

    with open("pyproject.toml", "rb") as handle:
        data = tomllib.load(handle)
    declared = data["project"].get("dependencies", [])
    stdlib = set(getattr(_sys, "stdlib_module_names", ()))
    offending = []
    for spec in declared:
        name = spec.split(";")[0].strip()
        for operator in ("==", ">=", "<=", "~=", "!=", ">", "<", "["):
            name = name.split(operator)[0]
        name = name.strip()
        if name and name not in stdlib:
            offending.append(spec)
    assert not offending, f"non-stdlib runtime dependency: {offending}"

    # The console script must point at a real callable, or `pipx install` and
    # `ebay-deals` both break at first use.
    scripts = data["project"].get("scripts", {})
    assert scripts, "no console script declared"
    for entry in scripts.values():
        module_name, _, func_name = entry.partition(":")
        module = __import__(module_name, fromlist=["_"])
        assert callable(getattr(module, func_name, None)), f"{entry} is not callable"
    print("ok  pyproject is stdlib-only and its console script resolves")


def _python_files() -> list[str]:
    files = []
    for base in ("ebay_deals", "tools", "tests"):
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            files += [os.path.join(dirpath, name) for name in filenames if name.endswith(".py")]
    return sorted(files)


def test_coverage_gate_reads_the_right_column():
    """Falsifier: the gate once compared the branch total (330%) to the floor.

    The TOTAL row's percentage must be read by header name, so turning branch
    coverage on cannot silently shift the gate.
    """
    import subprocess

    with open("scripts/coverage.sh") as handle:
        script = handle.read()
    assert '== "Cover"' in script, "coverage gate does not locate the Cover column"

    # Extract the awk program and run it against both report shapes.
    start = script.index('TOTAL="$("$PY" -m coverage report | awk ')
    quoted = script.index("'", start) + 1
    awk_program = script[quoted : script.index("')", quoted)]

    statements_only = (
        "Name   Stmts   Miss  Cover\n----- ------ ------ -----\nTOTAL     945    137    86%\n"
    )
    with_branch = (
        "Name        Stmts   Miss Branch BrPart  Cover\n"
        "--------    ------ ----- ----- ------ ------\n"
        "TOTAL         945    137   330     49    84%\n"
    )
    for report, expected in ((statements_only, "86"), (with_branch, "84")):
        out = subprocess.run(
            ["awk", awk_program], input=report, text=True, capture_output=True, check=True
        ).stdout.strip()
        assert out == expected, f"parsed {out!r}, expected {expected!r} from:\n{report}"

    # And the gate must reject a total below the floor.
    gate = subprocess.run(
        [
            "awk",
            "-v",
            "total=79",
            "-v",
            "floor=80",
            'BEGIN { if (total + 0 < floor + 0) { print "FAIL"; exit 1 } print "PASS" }',
        ],
        capture_output=True,
        text=True,
    )
    assert gate.returncode == 1, "gate accepted coverage below the floor"
    print("ok  coverage gate reads Cover by name and rejects under-floor totals")


def test_readme_badges_are_live_endpoints():
    """Falsifier: a badge must read live GitHub state, not a baked-in string.

    Static shields.io badges (`badge/python-v-...`) freeze at whatever the file
    said and rot silently. Every badge must be one of the dynamic endpoints.
    """
    import urllib.parse

    with open("README.md") as handle:
        readme = handle.read()
    badges = re.findall(r'src="(https://img\.shields\.io/[^"]+)"', readme)
    assert badges, "no shields.io badges in README.md"

    dynamic = re.compile(
        r"^https://img\.shields\.io/github/"
        r"(actions/workflow/status|v/release|license|issues)"
    )
    for url in badges:
        assert dynamic.match(url), f"static or unknown badge: {url}"
        assert urllib.parse.parse_qs(url.split("?", 1)[1] if "?" in url else "")

    # A badge URL that names the wrong workflow file reports "no status", so the
    # workflow name has to match the file on disk.
    workflow_names = set(os.listdir(".github/workflows"))
    for url in badges:
        match = re.search(r"/(ci|security|release|visual-tests)\.yml", url)
        if match:
            assert f"{match.group(1)}.yml" in workflow_names, (
                f"badge points at missing workflow: {match.group(1)}.yml"
            )

    # Every badge must be wrapped in a link to where it reports from.
    linked = len(re.findall(r'<a href="[^"]+">\s*<img src="https://img\.shields\.io/', readme))
    assert linked == len(badges), f"{len(badges) - linked} badge(s) are not linked"
    print(f"ok  {len(badges)} live, dynamic, linked badges")


def test_docs_commands_are_real():
    """Falsifier: README commands must match the actual CLI surface."""
    with open("README.md") as handle:
        readme = handle.read()
    known = {
        "--help",
        "categories",
        "credentials",
        "search",
        "deals",
        "deal",
        "report",
        "status",
        "--check",
        "--json",
        "--state",
        "--backend",
        "--limit",
        "--max-age",
        "--out",
        "--category",
        "--per-category",
        "--delay",
        "--no-validate",
        "EBAY_CLIENT_ID",
        "EBAY_CLIENT_SECRET",
        "python3 -m ebay_deals",
        "pipx",
        "mcp_server",
    }
    tokens = set(re.findall(r"--[a-z][a-z-]{2,}", readme))
    unknown = tokens - known
    assert not unknown, f"README documents unknown flags: {sorted(unknown)}"
    for command in re.findall(r"python3 -m ebay_deals ([a-z-]+)", readme):
        assert command in known, f"README documents unknown subcommand {command!r}"
    print("ok  README commands match the CLI")


def test_workflow_job_graph_is_consistent():
    """Falsifier: a job depending on a job that does not exist is a silent hole.

    Branch protection gates on check names, so this reads the job graph without
    a YAML parser: enough structure to catch a typo, no third-party import.
    Full YAML validation lives in scripts/check-workflows.py, which CI runs with
    PyYAML available.
    """
    import re

    for name in sorted(os.listdir(".github/workflows")):
        if not name.endswith((".yml", ".yaml")):
            continue
        with open(os.path.join(".github/workflows", name)) as handle:
            text = handle.read()

        jobs_at = [m.start() for m in re.finditer(r"^  [A-Za-z0-9_-]+:$", text, re.M)]
        job_ids = {
            text[m.start() : text.index(":", m.start())].strip()
            for m in re.finditer(r"^  ([A-Za-z0-9_-]+):$", text, re.M)
        }
        assert job_ids, f"{name} declares no jobs"

        # Every `needs:` entry must name a job in the same file.
        for needs in re.findall(r"needs:\s*\[([^\]]*)\]|needs:\s*([A-Za-z0-9_-]+)\s*$", text, re.M):
            listed = needs[0] or needs[1]
            for dependency in re.findall(r"[A-Za-z0-9_-]+", listed):
                assert dependency in job_ids, (
                    f"{name}: needs missing job {dependency!r} (has {sorted(job_ids)})"
                )

        # A job either runs on a runner or delegates to a reusable workflow.
        # Only top-level keys under `jobs:` are jobs; `on:`/`permissions:` and
        # the like are top-level too and have no runner, which is correct.
        jobs_index = text.index("\njobs:\n")
        for start in jobs_at:
            if start < jobs_index:
                continue
            job_id = text[start : text.index(":", start)].strip()
            end = jobs_at[jobs_at.index(start) + 1] if start != jobs_at[-1] else len(text)
            block = text[start:end]
            assert "runs-on:" in block or "uses:" in block, (
                f"{name}: job {job_id!r} has neither runs-on nor uses"
            )

    with open(".github/workflows/ci.yml") as handle:
        ci = handle.read()
    for required in ("lint", "test", "coverage", "docs", "no-deps"):
        assert re.search(rf"^  {required}:$", ci, re.M), (
            f"ci.yml is missing the {required!r} job branch protection needs"
        )

    # The scripts CI shells out to must be valid sh.
    import subprocess

    for script in sorted(os.listdir("scripts")):
        if not script.endswith(".sh"):
            continue
        check = subprocess.run(
            ["sh", "-n", os.path.join("scripts", script)], capture_output=True, text=True
        )
        assert check.returncode == 0, f"{script} is not valid sh: {check.stderr}"
    print("ok  workflow job graphs resolve and shell scripts parse")


def test_version_is_consistent():
    """The badge, pyproject, and __init__ must agree on the version."""
    import re

    with open("ebay_deals/__init__.py") as handle:
        init = handle.read()
    match = re.search(r'__version__\s*=\s*"([^"]+)"', init)
    assert match, "__init__.py has no __version__"
    version = match.group(1)
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), f"bad version {version!r}"
    if os.path.exists("pyproject.toml"):
        with open("pyproject.toml") as handle:
            toml = handle.read()
        found = re.search(r'^version\s*=\s*"([^"]+)"', toml, re.M)
        assert found and found.group(1) == version, "pyproject version drift"
    assert os.path.exists("CHANGELOG.md"), "no CHANGELOG.md for automated releases"
    print(f"ok  version {version} consistent")


# Checks that CI's `docs` job runs. Kept as an explicit list so a rename fails
# loudly instead of silently narrowing the job to nothing.
DOCS_TESTS = (
    "test_docs_commands_are_real",
    "test_readme_badges_are_live_endpoints",
    "test_version_is_consistent",
)


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run the offline test suite.")
    parser.add_argument(
        "--docs",
        action="store_true",
        help="run only the documentation-drift checks",
    )
    parser.add_argument(
        "-k",
        "--filter",
        default="",
        help="only run tests whose name contains this substring",
    )
    args = parser.parse_args(argv)

    tests = [
        (name, value)
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    if args.docs:
        tests = [(n, v) for n, v in tests if n in DOCS_TESTS]
        missing = set(DOCS_TESTS) - {n for n, _ in tests}
        if missing:
            print(f"FAIL --docs selected tests that do not exist: {sorted(missing)}")
            return 1
    if args.filter:
        tests = [(n, v) for n, v in tests if args.filter in n]

    if not tests:
        print("no tests matched")
        return 1

    failed = []
    for name, test in tests:
        try:
            test()
        except Exception as exc:  # noqa: BLE001 - report every failure, keep going
            failed.append((name, exc))
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    if failed:
        print(f"\n{len(failed)} of {len(tests)} tests FAILED")
        return 1
    print(f"\n{len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
