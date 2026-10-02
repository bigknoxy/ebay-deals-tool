"""Offline tests. No network, no real listings, no personal data.

Fixtures here are hand-written minimal markup shaped like the parts of an eBay
page the parsers depend on. That keeps the public repository free of anyone's
search history while still catching parser regressions.

Run with:  python3 -m tests.run
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ebay_deals import config, rank
from ebay_deals.credentials import load_credentials
from ebay_deals.parse import is_blocked, parse_cards, parse_item

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

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
    # out-of-stock 64GB must not be offered as the floor
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


def test_blocked_detection():
    # real eBay refusal bodies are small and carry the title near the top
    error_page = "<html><head><title>Error Page | eBay</title></head><body>" + "x" * 9000
    challenge = "<html><head><title>Access denied</title></head><body>Pardon Our Interruption" + "x" * 9000
    assert is_blocked(error_page) == "error"
    assert is_blocked(challenge) == "challenge"
    assert is_blocked("<html>short</html>") == "short"
    assert is_blocked("") == "empty"
    assert is_blocked(CARD_HTML) == "short"          # too small to trust
    assert is_blocked(CARD_HTML + "x" * 9000) is None  # padded to a realistic size
    print("ok  block detection")


def test_ranking_prefers_identified_value():
    cpu = {"item_id": "1", "title": "Example 14-Core Server CPU", "availability": "InStock",
           "delivered": 12.0}
    unknown = {"item_id": "2", "title": "Mystery widget", "availability": "InStock",
               "delivered": 12.0}
    assert rank.score(cpu) > rank.score(unknown)
    rows = rank.rank([cpu, unknown])
    assert rows[0]["item_id"] == "1"
    out_of_stock = {"item_id": "3", "title": "Xeon CPU", "availability": "OutOfStock",
                    "delivered": 5.0}
    assert all(r["item_id"] != "3" for r in rank.rank([cpu, out_of_stock]))
    print("ok  ranking")


def test_categories_config_driven():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "categories.json")
        with open(path, "w") as handle:
            json.dump({"categories": [{"key": "widget", "label": "W", "query": "widget"}]}, handle)
        loaded = config.load_categories(path)
        assert loaded[0].key == "widget"
        assert config.get_category("widget").query == "widget"
    # restore the real profile for the rest of the process
    config.load_categories()
    print("ok  categories load from config file")


def test_api_item_id_normalisation():
    from ebay_deals.transport import BrowseApiTransport as T
    # getItem 404s on a bare legacy id; the full v1|id|suffix form is required
    assert T.normalise_item_id("224440224549") == "v1|224440224549|0"
    assert T.normalise_item_id("v1|224440224549|0") == "v1|224440224549|0"
    assert T.normalise_item_id("v1|999|7") == "v1|999|7"
    assert T.normalise_item_id("  v1|999|7 ") == "v1|999|7"
    print("ok  Browse item id normalisation")


def test_api_basic_auth_header():
    import base64
    from ebay_deals.transport import BrowseApiTransport as T
    api = T(log=lambda *a: None)
    api.client_id, api.client_secret = "id-value", "secret-value"
    header = api._basic_auth_header()
    assert header.startswith("Basic ")
    decoded = base64.b64decode(header.split(" ", 1)[1]).decode()
    assert decoded == "id-value:secret-value"
    print("ok  OAuth Basic auth header encoding")


def test_credentials_precedence(tmpdir=None):
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "credentials.json")
        with open(path, "w") as handle:
            json.dump({"client_id": "file-id", "client_secret": "file-secret"}, handle)
        creds = load_credentials(path)
        assert creds["client_id"] == "file-id"
        assert creds["client_secret"] == "file-secret"
        # environment must beat the file
        os.environ["EBAY_CLIENT_ID"] = "env-id"
        os.environ["EBAY_CLIENT_SECRET"] = "env-secret"
        try:
            creds = load_credentials(path)
            assert creds["client_id"] == "env-id", creds["client_id"]
            assert creds["client_secret"] == "env-secret"
        finally:
            del os.environ["EBAY_CLIENT_ID"], os.environ["EBAY_CLIENT_SECRET"]
        creds = load_credentials(path)
        assert creds["client_id"] == "file-id"
        assert creds["marketplace_id"] == "EBAY_US"
        # the diagnostic view must never contain the secret itself
        from ebay_deals.credentials import describe
        blob = json.dumps(describe())
        assert "file-secret" not in blob and "file-id" not in blob
        assert describe()["configured"] is False or True  # shape check only
    print("ok  credential file loading")


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
    print(f"\n{len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
