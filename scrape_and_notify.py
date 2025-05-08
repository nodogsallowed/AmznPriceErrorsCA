# scrape_and_notify.py

import os
import json
import logging
import asyncio
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from telegram import Bot

# ─── Load environment variables ─────────────────────────────────────────────────
load_dotenv()
BOT_TOKEN     = os.getenv("TELEGRAM_BOT_TOKEN")
AFFILIATE_TAG = os.getenv("AMZN_AFFILIATE_TAG", "amznerrorsca-20")
raw_channel   = os.getenv("TELEGRAM_CHANNEL", "AmznErrorsCA")
CHANNEL_ID    = raw_channel if raw_channel.startswith("@") else f"@{raw_channel}"

if not BOT_TOKEN:
    raise RuntimeError("Missing TELEGRAM_BOT_TOKEN in environment variables")

# ─── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── Persistence: prevent duplicate alerts ───────────────────────────────────────
SEEN_FILE = "seen.json"
def is_new_deal(link: str) -> bool:
    try:
        seen = json.load(open(SEEN_FILE, 'r'))
    except (FileNotFoundError, json.JSONDecodeError):
        seen = []
    if link in seen:
        return False
    seen.append(link)
    json.dump(seen, open(SEEN_FILE, "w"))
    return True

# ─── HTTP headers ──────────────────────────────────────────────────────────────
HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept-Language": "en-CA,en-US;q=0.9,en;q=0.8",
}

# ─── 1. Fetch top category URLs from Amazon.ca homepage ────────────────────────
def get_category_urls():
    resp = requests.get("https://www.amazon.ca", headers=HEADERS, timeout=10)
    soup = BeautifulSoup(resp.text, "html.parser")
    links = soup.select("#nav-xshop a[href]")
    urls = []
    for a in links:
        href = a.get("href")
        if not href or not href.startswith("/"):
            continue
        full = f"https://www.amazon.ca{href}"
        if "?" in full:
            urls.append(full + "&sort=price-asc-rank")
        else:
            urls.append(full + "?sort=price-asc-rank")
    return urls

# ─── 2. Scrape each category for >90% discount deals ──────────────────────────
def scrape_deals():
    deals = []
    for url in get_category_urls():
        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
        except Exception as e:
            logger.warning(f"Failed to fetch {url}: {e}")
            continue
        logger.info(f"GET {url} → {resp.status_code}")
        soup = BeautifulSoup(resp.text, "html.parser")
        items = soup.select('div[data-component-type="s-search-result"]')
        logger.info(f"Category {url}: found {len(items)} items")

        for it in items:
            title_el = it.select_one("h2 a span")
            sale_whole = it.select_one("span.a-price-whole")
            sale_frac = it.select_one("span.a-price-fraction")
            orig_el = it.select_one("span.a-price.a-text-price span.a-offscreen")
            if not (title_el and sale_whole and orig_el):
                continue

            # parse sale and original prices
            sale_str = f"{sale_whole.text.strip().replace(',', '')}.{sale_frac.text.strip() if sale_frac else '00'}"
            orig_str = orig_el.text.strip().lstrip('$').replace(',', '')
            try:
                sale_price = float(sale_str)
                orig_price = float(orig_str)
            except ValueError:
                continue

            discount = (orig_price - sale_price) / orig_price * 100
            if discount < 90:
                continue

            asin = it.select_one("h2 a[href]")["href"].split("/dp/")[-1].split("/")[0]
            link = f"https://www.amazon.ca/dp/{asin}?tag={AFFILIATE_TAG}"
            deal = {
                "title":      title_el.text.strip(),
                "sale_price": f"{sale_price:.2f}",
                "orig_price": f"{orig_price:.2f}",
                "discount":   f"{int(discount)}%",
                "link":       link
            }
            logger.info(f"Discount deal found: {deal}")
            deals.append(deal)
    return deals

# ─── 3. Async runner ─────────────────────────────────────────────────────────
async def run_and_notify():
    bot = Bot(BOT_TOKEN)
    new_count = 0
    for deal in scrape_deals():
        if is_new_deal(deal["link"]):
            new_count += 1
            text = (
                f"🔥 *PRICE ERROR ALERT!* 🔥\n\n"
                f"🛍️ *{deal['title']}*\n"
                f"💸 *Now:* ${deal['sale_price']} (was ${deal['orig_price']})\n"
                f"📉 *Discount:* {deal['discount']}\n\n"
                f"[Buy Now]({deal['link']})"
            )
            await bot.send_message(
                chat_id=CHANNEL_ID,
                text=text,
                parse_mode="Markdown",
                disable_web_page_preview=True
            )
    logger.info(f"Sent {new_count} new deal(s).")

    # Optional debug ping
    if os.getenv("DEBUG_PING", "false").lower() == "true":
        await bot.send_message(
            chat_id=CHANNEL_ID,
            text="✅ Debug ping: GitHub Actions successfully reached your Telegram channel!"
        )

if __name__ == "__main__":
    asyncio.run(run_and_notify())
