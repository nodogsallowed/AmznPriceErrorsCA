# bot.py

import os
import json
import logging
import asyncio
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

# ─── Load environment variables ─────────────────────────────────────────────────
load_dotenv()
BOT_TOKEN      = os.getenv("TELEGRAM_BOT_TOKEN")
AFFILIATE_TAG  = os.getenv("AMZN_AFFILIATE_TAG", "amznerrorsca-20")
raw_channel    = os.getenv("TELEGRAM_CHANNEL", "AmznErrorsCA")
CHANNEL_ID     = raw_channel if raw_channel.startswith("@") else f"@{raw_channel}"
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "CrushTheCasino")  # your Telegram username without '@'

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

# ─── Fetch top category URLs from Amazon.ca ────────────────────────────────────
def get_category_urls():
    resp = requests.get("https://www.amazon.ca", headers=HEADERS, timeout=10)
    soup = BeautifulSoup(resp.text, "html.parser")
    links = soup.select("#nav-xshop a[href]")
    urls = []
    for a in links:
        href = a.get("href") or ""
        if not href.startswith("/"):
            continue
        full = f"https://www.amazon.ca{href}"
        suffix = "&sort=price-asc-rank" if "?" in full else "?sort=price-asc-rank"
        urls.append(full + suffix)
    return urls

# ─── Scrape >90% discount deals ─────────────────────────────────────────────────
def scrape_deals():
    deals = []
    for url in get_category_urls():
        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
        except Exception as e:
            logger.warning(f"Failed to fetch {url}: {e}")
            continue
        soup = BeautifulSoup(resp.text, "html.parser")
        items = soup.select('div[data-component-type="s-search-result"]')
        for it in items:
            title_el   = it.select_one("h2 a span")
            sale_whole = it.select_one("span.a-price-whole")
            sale_frac  = it.select_one("span.a-price-fraction")
            orig_el    = it.select_one("span.a-price.a-text-price span.a-offscreen")
            if not (title_el and sale_whole and orig_el):
                continue
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
            deals.append(deal)
    return deals

# ─── Telegram handlers ─────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome! Use /scrape to manually fetch latest deals, or I’ll auto-scrape hourly."
    )

async def scrape_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.effective_user.username or ""
    if username.lower() != ADMIN_USERNAME.lower():
        return await update.message.reply_text("❌ You are not authorized to run this.")
    await update.message.reply_text("🔍 Running manual scrape…")
    new = 0
    for deal in scrape_deals():
        if is_new_deal(deal["link"]):
            new += 1
            text = (
                f"🔥 *PRICE ERROR ALERT!* 🔥\n\n"
                f"🛍️ *{deal['title']}*\n"
                f"💸 *Now:* ${deal['sale_price']} (was ${deal['orig_price']})\n"
                f"📉 *Discount:* {deal['discount']}\n\n"
                f"[Buy Now]({deal['link']})"
            )
            await context.bot.send_message(
                chat_id=CHANNEL_ID,
                text=text,
                parse_mode="Markdown",
                disable_web_page_preview=True
            )
    await update.message.reply_text(f"✅ Manual scrape complete: sent {new} new deals.")

async def job_callback(context: ContextTypes.DEFAULT_TYPE):
    for deal in scrape_deals():
        if is_new_deal(deal["link"]):
            text = (
                f"🔥 *PRICE ERROR ALERT!* 🔥\n\n"
                f"🛍️ *{deal['title']}*\n"
                f"💸 *Now:* ${deal['sale_price']} (was ${deal['orig_price']})\n"
                f"📉 *Discount:* {deal['discount']}\n\n"
                f"[Buy Now]({deal['link']})"
            )
            await context.bot.send_message(
                chat_id=CHANNEL_ID,
                text=text,
                parse_mode="Markdown",
                disable_web_page_preview=True
            )

# ─── Main entrypoint ─────────────────────────────────────────────────────────
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("scrape", scrape_command))
    app.job_queue.run_repeating(job_callback, interval=3600, first=10)
    logger.info("▶️ Bot starting…")
    app.run_polling()

if __name__ == "__main__":
    main()
