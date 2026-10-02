# Methodology: how eBay deal data is collected here

This file is the reference for *how* the scanner works and *why* each choice was
made. It exists because the numbers in any deal report are only as good as the
collection method behind them, and because the method needs to be repeatable by
the packaged tool rather than re-derived by hand each session.

## 1. Two backends, one preference order

| Backend | When used | Notes |
|---|---|---|
| `browse_api` | `EBAY_CLIENT_ID` + `EBAY_CLIENT_SECRET` set | Official Browse Item API, OAuth 2.0 client credentials. Supported, quotable, scales. |
| `html` | otherwise | Mobile endpoints, one curl subprocess per request, persisted cookie jar. |

The API is preferred because it is the access model eBay actually supports. The
HTML path exists so the tool is useful before credentials exist, and it is
deliberately slow.

## 2. Measurements from 2026-09-28 (the numbers behind the defaults)

Starting cold at 21:11 CDT:

| Request | Result |
|---|---|
| `www.ebay.com/sch/i.html` | HTTP 403, 1,831-byte error page — every time, including after a 5-minute wait on 2026-09-23 |
| `m.ebay.com/sch/i.html` | HTTP 200, ~900 KB, 52–92 usable cards |
| `m.ebay.com/itm/<id>` | HTTP 200, ~1.1–1.2 MB with full buy box and variation data (redirects to `www`, still served) |
| urllib (Python stdlib), same URL/headers/cookies | HTTP 403 on the first request |
| curl, same URL/headers/cookies | HTTP 200 |

Two consequences, both encoded in `transport.py`:

1. **Use curl.** When urllib and curl disagree at the first request, the cause is
   transport-level fingerprinting. Fighting that is not the goal of this tool, so
   it shells out to curl instead of trying to look more like something else.
2. **Budget requests.** 13–16 successful requests per session, then HTTP 403 for
   the whole session. A 90-second backoff did **not** clear it; 420 seconds did.

So the defaults are: 25s between search requests, 18s between item requests, a
maximum of 30 item fetches per run, and backoff `(0, 90, 420)` seconds. The
tool treats a 403 or a challenge as a hard stop and ends the run with a
`blocked` record. It does not rotate hosts, IPs, user agents, cookies or
accounts, does not solve CAPTCHAs, and does not retry through a challenge. Those
would be both unreliable and outside the intended access model.

## 3. Two-phase runs

A run is deliberately split:

* **Search phase** — one request per category, 25s apart, results written to
  JSONL and raw HTML cached. This phase is broad and cheap.
* **Validate phase** — one item page per shortlisted candidate, 18s apart. This
  phase is narrow and is the only one whose output can be quoted as a price.

Splitting them means a blocked session costs minutes but never corrupts the
report: search-level candidates stay labelled as search-level, and only
validated records get ranked into "best deals".

## 4. Why item pages are parsed the way they are

Two sources per item page:

* **JSON-LD** (`application/ld+json`) — title, buy-box price, availability,
  shipping rate, item condition, seller. Generated for search engines, so it is
  the most stable structured data on the page.
* **Embedded pick-list JSON** — `selectMenus` (groups) + `menuItemMap`
  (value ids → labels → `matchingVariationIds`) joined to the per-variation
  price map keyed by variation id, which also carries `maxQuantity` and
  `outOfStock`.

**The base-price trap.** On a variation listing, the page title and the JSON-LD
price describe the *default* selection. Example verified repeatedly:
one workstation listing is titled "... 64GB RAM" with a base price of
`$149.88`, but the resolved rows are:

| Configuration | Price |
|---|---|
| E5-2660 v3, no RAM | $149.88 |
| E5-2660 v3, 16GB | $209.88 |
| E5-2660 v3, 32GB | $289.88 |
| E5-2660 v3, 64GB | $429.88 |

Any report that quotes `$149.88` for a 64GB machine is wrong. The rule enforced
in code: quote a resolved variation row, and when a listing has variations say
so. `price_floor` (cheapest in-stock row) and `price_ceiling` are both recorded
so the gap is visible instead of hidden behind one number.

## 5. Delivered price

`delivered = price + shipping`, USD, **pre-tax**, shipping as shown to ZIP
`EBAY_SHIP_ZIP` (unset by default, so shipping costs stay as listed and are
never attributed to a location). Local-pickup-only cards are dropped
because they are not shippable deals. Tax at checkout will be roughly 8% in that
ZIP; the report states pre-tax and leaves the tax line to the buyer.

## 6. Ranking

`rank.py` is a transparent weighted sum, not a black box:

* **Value** — the most valuable configuration mentioned anywhere in the title or
  variation labels wins (64GB > 32GB > 16GB; 24GB GPU; core count). One value
  term, `max()`, not a sum, so a listing cannot inflate its score by listing
  every spec.
* **Risk penalties** — "for parts", "not working", "untested", "broken",
  "as is", lots/collections. These are the dominant negative signal.
* **Bonuses** — "tested", "works", "bench test", SSD/NVMe, PSU wattage.
* **Cost curve** — `min(1, delivered^0.35 / 9)` subtracted, so a $15 CPU competes
  on value, and a $300 machine has to be genuinely better to win.
* **Availability** — anything not in stock is dropped before scoring.

## 7. Evidence and replay

* Raw HTML: `~/.cache/ebay-deals/cache/*.html` (or `$EBAY_DEALS_STATE`).
* Structured events: `~/.cache/ebay-deals/runs/scan-<timestamp>.jsonl`, one JSON
  object per line, kinds: `search`, `item`, `blocked`.
* Re-parsing a cached page costs no requests, which is how parser changes get
  regression-tested against pages that have already changed hands.

## 8. Known gaps

* **Seller name and feedback score are not available** from the mobile item
  page. That layout loads them client-side, so they are absent from the HTML we
  save (verified 2026-09-29: zero occurrences of `sellerName`, `feedbackScore`,
  `Sold by`, or `storeName` in the cached pages). Reports must say so rather than
  quote a reputation the data does not contain. Parsing hooks are left in
  `parse.py` because the desktop layout does include them.
* **Search-level prices are not deal prices.** A card price can be a base
  variation, an auction opening bid, or "free" for a barebone. Only validated
  item pages get ranked into "best deals".

## 9. What this method cannot do

* It cannot be exhaustive. 17 categories × one page each is a *broad scan*, not a
  crawl of all inventory, and reports say so.
* It cannot see a blocked session. When 403s start, the run stops; the missing
  categories are recorded as not covered.
* It cannot verify condition. "Tested" in a title is a seller claim. Bench-
  testing before buying is still on the buyer.
* It will drift. Prices and stock change hourly; `max_age` in the CLI and MCP
  tools exists so stale rows can be excluded rather than silently reported.
