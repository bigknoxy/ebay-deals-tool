# Lessons learned the hard way

Notes from operating this tool against live eBay. Every item here came from a
failed run first, so treat them as load-bearing, not decoration.

## Browse API: two authentication/identifier traps

Both cost real debugging time and neither is obvious from the API reference.

**1. The token request uses HTTP Basic auth, not body parameters.**

The documented request is:

```
POST https://api.ebay.com/identity/v1/oauth2/token
Content-Type: application/x-www-form-urlencoded
Authorization: Basic base64(AppID:ClientSecret)

grant_type=client_credentials&scope=https://api.ebay.com/oauth/api_scope
```

Sending `client_id` and `client_secret` as form fields instead returns
`HTTP 401 / invalid_client / "client authentication failed"` — an error that
reads like bad credentials. It is not; it means eBay never saw the Basic
header. Every official eBay client library (node, java, php, python) does it the
documented way.

Implementation: `BrowseApiTransport._basic_auth_header`.

**2. `getItem` needs the full identifier, not the numeric listing id.**

Listings are keyed `v1|<legacyItemId>|<suffix>`. Requesting
`/buy/browse/v1/item/224440224549` returns
`errorId 11001 / "The specified item Id was not found."` while
`/buy/browse/v1/item/v1|224440224549|0` returns the listing. `item_summary/search`
returns the full form in `itemId`, so keep it rather than rebuilding it — the
trailing suffix is not always `0`, and rebuilding drops those listings.

Implementation: `BrowseApiTransport.normalise_item_id`.

## Credential loading

Precedence is environment variable → file, resolved per field so a partial
environment does not half-override the file. Variable names are explicit
(`EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`), never derived from the field name,
because a stray `CLIENT_ID` in the environment would otherwise silently take
effect.

Diagnose without exposing anything:

```bash
python3 -m ebay_deals credentials --check
```

It performs a real token request against production and sandbox and prints only
pass/fail plus eBay's error string. Secrets are never logged, never echoed, and
never written to the evidence store.

## Parsing traps found in real listings

**Base price is the cheapest variation, not the advertised configuration.** A
workstation listing titled with 64GB of RAM is frequently priced from $149.88,
which is the "no RAM" row. Quoting that number as the deal is simply wrong. The
variation table has to be resolved before any price is reported.

**Multi-capacity listings hide the price you want.** A Crucial listing showing
$27.15 was the 4GB row; the 16GB rows were $86.71. Always read
`lowPrice`/`highPrice` and the variation list.

**"32GB" often means a 2x16GB kit.** A search for a single 32GB SO-DIMM returns
plenty of two-stick kits. A laptop with one slot cannot use them. This is a
category-level constraint, so it belongs in the category's `watch` list.

**Out-of-stock rows must not set the floor.** Variation tables include sold-out
configurations; anchoring on one understates cost and can rank a dead listing
first.

**`14-Core` does not match `14.core`.** eBay titles hyphenate constantly. Spec
regexes need `[\s-]?`, not `\s?`. This silently dropped every hyphenated listing
from the ranker.

**Promotional card ids are 13 digits.** Real listing ids are 9–12. Anchoring on
`\d{13}` picked up a shop banner, which 404s and burns retries in the backoff
loop.

## HTML backend behaviour

Measured on the mobile endpoints with a single cookie jar and no evasion:

- `www.ebay.com` search and item requests returned HTTP 403; `m.ebay.com` served
  the same pages.
- Python `urllib` was refused on the first request where curl succeeded with
  identical URL, headers and cookies. The tool uses curl rather than
  fingerprint-tweaking, which would slide into evasion.
- A session survived roughly 13–16 successful requests, then was refused. A
  90-second backoff did not clear it; 420 seconds did.

Consequence: the HTML path is a fallback with a hard stop, and `Blocked` is
raised rather than retried through a challenge. A partial scan is never reported
as exhaustive — `scan_status` carries the blocked count and item counts so the
gap is visible.

## Engineering mistakes worth remembering

- **A `None` price silently removes a record from every ranking.** `rank()`
  skips anything without a delivered total, so a backend that stores
  `price=None` looks like a backend that found nothing. Assert that a new
  backend produces a rankable record, not just a dict.
- **Never overwrite a module-level registry during a partial edit.** Truncating
  a tuple literal mid-expression left `config.py` unimportable in a way that
  looked like a syntax error in a dataclass.
- **Test fixtures must be realistic.** A synthetic blocked-page fixture put the
  error title past the 4 KB window the detector actually inspects, and the
  failure pointed at the parser instead of at the fixture.
