# Expedia Scraper API

<p align="center">
  <a href="https://www.scrapingbee.com/">
    <img src="REPLACE_WITH_SCREENSHOT_URL" alt="expedia-scraper-api" />
  </a>
</p>

[![checks](https://github.com/ScrapingBee/expedia-scraper-api/workflows/checks/badge.svg)](https://github.com/ScrapingBee/expedia-scraper-api/actions)
[![license](https://img.shields.io/github/license/ScrapingBee/expedia-scraper-api.svg)](LICENSE)

One request, 75 credits, 100 Lisbon hotels with nightly rate, stay total, guest score and review count. That is what this [Expedia scraper API](https://www.scrapingbee.com/scrapers/expedia-scraper-api/) recipe returned on 2026-10-29, and the four request parameters that made it work are the main thing worth copying from this repo.

Everything here runs on [ScrapingBee's web scraping API](https://www.scrapingbee.com/) and reads public, logged out pages only. The code in [`examples/`](examples) was executed against expedia.com while writing this README, and every number below comes from those runs.

## Recipe 0: the request that holds up

```python
RECIPE = {
    "stealth_proxy": "true",
    "block_resources": "false",
    "wait_browser": "load",
    "country_code": "us",
}
```

How we got there, measured on the Hotel-Search page for Lisbon, 7 to 10 December 2026:

| Attempt | Credits billed | What came back |
|---|---|---|
| `mode=auto` (first two runs) | 25 | Settled on premium proxy plus JavaScript, **10 cards only** |
| `mode=auto` (later runs) | 0 | `613`, every tier tried, nothing billed |
| `premium_proxy` + `render_js` | 0 | Expedia answered `400`, then `429` |
| Recipe 0 above | 75 | **100 cards**, every one with a name and a nightly rate |

The jump from 10 cards to 100 comes from `wait_browser=load` together with `block_resources=false`. With the defaults the browser hands the page back at `domcontentloaded`, before Expedia has filled the result list. Failed calls cost nothing, which made the trial and error cheap: a run that ends in an error is not billed.

`country_code=us` keeps prices in dollars and the copy in English. It adds nothing to the 75 credit cost.

## Recipe 1: a destination search as rows

[`examples/expedia_hotels.py`](examples/expedia_hotels.py), `search()`.

The search URL is plain query string:

```
https://www.expedia.com/Hotel-Search?destination=Lisbon&startDate=2026-12-07&endDate=2026-12-10
```

Expedia resolves the free text destination itself (the response carried `regionId=2080` in its resolved URL). Each property sits in a `[data-stid="lodging-card-responsive"]` card, and `data-stid` attributes are the stable hooks on this page. The long `uitk-*` utility classes are styling and do change.

```python
"hotels": {
    "selector": '[data-stid="lodging-card-responsive"]',
    "type": "list",
    "output": {
        "name": "h3.uitk-heading",
        "area": "div.truncate-lines-3",
        "score": "span.uitk-badge-base-large span.uitk-badge-base-text",
        "reviews": "span.uitk-type-200.uitk-type-regular",
        "nightly": "div.uitk-type-end.uitk-type-400",
        "hidden": {"selector": "div.is-visually-hidden", "type": "list"},
        "link": {"selector": 'a[href*="Hotel-Information"]', "output": "@href"},
    },
}
```

A row after parsing:

```json
{
  "hotel_id": "72278510",
  "name": "The Emerald House Lisbon, Curio Collection By Hilton",
  "area": "Lapa",
  "score": 9.6,
  "score_label": "Exceptional",
  "reviews": 622,
  "nightly_usd": 188,
  "total_usd": 624,
  "was_usd": null,
  "sponsored": true,
  "url": "https://www.expedia.com/Lisbon-District-Hotels-The-Emerald-House-Lisbon.h72278510.Hotel-Information"
}
```

Three details decide whether those rows are right:

**Guest scores are out of 10.** The badge reads `9.6` and its screen reader text says `9.6 out of 10`. Older Expedia scrapers expect a five star string and quietly misparse this.

**The total lives in screen reader text.** The visible card shows `$188 nightly`. The stay total sits in a hidden line, `The current price is $624 total`. Discounted cards add a second hidden line, `The previous price was $659`, and in the Lisbon run 72 of 100 cards had one. Grab all hidden lines as a list and pick by wording; selecting the first one returns the old price on discounted cards.

**Sponsored cards repeat.** 16 of the 100 cards were ads, and 4 of those also appeared as organic cards, so the page held 96 distinct hotels. The card link carries `sponsoredListing=true` on ads and the property id as `.h72278510.` in its path. `unique()` keys on that id and keeps the organic copy.

The link query string also includes `top_dp=624&top_cur=USD`, the same stay total in machine form, which is handy as a cross check.

## Recipe 2: every room on one property

`rooms()` in the same file. Point it at the `url` from Recipe 1 plus `chkin` and `chkout`:

```
The Emerald House Lisbon, Curio Collection By Hilton, 9 room offers, 75 credits
  Deluxe Room, 2 Twin Beds: $188/night, $624 total
  Deluxe Room, 1 King Bed, Balcony: $193/night, $640 total
  Deluxe Room, 1 King Bed, Balcony (View): $223/night, $734 total
  Premium Room, 1 King Bed, Corner: $227/night, $750 total
  Premium Room, 1 King Bed, Balcony (View): $252/night, $829 total
  Suite, 1 King Bed, Terrace (Lapa): $307/night, $1002 total
```

Room offers are `[data-stid^="property-offer-"]` blocks numbered from 0. Each carries the room name, a facts list (`258 sq ft`, `Sleeps 2`, `2 Twin Beds`, `Free WiFi`), the nightly and total rates, and an availability line such as `We have 2 left` or `We are sold out`. Sold out rooms stay on the page with empty prices, so filter on that line rather than on a missing price.

**Do not trust the JSON-LD on this page.** The hotel page carries `application/ld+json` blocks, and they look like exactly what you want: `@type: Hotel`, address, `starRating`, `aggregateRating`, amenities. Read the names. They are `ItemList` entries for *other* properties Expedia recommends (Westlight Cascais Chalet, EPIC SANA Marques, Locke de Santa Joana on the Emerald House page). The hotel you asked for is only in the HTML.

## Recipe 3: a rate calendar

[`examples/rate_calendar.js`](examples/rate_calendar.js) searches one destination for several check in dates and keeps one row per date: hotel count, cheapest nightly rate, median, and the cheapest property's name. Node 18 or later, no dependencies.

```bash
export SCRAPINGBEE_API_KEY=...
node examples/rate_calendar.js Lisbon 2026-12-07
```

```
2026-12-07  100 hotels  min $58  median $122  cheapest: easyHotel Lisbon  [75 credits]
```

It runs dates one after another. Expedia answered some test calls with `429`, and those are unbilled but also useless, so the script does not add load by running dates in parallel.

## When a page comes back half loaded

Some date pairs return 3 real cards and 20 grey skeleton cards (`[data-stid="search-listing-placeholder"]`). Lisbon for 14 to 17 December did this on five consecutive calls, including a retry with an extra 8 second `wait`, a retry against the fully resolved `regionId` URL, and a JavaScript scenario that scrolled the page three times. The page header still said `300+ properties`.

Both scripts extract the skeletons alongside the hotels. If any are present the call is retried once, and if they are still there the result is flagged as partial (a stderr warning in Python, `(PARTIAL)` in the Node output). A short list that looks complete is worse than an error, so the flag stays on.

## Cost per job

| Job | Calls | Credits |
|---|---|---|
| One destination, one date pair, up to 100 hotels | 1 | 75 |
| Room list for one hotel | 1 | 75 |
| 30 day rate calendar for one destination | 30 | 2,250 |
| Room lists for the 10 cheapest hotels it finds | 10 | 750 |

ScrapingBee does not cache, so repeating a search is billed again. A retried partial page bills twice. See [ScrapingBee pricing](https://www.scrapingbee.com/pricing/) for what a plan's monthly credits cover; new accounts start with 1,000 free credits.

## Expedia API or scraping?

Expedia Group's own APIs are partner programs for companies that sell its inventory, with their own application and contract. If you want the prices and scores a traveller sees on expedia.com for market research or rate monitoring, the public page is the source, and this repo reads it.

Related ScrapingBee endpoints for the same job: [Hotels.com scraper](https://www.scrapingbee.com/scrapers/hotels.com-api/), [Booking scraper](https://www.scrapingbee.com/scrapers/booking-api/), [Google Hotels scraper](https://www.scrapingbee.com/scrapers/google-hotel-scraper/), [Priceline scraper](https://www.scrapingbee.com/scrapers/priceline-api/), [Kayak scraper](https://www.scrapingbee.com/scrapers/kayak-api/), [Vrbo scraper](https://www.scrapingbee.com/scrapers/vrbo-api/), [Tripadvisor scraper](https://www.scrapingbee.com/scrapers/tripadvisor-api/).

Reference: [data extraction rules](https://www.scrapingbee.com/documentation/data-extraction/), [JavaScript scenario](https://www.scrapingbee.com/documentation/js-scenario/), [HTML API parameters](https://www.scrapingbee.com/documentation/).

## Scope

Public search and property pages only. Trips, saved lists, member prices and anything else behind a sign in are out of scope, and scraping under login credentials is prohibited by ScrapingBee's terms of service. Expedia's own terms of use govern the site. Rates change by the minute and by visitor, so treat a scraped price as an observation with a timestamp, not a quote.

## FAQ

**How do I scrape Expedia hotel prices?**
Send the Hotel-Search URL with your destination and dates through ScrapingBee with stealth proxies, `wait_browser=load` and `block_resources=false`, and extract the `lodging-card-responsive` cards. One call returned 100 hotels for 75 credits.

**Why do I only get 10 hotels?**
The page was returned before the list finished loading. `wait_browser=load` fixed it in testing; Auto-Mode at 25 credits returned 10 cards.

**Why does my price column show the old price?**
Discounted cards carry two hidden price lines. Pick the one that says "current price".

**Is there structured data for the hotel on its own page?**
Not for that hotel. The JSON-LD on a property page describes recommended other hotels.

**Does it work for other currencies?**
`country_code` changes the exit country. This repo was tested with `us` and dollar prices only.

## License

MIT. See [LICENSE](LICENSE).
