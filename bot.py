# bot.py

import os
import json
import logging
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from telegram import Update, __version__ as TG_VER
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

# ─── 1. Ensure correct PTB version ────────────────────────────────────────────
if int(TG_VER.split('.')[0]) < 20:
    raise RuntimeError(f"Require PTB v20+, found {TG_VER}")

# ─── 2. Load your secrets from .env ──────────────────────────────────────────
load_dotenv()
BOT_TOKEN     = os.getenv("TELEGRAM_BOT_TOKEN")
AFFILIATE_TAG = os.getenv("AMZN_AFFILIATE_TAG", "amznerrorsca-20")
CHANNEL_ID    = os.getenv("TELEGRAM_CHANNEL", "@YourChannelUsername")

if not BOT_TOKEN:
    raise RuntimeError("Missing TELEGRAM_BOT_TOKEN in .env")

# ─── 3. Configure logging ─────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── 4. Persistence: avoid duplicate alerts ───────────────────────────────────
SEEN_FILE = "seen.json"
def is_new_deal(link: str) -> bool:
    try:
        seen = json.loads(open(SEEN_FILE, 'r').read())
    except (FileNotFoundError, json.JSONDecodeError):
        seen = []
    if link in seen:
        return False
    seen.append(link)
    with open(SEEN_FILE, 'w') as f:
        json.dump(seen, f)
    return True

# ─── 5. Scraping logic ────────────────────────────────────────────────────────
SEARCH_URL = "https://www.amazon.ca/s?k=laptop&sort=price-asc-rank"
HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept-Language": "en-CA,en-US;q=0.9,en;q=0.8",
}

def scrape_amazon_ca_deals():
    """Return list of {'title','price','link'} for price-error deals."""
    resp = requests.get(SEARCH_URL, headers=HEADERS, timeout=10)
    logger.info(f"GET {SEARCH_URL} → {resp.status_code}")
    soup = BeautifulSoup(resp.text, "html.parser")

    containers = soup.select('div[data-component-type="s-search-result"]')
    logger.info(f"Found {len(containers)} product containers")
    deals = []

    for item in containers:
        # Title
        h2    = item.select_one("h2 a span")
        whole = item.select_one("span.a-price-whole")
        frac  = item.select_one("span.a-price-fraction")
        if not (h2 and whole):
            continue

        price = float(f"{whole.text.replace(',', '')}.{frac.text if frac else '00'}")
        if price < 5.0:
            continue

        # ASIN + affiliate link
        link_tag = item.select_one("h2 a[href]")
        asin     = link_tag['href'].split("/dp/")[-1].split("/")[0]
        aff_link = f"https://www.amazon.ca/dp/{asin}?tag={AFFILIATE_TAG}"

        deals.append({
            "title": h2.text.strip(),
            "price": f"{price:.2f}",
            "link":  aff_link
        })

    logger.info(f"Scraped {len(deals)} deals below threshold")
    return deals

# ─── 6. Telegram handlers ────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome! I'll post Canada-only Amazon price-error alerts here."
    )

async def job_callback(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Running scheduled scrape…")
    for deal in scrape_amazon_ca_deals():
        if is_new_deal(deal["link"]):
            text = (
                f"🔥 *PRICE ERROR ALERT!* 🔥\n\n"
                f"🛍️ *{deal['title']}*\n"
                f"💸 *Now:* ${deal['price']}\n\n"
                f"[Buy Now]({deal['link']})"
            )
            await context.bot.send_message(
                chat_id=CHANNEL_ID,
                text=text,
                parse_mode="Markdown",
                disable_web_page_preview=True
            )

# ─── 7. Entrypoint ───────────────────────────────────────────────────────────
def main():
    # Build the bot
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Register /start
    app.add_handler(CommandHandler("start", start))

    # Schedule the job: first run after 10s, then every hour
    app.job_queue.run_repeating(job_callback, interval=3600, first=10)

    # Start polling
    logger.info("▶️ Bot is starting…")
    app.run_polling()

if __name__ == "__main__":
    main()
