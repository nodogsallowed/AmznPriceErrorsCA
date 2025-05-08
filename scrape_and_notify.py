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
BOT_TOKEN      = os.getenv("TELEGRAM_BOT_TOKEN")
AFFILIATE_TAG  = os.getenv("AMZN_AFFILIATE_TAG", "amznerrorsca-20")
RAW_CHANNEL    = os.getenv("TELEGRAM_CHANNEL", "AmznErrorsCA")
CHANNEL_ID     = RAW_CHANNEL if RAW_CHANNEL.startswith("@") else f"@{RAW_CHANNEL}"
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME")
DEBUG_PING     = os.getenv("DEBUG_PING", "false").lower() == "true"

if not BOT_TOKEN:
    raise RuntimeError("Missing TELEGRAM_BOT_TOKEN in environment variables")

# ─── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── Persistence: prevent duplicate alerts ───────────────────────────────────────
SEEN_FILE = "seen.json"
def load_seen():
    try:
        return json.load(open(SEEN_FILE))
    except:
        return {"links": []}

def save_seen(data):
    json.dump(data, open(SEEN_FILE, "w"), indent=2)

def is_new_deal(link: str) -> bool:
    seen = load_seen()
    if link in seen["links"]:
        return False
    seen["links"].append(link)
    save_seen(seen)
    return True

# ─── HTTP headers ──────────────────────────────────────────────────────────────
HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept-Language": "en-CA,en-US;q=0.9"
}

# ─── Top-category list ─────────────────────────────────────────────────────────
CATEGORY_PATHS = [
    "/b?node=37219708011&ref_=nav_cs_cash_desk_disco",
    "/Best-Sellers-generic/zgbs/",
    "/Electronics-Accessories/b/?ie=UTF8&node=667823011&ref_=nav_cs_electronics",
    "/Books-Used-Books-Textbooks/b/?ie=UTF8&node=916520&ref_=nav_cs_books",
    "/Beauty/b/?ie=UTF8&node=6205124011&ref_=nav_cs_beauty",
    "/Toys-Games/b/?ie=UTF8&node=6205517011&ref_=nav_cs_toys",
    "/sporting-goods/b/?ie=UTF8&node=2242989011&ref_=nav_cs_sports",
    "/Computers-Accessories/b/?ie=UTF8&node=2404990011&ref_=nav_cs_pc",
    "/Health-Personal-Care/b/?ie=UTF8&node=6205177011&ref_=nav_cs_hpc",
    "/Home-Improvement/b/?ie=UTF8&node=3006902011&ref_=nav_cs_hi",
    "/Fashion/b/?ie=UTF8&node=21204935011&ref_=nav_cs_fashion",
    "/video-games-hardware-accessories/b/?ie=UTF8&node=3198031&ref_=nav_cs_video_games",
    "/grocery/b/?ie=UTF8&node=6967215011&ref_=nav_cs_grocery",
    "/pet-supplies-dog-cat-food-bed-toy/b/?ie=UTF8&node=6205514011&ref_=nav_cs_pets",
    "/gp/browse.html?node=3561346011&ref_=nav_cs_baby",
]

def get_category_urls():
    urls = []
    for path in CATEGORY_PATHS:
        suffix = "&sort=price-asc-rank" if "?" in path else "?sort=price-asc-rank"
        urls.append(f"https://www.amazon.ca{path}{suffix}")
    logger.info("Will scan these categories:\n" + "\n".join(urls))
    return urls

# ─── Scrape each category for ≥90% discount ───────────────────────────────────
def scrape_category(url):
    resp = requests.get(url, headers=HEADERS, timeout=10)
    soup = BeautifulSoup(resp.text, "html.parser")
    items = soup.select('div[data-component-type="s-search-result"]')
    deals = []
    for it in items:
        title_el   = it.select_one("h2 a span")
        sale_whole = it.select_one("span.a-price-whole")
        sale_frac  = it.select_one("span.a-price-fraction")
        orig_el    = it.select_one("span.a-price.a-text-price span.a-offscreen")
        if not (title_el and sale_whole and orig_el):
            continue

        sale_str = f"{sale_whole.text.strip().replace(',', '')}.{(sale_frac.text.strip() if sale_frac else '00')}"
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
        deals.append({
            "title":      title_el.text.strip(),
            "sale_price": f"{sale_price:.2f}",
            "orig_price": f"{orig_price:.2f}",
            "discount":   f"{int(discount)}%",
            "link":       link,
            "asin":       asin
        })
    return deals

def scrape_deals():
    all_deals = []
    for url in get_category_urls():
        status = requests.get(url, headers=HEADERS, timeout=10).status_code
        logger.info(f"GET {url} → {status}")
        all_deals.extend(scrape_category(url))
    logger.info(f"Finished scraping {len(CATEGORY_PATHS)} categories, found {len(all_deals)} raw deals")
    return all_deals

# ─── Price history via CamelCamelCamel ────────────────────────────────────────
def get_price_history(asin):
    ccc_url = f"https://camelcamelcamel.com/product/{asin}"
    try:
        resp = requests.get(ccc_url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")
        low = soup.select_one(".stat.lowest span.value")
        avg = soup.select_one(".stat.average span.value")
        return {
            "lowest":  low.text.strip() if low else None,
            "average": avg.text.strip() if avg else None,
            "url":     ccc_url
        }
    except Exception as e:
        logger.warning(f"C3 history failed for {asin}: {e}")
        return None

# ─── Async runner ─────────────────────────────────────────────────────────────
async def run_and_notify():
    bot = Bot(BOT_TOKEN)
    sent = 0

    for deal in scrape_deals():
        if is_new_deal(deal["link"]):
            sent += 1
            hist = get_price_history(deal["asin"])
            hist_text = ""
            if hist and hist["lowest"]:
                hist_text = f"\n📈 Lowest: {hist['lowest']} | Avg: {hist['average']}"
            text = (
                f"🔥 *PRICE ERROR!* 🔥\n\n"
                f"🛍️ *{deal['title']}*\n"
                f"💸 Now: ${deal['sale_price']} (was ${deal['orig_price']})\n"
                f"📉 {deal['discount']}{hist_text}\n\n"
                f"[Buy Now]({deal['link']})"
            )
            await bot.send_message(
                chat_id=CHANNEL_ID, text=text,
                parse_mode="Markdown", disable_web_page_preview=True
            )

    logger.info(f"Sent {sent} new deal(s).")

    if DEBUG_PING:
        await bot.send_message(chat_id=CHANNEL_ID,
                               text="✅ Debug ping: GitHub Actions reached your Telegram channel!")

if __name__ == "__main__":
    asyncio.run(run_and_notify())
