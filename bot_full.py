import os
import json
import logging
import asyncio
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    JobQueue
)

# ─── Load .env ─────────────────────────────────────────────────────────────────
load_dotenv()
BOT_TOKEN      = os.getenv("TELEGRAM_BOT_TOKEN")
AFFILIATE_TAG  = os.getenv("AMZN_AFFILIATE_TAG", "amznerrorsca-20")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME")
DEBUG_PING     = os.getenv("DEBUG_PING", "false").lower() == "true"

# ─── Files ─────────────────────────────────────────────────────────────────────
DATA_DIR       = os.getenv("DATA_DIR", ".")
SEEN_FILE      = os.path.join(DATA_DIR, "seen.json")
SUBS_FILE      = os.path.join(DATA_DIR, "subscriptions.json")
ALERTS_FILE    = os.path.join(DATA_DIR, "alerts.json")
FEEDBACK_FILE  = os.path.join(DATA_DIR, "feedback.json")

# ─── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── Helpers ───────────────────────────────────────────────────────────────────
def load_json(path):
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        logger.warning(f"Corrupted JSON at {path}, resetting.")
        return {}

def save_json(path, data):
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)

# ─── Category Mapping (example) ─────────────────────────────────────────────────
CATEGORY_MAP = {
    "electronics": "/Electronics-Accessories/b/?ie=UTF8&node=667823011",
    # ... fill in your categories here
}
HEADERS = {"User-Agent":"Mozilla/5.0","Accept-Language":"en-CA"}

def make_url(path):
    suffix = '?sort=price-asc-rank' if '?' not in path else '&sort=price-asc-rank'
    return f"https://www.amazon.ca{path}{suffix}"

# Return URLs for either a single category or all
def get_category_urls(cat=None):
    if cat:
        path = CATEGORY_MAP.get(cat.lower())
        return [make_url(path)] if path else []
    return [make_url(p) for p in CATEGORY_MAP.values()]

# Scrape a single category page for deals
def scrape_category(url, min_discount=0):
    resp = requests.get(url, headers=HEADERS, timeout=10)
    soup = BeautifulSoup(resp.text, "html.parser")
    items = soup.select('div[data-component-type="s-search-result"]')
    deals = []
    for it in items:
        title_el   = it.select_one("h2 a span")
        sale_whole = it.select_one("span.a-price-whole")
        orig_el    = it.select_one("span.a-price.a-text-price span.a-offscreen")
        if not (title_el and sale_whole and orig_el):
            continue
        sale = float(sale_whole.text.replace(',','') + ".00")
        orig = float(orig_el.text.strip().lstrip('$').replace(',',''))
        discount = int((orig - sale) / orig * 100)
        if discount < min_discount:
            continue
        link = it.select_one("h2 a[href]")["href"]
        asin = link.split("/dp/")[-1].split("/")[0]
        deals.append({
            "title": title_el.text.strip(),
            "sale": f"{sale:.2f}",
            "orig": f"{orig:.2f}",
            "discount": discount,
            "link": f"https://www.amazon.ca/dp/{asin}?tag={AFFILIATE_TAG}",
            "asin": asin
        })
    return deals

# Master scrape across categories
def scrape_deals(cat=None, min_discount=0):
    all_deals = []
    for url in get_category_urls(cat):
        logger.info(f"Scraping {url}")
        all_deals.extend(scrape_category(url, min_discount))
    return all_deals

# ─── Bot Handlers ──────────────────────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Welcome! Use /menu to choose a command.")

async def menu_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton(t, callback_data=f"cmd:{c}") for t,c in [
        ("Help","help"),("Search","search"),("Subscribe","subscribe"),("Unsubscribe","unsubscribe")]]]
    kb += [[InlineKeyboardButton(t, callback_data=f"cmd:{c}") for t,c in [
        ("My Settings","mysettings"),("Alert","alert"),("Scrape","scrape")]]]
    markup = InlineKeyboardMarkup(kb)
    target = update.message or update.callback_query.message
    await target.reply_text("Please choose:", reply_markup=markup)
    if update.callback_query:
        await update.callback_query.answer()

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "/search <category> <min_discount> - lookup deals\n"
        "/subscribe <category> <min_discount> - hourly alerts\n"
        "/unsubscribe <category> - stop alerts\n"
        "/mysettings - view your subscriptions and alerts\n"
        "/alert <url_or_asin> <min_drop>% - track single product\n"
        "/scrape - manual scrape (admin only)"
    )
    target = update.message or update.callback_query.message
    await target.reply_text(text)
    if update.callback_query:
        await update.callback_query.answer()

async def search_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if len(args) != 2:
        return await update.message.reply_text("Usage: /search <category> <min_discount>")
    cat, thresh = args[0], args[1]
    try:
        min_d = int(thresh)
    except ValueError:
        return await update.message.reply_text("min_discount must be an integer.")
    deals = scrape_deals(cat, min_d)
    if not deals:
        return await update.message.reply_text("No deals found.")
    for d in deals[:5]:
        await update.message.reply_text(
            f"📢 {d['title']} - ${d['sale']} (was ${d['orig']}, {d['discount']}% off)\n{d['link']}"
        )
    await update.message.reply_text("✅ Search complete.")

async def subscribe_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if len(args) != 2:
        return await update.message.reply_text("Usage: /subscribe <category> <min_discount>")
    cat, thresh = args[0].lower(), args[1]
    try:
        min_d = int(thresh)
    except ValueError:
        return await update.message.reply_text("min_discount must be an integer.")
    subs = load_json(SUBS_FILE)
    uid = str(update.message.chat_id)
    subs.setdefault(uid, {})[cat] = min_d
    save_json(SUBS_FILE, subs)
    await update.message.reply_text(f"Subscribed to {cat} at {min_d}% discount alerts.")

async def unsubscribe_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if len(args) != 1:
        return await update.message.reply_text("Usage: /unsubscribe <category>")
    cat = args[0].lower()
    subs = load_json(SUBS_FILE)
    uid = str(update.message.chat_id)
    if uid in subs and cat in subs[uid]:
        del subs[uid][cat]
        save_json(SUBS_FILE, subs)
        return await update.message.reply_text(f"Unsubscribed from {cat}.")
    await update.message.reply_text(f"No subscription found for {cat}.")

async def mysettings_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(update.message.chat_id)
    subs = load_json(SUBS_FILE).get(uid, {})
    text = "Your subscriptions:\n" + "\n".join(f"{c}: {d}%" for c,d in subs.items()) if subs else "You have no subscriptions."
    await update.message.reply_text(text)

async def alert_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if len(args) != 2:
        return await update.message.reply_text("Usage: /alert <url_or_asin> <min_drop>")
    item, thresh = args[0], args[1]
    try:
        min_d = int(thresh)
    except ValueError:
        return await update.message.reply_text("min_drop must be an integer.")
    alerts = load_json(ALERTS_FILE)
    uid = str(update.message.chat_id)
    alerts.setdefault(uid, {})[item] = min_d
    save_json(ALERTS_FILE, alerts)
    await update.message.reply_text(f"Price alert set on {item} for {min_d}% drop.")

async def scrape_manual(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user.username
    if ADMIN_USERNAME and user != ADMIN_USERNAME:
        return await update.message.reply_text("❌ Not authorized.")
    await update.message.reply_text("🔄 Manual scrape started...")
    deals = scrape_deals()
    count = 0
    for d in deals:
        # Use seen to avoid duplicates
        seen = load_json(SEEN_FILE).get("links", [])
        if d['link'] not in seen:
            count += 1
            await update.message.reply_text(
                f"📢 {d['title']} - ${d['sale']} (was ${d['orig']}, {d['discount']}% off)\n{d['link']}"
            )
            seen.append(d['link'])
        save_json(SEEN_FILE, {"links": seen})
    await update.message.reply_text(f"✅ Completed. {count} new deal(s).")

# Dispatcher callback for inline menu
async def cmd_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data.split(':',1)[1]
    mapping = {
        'help': help_cmd,
        'search': search_cmd,
        'subscribe': subscribe_cmd,
        'unsubscribe': unsubscribe_cmd,
        'mysettings': mysettings_cmd,
        'alert': alert_cmd,
        'scrape': scrape_manual
    }
    if data in mapping:
        await mapping[data](update, ctx)
    else:
        await update.callback_query.message.reply_text("Unknown command.")

    await update.callback_query.answer()

# ─── Background Jobs ───────────────────────────────────────────────────────────
async def job_subscriptions(context: ContextTypes.DEFAULT_TYPE):
    subs = load_json(SUBS_FILE)
    for uid, cats in subs.items():
        for cat, min_d in cats.items():
            deals = scrape_deals(cat, min_d)
            for d in deals:
                await context.bot.send_message(
                    chat_id=int(uid),
                    text=f"🔔 Subscription alert: {d['title']} - ${d['sale']} ({d['discount']}% off)\n{d['link']}"
                )

async def job_alerts(context: ContextTypes.DEFAULT_TYPE):
    alerts = load_json(ALERTS_FILE)
    for uid, items in alerts.items():
        for item, min_d in items.items():
            # Basic check: re-scrape search for ASIN
            deals = scrape_category(f"/dp/{item}", 0) if len(item) == 10 else []
            # TODO: implement actual price-drop logic using history
            if deals:
                d = deals[0]
                # Placeholder: always notify
                await context.bot.send_message(
                    chat_id=int(uid),
                    text=f"🔔 Price alert: {d['title']} now at ${d['sale']}\
