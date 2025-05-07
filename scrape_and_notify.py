# scrape_and_notify.py

import os, json, logging, requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from telegram import Bot

# ─── Load secrets ─────────────────────────────────────────────────────────────
load_dotenv()
BOT_TOKEN     = os.getenv("TELEGRAM_BOT_TOKEN")
AFFILIATE_TAG = os.getenv("AMZN_AFFILIATE_TAG")
CHANNEL_ID    = os.getenv("TELEGRAM_CHANNEL")

# ─── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ─── State storage ────────────────────────────────────────────────────────────
SEEN_FILE = "seen.json"
def is_new_deal(link):
    try:
        seen = json.load(open(SEEN_FILE))
    except:
        seen = []
    if link in seen:
        return False
    seen.append(link)
    json.dump(seen, open(SEEN_FILE, "w"))
    return True

# ─── Scraper ──────────────────────────────────────────────────────────────────
SEARCH_URL = "https://www.amazon.ca/s?k=laptop&sort=price-asc-rank"
HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept-Language": "en-CA,en-US;q=0.9,en;q=0.8",
}

def scrape_deals():
    resp = requests.get(SEARCH_URL, headers=HEADERS, timeout=10)
    logger.info(f"GET {SEARCH_URL} → {resp.status_code}")
    soup = BeautifulSoup(resp.text, "html.parser")
    items = soup.select('div[data-component-type="s-search-result"]')
    logger.info(f"Found {len(items)} items")

    deals = []
    for it in items:
        title_el = it.select_one("h2 a span")
        whole    = it.select_one("span.a-price-whole")
        frac     = it.select_one("span.a-price-fraction")
        if not (title_el and whole):
            continue
        price = float(f"{whole.text.replace(',','')}.{frac.text if frac else '00'}")
        if price < 5.0:
            continue
        asin = it.select_one("h2 a")["href"].split("/dp/")[-1].split("/")[0]
        link = f"https://www.amazon.ca/dp/{asin}?tag={AFFILIATE_TAG}"
        deals.append({"title": title_el.text.strip(), "price": f"{price:.2f}", "link": link})
    return deals

# ─── Main ────────────────────────────────────────────────────────────────────
def main():
    bot = Bot(BOT_TOKEN)
    new = 0
    for deal in scrape_deals():
        if is_new_deal(deal["link"]):
            new += 1
            text = (
                f"🔥 *PRICE ERROR ALERT!* 🔥\n\n"
                f"🛍️ *{deal['title']}*\n"
                f"💸 *Now:* ${deal['price']}\n\n"
                f"[Buy Now]({deal['link']})"
            )
            bot.send_message(chat_id=CHANNEL_ID, text=text,
                             parse_mode="Markdown", disable_web_page_preview=True)
    logger.info(f"Sent {new} new deal(s).")

# ─── DEBUG: one-time ping to verify delivery ─────────────────────────────
if os.getenv("DEBUG_PING", "false").lower() == "true":
    bot = Bot(BOT_TOKEN)
    bot.send_message(
        chat_id=CHANNEL_ID,
        text="✅ Debug ping: GitHub Actions successfully reached your Telegram channel!"
    )

if __name__=="__main__":
    main()
