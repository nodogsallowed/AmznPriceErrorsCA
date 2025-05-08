"""
Interactive Telegram Amazon Price Bot

Features:
 - /start, /help
 - /search <category> <min_discount>
 - /subscribe <category> <min_discount>
 - /unsubscribe <category>
 - /mysettings
 - /alert <amazon_url_or_asin> <min_drop_percent>
 - /scrape (admin)
 - Inline 👍/👎 feedback
 - Hourly background scraping for subscriptions and alerts
 - Price history via CamelCamelCamel
 - Persistent storage: seen.json, subscriptions.json, alerts.json, feedback.json
"""
import os
import json
import logging
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    JobQueue
)
import datetime

# ─── Load .env ─────────────────────────────────────────────────────────────────
load_dotenv()
BOT_TOKEN      = os.getenv("TELEGRAM_BOT_TOKEN")
AFFILIATE_TAG  = os.getenv("AMZN_AFFILIATE_TAG", "amznerrorsca-20")
DEBUG_PING     = os.getenv("DEBUG_PING", "false").lower() == "true"
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME")

# ─── Files ─────────────────────────────────────────────────────────────────────
SEEN_FILE       = "seen.json"
SUBS_FILE       = "subscriptions.json"
ALERTS_FILE     = "alerts.json"
FEEDBACK_FILE   = "feedback.json"

# ─── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── Helpers ───────────────────────────────────────────────────────────────────
def load_json(path):
    try:
        return json.load(open(path, 'r'))
    except:
        return {}

def save_json(path, data):
    json.dump(data, open(path, 'w'), indent=2)

# ─── Category Mapping ──────────────────────────────────────────────────────────
CATEGORY_MAP = {
    "electronics": "/Electronics-Accessories/b/?ie=UTF8&node=667823011",
    "books":       "/Books-Used-Books-Textbooks/b/?ie=UTF8&node=916520",
    "beauty":      "/Beauty/b/?ie=UTF8&node=6205124011",
    "toys":        "/Toys-Games/b/?ie=UTF8&node=6205517011",
    "sports":      "/sporting-goods/b/?ie=UTF8&node=2242989011",
    "pc":          "/Computers-Accessories/b/?ie=UTF8&node=2404990011",
    "health":      "/Health-Personal-Care/b/?ie=UTF8&node=6205177011",
    "home":        "/Home-Improvement/b/?ie=UTF8&node=3006902011",
    "fashion":     "/Fashion/b/?ie=UTF8&node=21204935011",
    "videogames":  "/video-games-hardware-accessories/b/?ie=UTF8&node=3198031",
    "grocery":     "/grocery/b/?ie=UTF8&node=6967215011",
    "pets":        "/pet-supplies-dog-cat-food-bed-toy/b/?ie=UTF8&node=6205514011",
    "baby":        "/gp/browse.html?node=3561346011"
}

HEADERS = {"User-Agent":"Mozilla/5.0","Accept-Language":"en-CA"}

# ─── Build URLs ────────────────────────────────────────────────────────────────
def make_url(path):
    return f"https://www.amazon.ca{path}?sort=price-asc-rank"

def get_category_urls(category=None):
    if category and category in CATEGORY_MAP:
        return [make_url(CATEGORY_MAP[category])]
    if category:
        return []
    return [make_url(p) for p in CATEGORY_MAP.values()]

# ─── Scraper ───────────────────────────────────────────────────────────────────
def scrape_category(url, min_discount):
    resp = requests.get(url, headers=HEADERS, timeout=10)
    soup = BeautifulSoup(resp.text, 'html.parser')
    items = soup.select('div[data-component-type="s-search-result"]')
    deals = []
    for it in items:
        title_el   = it.select_one("h2 a span")
        sale_whole = it.select_one("span.a-price-whole")
        sale_frac  = it.select_one("span.a-price-fraction")
        orig_el    = it.select_one("span.a-price.a-text-price span.a-offscreen")
        if not (title_el and sale_whole and orig_el):
            continue
        sale_str = f"{sale_whole.text.strip()}.{sale_frac.text.strip()}"
        orig_str = orig_el.text.strip().lstrip('$')
        try:
            sale_price = float(sale_str.replace(',', ''))
            orig_price = float(orig_str.replace(',', ''))
        except:
            continue
        discount = (orig_price - sale_price) / orig_price * 100
        if discount < min_discount:
            continue
        asin = it.select_one("h2 a[href]")["href"].split("/dp/")[-1].split("/")[0]
        link = f"https://www.amazon.ca/dp/{asin}?tag={AFFILIATE_TAG}"
        deals.append({"title":title_el.text.strip(),
                      "sale":sale_price,
                      "orig":orig_price,
                      "discount":int(discount),
                      "link":link,
                      "asin":asin})
    return deals

# ─── Master scraper ──────────────────────────────────────────────────────────
def scrape_deals(category=None, min_discount=90):
    urls = get_category_urls(category)
    all_deals = []
    for url in urls:
        logger.info(f"GET {url} → {requests.get(url, headers=HEADERS).status_code}")
        all_deals += scrape_category(url, min_discount)
    return all_deals

# ─── Seen filter ───────────────────────────────────────────────────────────────
def is_new(link):
    seen = load_json(SEEN_FILE).get("links", [])
    if link in seen:
        return False
    seen.append(link)
    save_json(SEEN_FILE, {"links":seen})
    return True

# ─── Subscriptions ────────────────────────────────────────────────────────────
def add_sub(chat_id, cat, disc):
    subs = load_json(SUBS_FILE)
    user = str(chat_id)
    subs.setdefault(user, [])
    if any(s[0]==cat for s in subs[user]):
        return False
    subs[user].append((cat, disc))
    save_json(SUBS_FILE, subs)
    return True

def remove_sub(chat_id, cat):
    subs = load_json(SUBS_FILE)
    user = str(chat_id)
    old = subs.get(user, [])
    subs[user] = [s for s in old if s[0]!=cat]
    save_json(SUBS_FILE, subs)
    return len(old)!=len(subs[user])

def list_sub(chat_id):
    return load_json(SUBS_FILE).get(str(chat_id), [])

# ─── Alerts ───────────────────────────────────────────────────────────────────
def add_alert(chat_id, asin, disc):
    alerts = load_json(ALERTS_FILE)
    user = str(chat_id)
    alerts.setdefault(user, [])
    if any(a[0]==asin for a in alerts[user]):
        return False
    alerts[user].append((asin, disc))
    save_json(ALERTS_FILE, alerts)
    return True

# ─── Feedback ─────────────────────────────────────────────────────────────────
def save_feedback(chat_id, link, fb):
    fbdata = load_json(FEEDBACK_FILE)
    fbdata.setdefault(str(chat_id), {})[link] = fb
    save_json(FEEDBACK_FILE, fbdata)

# ─── Handlers ─────────────────────────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome! Use /help to see commands." )

async def help_cmd(update, ctx):
    text = (
        "/search <cat> <min_discount> - immediate lookup\n"
        "/subscribe <cat> <min_discount> - hourly alerts\n"
        "/unsubscribe <cat>\n"
        "/mysettings - view subscriptions\n"
        "/alert <url_or_asin> <min_drop> - price-drop alert\n"
        "/scrape - (admin) force background scrape\n"
    )
    await update.message.reply_text(text)

async def search_cmd(update, ctx):
    args = ctx.args
    if len(args)!=2:
        return await update.message.reply_text("Usage: /search <category> <min_discount>")
    cat, disc = args[0].lower(), args[1]
    try:
        disc = int(disc)
    except:
        return await update.message.reply_text("Discount must be a number")
    deals = scrape_deals(cat if cat!="all" else None, disc)
    if not deals:
        return await update.message.reply_text("No deals found.")
    for d in deals[:5]:
        await update.message.reply_text(
            f"*{d['title']}*\n${d['sale']}(was ${d['orig']}, {d['discount']}% off)\n{d['link']}",
            parse_mode="Markdown"
        )

async def subscribe(update, ctx):
    args = ctx.args
    if len(args)!=2:
        return await update.message.reply_text("Usage: /subscribe <category> <min_discount>")
    cat, disc = args[0].lower(), args[1]
    try:
        disc = int(disc)
    except:
        return await update.message.reply_text("Discount must be a number")
    if cat not in CATEGORY_MAP:
        return await update.message.reply_text("Unknown category.")
    ok = add_sub(update.effective_chat.id, cat, disc)
    await update.message.reply_text(
        "Subscribed!" if ok else "Already subscribed to that category." 
    )

async def unsubscribe(update, ctx):
    if len(ctx.args)!=1:
        return await update.message.reply_text("Usage: /unsubscribe <category>")
    cat = ctx.args[0].lower()
    ok = remove_sub(update.effective_chat.id, cat)
    await update.message.reply_text(
        "Unsubscribed." if ok else "You weren't subscribed to that." 
    )

async def mysettings(update, ctx):
    subs = list_sub(update.effective_chat.id)
    if not subs:
        return await update.message.reply_text("You have no subscriptions.")
    text = "Your subscriptions:\n" + "\n".join(f"{c}: {d}%" for c,d in subs)
    await update.message.reply_text(text)

async def alert_cmd(update, ctx):
    if len(ctx.args)!=2:
        return await update.message.reply_text("Usage: /alert <url_or_asin> <min_drop>")
    raw, disc = ctx.args
    try:
        disc = int(disc)
    except:
        return await update.message.reply_text("Min drop must be a number")
    asin = raw.split('/dp/')[-1].split('/')[0]
    ok = add_alert(update.effective_chat.id, asin, disc)
    await update.message.reply_text(
        "Alert set!" if ok else "Alert already exists." 
    )

async def feedback_cb(update, ctx):
    q = update.callback_query
    link, fb = q.data.split(':')[1:]  # ['fb','link','up']
    save_feedback(q.message.chat.id, link, fb)
    await q.answer("Thanks for feedback!")

# ─── Background Jobs ─────────────────────────────────────────────────────────
async def hourly_jobs(ctx: ContextTypes.DEFAULT_TYPE):
    subs = load_json(SUBS_FILE)
    for user, lst in subs.items():
        for cat, disc in lst:
            deals = scrape_deals(cat, disc)
            for d in deals:
                if is_new(d['link']):
                    await ctx.bot.send_message(
                        chat_id=int(user),
                        text=f"📢 {d['title']} - ${d['sale']} (was ${d['orig']}, {d['discount']}% off)\n{d['link']}"
                    )

# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CommandHandler("subscribe", subscribe))
    app.add_handler(CommandHandler("unsubscribe", unsubscribe))
    app.add_handler(CommandHandler("mysettings", mysettings))
    app.add_handler(CommandHandler("alert", alert_cmd))
    app.add_handler(CallbackQueryHandler(feedback_cb, pattern="^fb:"))

    jq: JobQueue = app.job_queue
    jq.run_repeating(hourly_jobs, interval=3600, first=10)

    logger.info("▶️ Bot starting…")
    app.run_polling()

if __name__ == '__main__':
    main()
