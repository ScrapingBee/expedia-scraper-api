// Expedia rate calendar: the same destination searched across several
// check in dates, reduced to one row per date.
//
// Verified live on 2026-10-29 against expedia.com, Lisbon.
// Node 18+ (global fetch). Set SCRAPINGBEE_API_KEY before running.
// Cost: 75 credits per date searched.

const API = "https://app.scrapingbee.com/api/v1/";
const CARD = '[data-stid="lodging-card-responsive"]';

const RULES = {
  hotels: {
    selector: CARD,
    type: "list",
    output: {
      name: "h3.uitk-heading",
      nightly: "div.uitk-type-end.uitk-type-400",
      link: { selector: 'a[href*="Hotel-Information"]', output: "@href" },
    },
  },
  // Skeleton cards still waiting for data when the page was captured.
  pending: { selector: '[data-stid="search-listing-placeholder"]', type: "list" },
};

function addDays(iso, n) {
  const d = new Date(iso + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

function dollars(text) {
  const m = /\$([\d,]+)/.exec(text || "");
  return m ? Number(m[1].replace(/,/g, "")) : null;
}

async function searchNight(destination, checkIn, nights, attempt = 1) {
  const target =
    "https://www.expedia.com/Hotel-Search" +
    `?destination=${encodeURIComponent(destination)}` +
    `&startDate=${checkIn}&endDate=${addDays(checkIn, nights)}`;

  const params = new URLSearchParams({
    url: target,
    stealth_proxy: "true",
    block_resources: "false",
    wait_browser: "load",
    country_code: "us",
    wait_for: CARD,
    extract_rules: JSON.stringify(RULES),
  });

  const res = await fetch(`${API}?${params}`, {
    headers: { Authorization: `Bearer ${process.env.SCRAPINGBEE_API_KEY}` },
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);

  const { hotels = [], pending = [] } = await res.json();
  // A full page returned 95 to 100 cards in testing. Some date pairs came
  // back as 3 cards plus 20 skeletons, and kept doing so on retry, longer
  // waits and scrolling. Retry once, then flag the row instead of trusting it.
  if (pending.length && attempt < 2) {
    return searchNight(destination, checkIn, nights, attempt + 1);
  }
  // Sponsored cards repeat an organic one, so key on the h<id> in the link.
  const byId = new Map();
  for (const h of hotels) {
    const id = (/\.h(\d+)\.Hotel-Information/.exec(h.link || "") || [])[1] || h.name;
    const rate = dollars(h.nightly);
    if (rate !== null && !byId.has(id)) byId.set(id, { name: h.name, rate });
  }
  const rates = [...byId.values()].map((h) => h.rate).sort((a, b) => a - b);
  const cheapest = [...byId.values()].find((h) => h.rate === rates[0]);

  return {
    checkIn,
    partial: pending.length > 0,
    hotels: rates.length,
    min: rates[0],
    median: rates[Math.floor(rates.length / 2)],
    cheapest: cheapest ? cheapest.name : null,
    // Credits for the call that was kept. A retried partial page bills twice.
    credits: res.headers.get("spb-cost"),
  };
}

async function main() {
  const destination = process.argv[2] || "Lisbon";
  const start = process.argv[3] || "2026-12-07";
  const dates = [0, 7].map((offset) => addDays(start, offset));

  // Sequential on purpose: Expedia answered some test calls with 429.
  for (const checkIn of dates) {
    const row = await searchNight(destination, checkIn, 3);
    console.log(
      `${row.checkIn}  ${row.hotels} hotels${row.partial ? " (PARTIAL)" : ""}  min $${row.min}  median $${row.median}  ` +
        `cheapest: ${row.cheapest}  [${row.credits} credits]`
    );
  }
}

main().catch((err) => {
  console.error(err.message);
  process.exit(1);
});
