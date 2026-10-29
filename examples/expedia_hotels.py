"""Expedia hotel search and room rates through ScrapingBee.

Verified live on 2026-10-29 against expedia.com, Lisbon, 7 to 10 December 2026.

Two calls, two jobs:
  search()  the Hotel-Search results page, 100 property cards per call
  rooms()   one Hotel-Information page, every room offer with its rate

Both use the same request recipe. The four parameters below are what got
Expedia to return the full result list; dropping wait_browser=load cut the
search page to the first 10 cards.
Set SCRAPINGBEE_API_KEY in your environment before running.
"""

import csv
import json
import os
import re
import sys
from urllib.parse import parse_qs, urlparse

import requests

API = "https://app.scrapingbee.com/api/v1/"
HEADERS = {"Authorization": f"Bearer {os.environ['SCRAPINGBEE_API_KEY']}"}

# 75 credits per call. Lower tiers came back as Expedia 400/429 or 613
# timeouts during testing, and failed calls are not billed.
RECIPE = {
    "stealth_proxy": "true",
    "block_resources": "false",
    "wait_browser": "load",
    "country_code": "us",
}

CARD = '[data-stid="lodging-card-responsive"]'

SEARCH_RULES = {
    "hotels": {
        "selector": CARD,
        "type": "list",
        "output": {
            # h3.uitk-heading, not plain h3: the first h3 in some cards is a
            # hidden "Photo gallery for ..." label.
            "name": "h3.uitk-heading",
            "area": "div.truncate-lines-3",
            "score": "span.uitk-badge-base-large span.uitk-badge-base-text",
            "score_label": "span.uitk-type-medium.uitk-text-emphasis-theme",
            "reviews": "span.uitk-type-200.uitk-type-regular",
            "nightly": "div.uitk-type-end.uitk-type-400",
            # Screen reader lines. One says "The current price is $X total",
            # discounted cards add "The previous price was $Y".
            "hidden": {"selector": "div.is-visually-hidden", "type": "list"},
            "link": {"selector": 'a[href*="Hotel-Information"]', "output": "@href"},
        },
    },
    # Grey skeleton cards Expedia has not filled yet. Non zero means the
    # page was captured with listings still loading.
    "pending": {"selector": '[data-stid="search-listing-placeholder"]', "type": "list"},
}

ROOM_RULES = {
    "name": '[data-stid="content-hotel-title"] h1',
    "score": '[data-stid="content-hotel-reviewsummary"] span.uitk-badge-base-text',
    "rooms": {
        "selector": '[data-stid^="property-offer-"]',
        "type": "list",
        "output": {
            "room": "h3.uitk-heading-6",
            "facts": {"selector": "div.uitk-type-300.uitk-text-default-theme", "type": "list"},
            "nightly": "div.uitk-type-end.uitk-type-400",
            "hidden": {"selector": "div.is-visually-hidden.uitk-type-300", "type": "list"},
            "availability": "div.uitk-text-negative-theme",
        },
    },
}

MONEY = re.compile(r"\$([\d,]+)")


def _call(url, rules, wait_for=None):
    params = dict(RECIPE, url=url, extract_rules=json.dumps(rules))
    if wait_for:
        params["wait_for"] = wait_for
    r = requests.get(API, headers=HEADERS, params=params, timeout=200)
    r.raise_for_status()
    return r.json(), r.headers.get("spb-cost")


def _money(text):
    m = MONEY.search(text or "")
    return int(m.group(1).replace(",", "")) if m else None


def _current_total(lines):
    for line in lines or []:
        if "current price" in line:
            return _money(line)
    return None


def _previous_total(lines):
    for line in lines or []:
        if "previous price" in line:
            return _money(line)
    return None


def search(destination, check_in, check_out):
    """Up to 100 hotels for one destination and date pair, 75 credits."""
    url = (
        "https://www.expedia.com/Hotel-Search"
        f"?destination={destination.replace(' ', '+')}"
        f"&startDate={check_in}&endDate={check_out}"
    )
    data, cost = _call(url, SEARCH_RULES, wait_for=CARD)
    if data.get("pending"):
        data, cost = _call(url, SEARCH_RULES, wait_for=CARD)
    if data.get("pending"):
        # Seen on some date pairs: 3 cards plus 20 skeletons, repeatably.
        # Report it rather than hand back a short list as if it were whole.
        print(f"warning: {url} came back partial, {len(data['hotels'])} cards", file=sys.stderr)
    hotels = []
    for card in data.get("hotels", []):
        link = card.get("link") or ""
        query = parse_qs(urlparse(link).query)
        hotel_id = re.search(r"\.h(\d+)\.Hotel-Information", link)
        hotels.append(
            {
                "hotel_id": hotel_id.group(1) if hotel_id else None,
                "name": card.get("name"),
                "area": card.get("area"),
                "score": float(card["score"]) if card.get("score") else None,
                "score_label": card.get("score_label"),
                "reviews": int(re.sub(r"\D", "", card["reviews"]) or 0) if card.get("reviews") else None,
                "nightly_usd": _money(card.get("nightly")),
                "total_usd": _current_total(card.get("hidden")),
                "was_usd": _previous_total(card.get("hidden")),
                # The card link carries the sponsored flag Expedia sets on ads.
                "sponsored": query.get("sponsoredListing", ["false"])[0] == "true",
                "url": "https://www.expedia.com" + link.split("?")[0] if link else None,
            }
        )
    return hotels, cost


def unique(hotels):
    """Sponsored properties also appear as organic cards. Keep the organic one.

    The Lisbon test run returned 100 cards, 16 sponsored, 96 distinct hotels.
    """
    seen = {}
    for h in hotels:
        key = h["hotel_id"] or h["name"]
        if key not in seen or seen[key]["sponsored"]:
            seen[key] = h
    return list(seen.values())


def rooms(hotel_url, check_in, check_out):
    """Every room offer on one property page, 75 credits.

    Do not read application/ld+json here. On this page those blocks are
    ItemLists of OTHER hotels Expedia recommends, not the one you asked for.
    """
    url = f"{hotel_url}?chkin={check_in}&chkout={check_out}"
    data, cost = _call(url, ROOM_RULES)
    offers = []
    for o in data.get("rooms", []):
        facts = [f for f in o.get("facts") or [] if "price" not in f]
        offers.append(
            {
                "room": o.get("room"),
                "facts": facts,
                "nightly_usd": _money(o.get("nightly")),
                "total_usd": _current_total(o.get("hidden")),
                "sold_out": "sold out" in (o.get("availability") or "").lower(),
                "availability": o.get("availability") or None,
            }
        )
    return {"name": data.get("name"), "score": data.get("score"), "rooms": offers}, cost


def to_csv(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    city = sys.argv[1] if len(sys.argv) > 1 else "Lisbon"
    check_in, check_out = "2026-12-07", "2026-12-10"

    cards, cost = search(city, check_in, check_out)
    hotels = unique(cards)
    print(f"{len(cards)} cards, {len(hotels)} distinct hotels in {city}, {cost} credits")
    for h in hotels[:5]:
        print(f"  {h['score']}  ${h['nightly_usd']}/night  ${h['total_usd']} total  {h['name']} ({h['area']})")

    discounted = [h for h in hotels if h["was_usd"]]
    print(f"{len(discounted)} cards show a struck through previous price")
    to_csv(hotels, f"expedia_{city.lower()}_{check_in}.csv")

    first = hotels[0]
    detail, cost = rooms(first["url"], check_in, check_out)
    print(f"\n{detail['name']}, {len(detail['rooms'])} room offers, {cost} credits")
    for r in detail["rooms"][:6]:
        state = "sold out" if r["sold_out"] else f"${r['nightly_usd']}/night, ${r['total_usd']} total"
        print(f"  {r['room']}: {state}")
