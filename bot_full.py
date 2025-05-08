import os
import json
import logging
import asyncio
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot, ForceReply
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters,
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
def load_json(path: str) -> dict:
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_json(path: str, data: dict) -> None:
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)

def get_target(update: Update):
    return update.message or (update.callback_query and update.callback_query.message)

# ─── Category Mapping ──────────────────────────────────────────────────────────
CATEGORY_MAP = {
    "electronics": "/Electronics-Accessories/b/?ie=UTF8&node=667823011",
    # Add other categories...
}
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept-Language": "en-CA"}

def make_url(path: str) -> str:
    suffix = '?sort=price-asc-rank' if '?' not in path else '&sort=price-asc-rank'
    return f"https://www.amazon.ca{path}{suffix}"

def get_category_urls(cat: str = None) -> list:
    if cat:
        path = CATEGORY_MAP.get(cat.lower())
        return [make_url(path)] if path else []
    return [make_url(p) for p in CATEGORY_MAP.values()]

# ─── Scraping ───────────────────────────────────────────────────────────────────
def scrape_category(url: str, min_discount: int = 0) -> list:
    resp = requests.get(url, headers=HEADERS, timeout=10)
    soup = BeautifulSoup(resp.text, "html.parser")
    items = soup.select('div[data-component-type="s-search-result"]')
    deals = []
    for it in items:
        title_el = it.select_one("h2 a span")
        sale_whole = it.select_one("span.a-price-whole")
        orig_el = it.select_one("span.a-price.a-text-price span.a-offscreen")
        if not (title_el and sale_whole and orig_el):
            continue
        try:
            sale = float(sale_whole.text.replace(',','') + ".00")
            orig = float(orig_el.text.strip().lstrip('$').replace(',',''))
        except ValueError:
            continue
        discount = int((orig - sale) / orig * 100)
        if discount < min_discount:
            continue
        href = it.select_one("h2 a[href]")["href"]
        asin = href.split("/dp/")[-1].split("/")[0]
        deals.append({
            "title":    title_el.text.strip(),
            "sale":     f"{sale:.2f}",
            "orig":     f"{orig:.2f}",
            "discount": discount,
            "link":     f"https://www.amazon.ca/dp/{asin}?tag={AFFILIATE_TAG}",
            "asin":     asin
        })
    return deals


def scrape_deals(cat: str = None, min_discount: int = 0) -> list:
    all_deals = []
    for url in get_category_urls(cat):
        logger.info(f"Scraping {url}")
        all_deals.extend(scrape_category(url, min_discount))
    return all_deals

# ─── Conversation states ───────────────────────────────────────────────────────
SEARCH, SUBSCRIBE, UNSUBSCRIBE, ALERT = range(4)

# ─── Inline-prompt Handlers ─────────────────────────────────────────────────────
async def search_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    target = get_target(update)
    await target.reply_text(
        "🔍 Enter category and min discount (e.g. electronics 20)",
        reply_markup=ForceReply(selective=True)
    )
    return SEARCH

async def search_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().split()
    if len(text) != 2 or not text[1].isdigit():
        await update.message.reply_text("Invalid format. Use: <category> <min_discount>")
        return SEARCH
    cat, min_d = text[0], int(text[1])
    deals = scrape_deals(cat, min_d)
    if not deals:
        await update.message.reply_text("No deals found.")
    else:
        for d in deals[:5]:
            await update.message.reply_text(
                f"📢 {d['title']} - ${d['sale']} (was ${d['orig']}, {d['discount']}% off)\n{d['link']}"
            )
    return ConversationHandler.END

async def subscribe_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    target = get_target(update)
    await target.reply_text(
        "🔔 Enter category and min discount to subscribe (e.g. electronics 15)",
        reply_markup=ForceReply(selective=True)
    )
    return SUBSCRIBE

async def subscribe_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().split()
    if len(text) != 2 or not text[1].isdigit():
        await update.message.reply_text("Invalid format. Use: <category> <min_discount>")
        return SUBSCRIBE
    cat, min_d = text[0].lower(), int(text[1])
    subs = load_json(SUBS_FILE)
    uid = str(update.message.chat.id)
    subs.setdefault(uid, {})[cat] = min_d
    save_json(SUBS_FILE, subs)
    await update.message.reply_text(f"Subscribed to {cat} at {min_d}% discount alerts.")
    return ConversationHandler.END

async def unsubscribe_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    target = get_target(update)
    await target.reply_text(
        "❌ Enter category to unsubscribe (e.g. electronics)",
        reply_markup=ForceReply(selective=True)
    )
    return UNSUBSCRIBE

async def unsubscribe_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cat = update.message.text.strip().lower()
    subs = load_json(SUBS_FILE)
    uid = str(update.message.chat.id)
    if uid in subs and cat in subs[uid]:
        del subs[uid][cat]
        save_json(SUBS_FILE, subs)
        await update.message.reply_text(f"Unsubscribed from {cat}.")
    else:
        await update.message.reply_text(f"No subscription found for {cat}.")
    return ConversationHandler.END

async def alert_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    target = get_target(update)
    await target.reply_text(
        "⚠️ Enter URL or ASIN and min drop percent (e.g. B00EXAMPLE 10)",
        reply_markup=ForceReply(selective=True)
    )
    return ALERT

async def alert_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().split()
    if len(text) != 2 or not text[1].isdigit():
        await update.message.reply_text("Invalid format. Use: <url_or_asin> <min_drop>")
        return ALERT
    item, min_d = text[0], int(text[1])
    alerts = load_json(ALERTS_FILE)
    uid = str(update.message.chat.id)
    alerts.setdefault(uid, {})[item] = min_d
    save_json(ALERTS_FILE, alerts)
    await update.message.reply_text(f"Price alert set on {item} for {min_d}% drop.")
    return ConversationHandler.END

# ─── Manual scrape & help ─────────────────────────────────────────────────────
async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    target = get_target(update)
    await target.reply_text(
        "/help - show commands\n"
        "/search - interactive search\n"
        "/subscribe - interactive subscription\n"
        "/unsubscribe - interactive unsubscribe\n"
        "/mysettings - view subscriptions\n"
        "/alert - interactive alert setup\n"
        "/scrape - manual scrape (admin only)"
    )

async def scrape_manual(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    target = get_target(update)
    user = target.from_user.username
    if ADMIN_USERNAME and user != ADMIN_USERNAME:
        return await target.reply_text("❌ Not authorized.")
    await target.reply_text("🔄 Manual scrape started...")
    deals = scrape_deals()
    seen = load_json(SEEN_FILE).get("links", [])
    count = 0
    for d in deals:
        if d['link'] not in seen:
            count += 1
            await target.reply_text(
                f"📢 {d['title']} - ${d['sale']} (was ${d['orig']}, {d['discount']}% off)\n{d['link']}"
            )
            seen.append(d['link'])
    save_json(SEEN_FILE, {"links": seen})
    await target.reply_text(f"✅ Completed. {count} new deal(s).")

async def mysettings_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    target = get_target(update)
    uid = str(target.chat.id)
    subs = load_json(SUBS_FILE).get(uid, {})
    if not subs:
        return await target.reply_text("You have no subscriptions.")
    text = "Your subscriptions:\n" + "\n".join(f"{c}: {d}%" for c,d in subs.items())
    await target.reply_text(text)

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
            if len(item) == 10:
                deals = scrape_category(f"https://www.amazon.ca/dp/{item}?tag={AFFILIATE_TAG}")
                if deals:
                    d = deals[0]
                    await context.bot.send_message(
                        chat_id=int(uid),
                        text=f"🔔 Price alert: {d['title']} now at ${d['sale']}\n{d['link']}"
                    )
    if DEBUG_PING and alerts:
        first_uid = next(iter(alerts))
        await context.bot.send_message(chat_id=int(first_uid), text="✅ Debug alert job ran.")

# ─── Application Setup ─────────────────────────────────────────────────────────
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Conversation handlers for inline buttons
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(search_start, pattern="^cmd:search$")],
        states={SEARCH: [MessageHandler(filters.FORCE_REPLY & ~filters.COMMAND, search_input)]},
        fallbacks=[]
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(subscribe_start, pattern="^cmd:subscribe$")],
        states={SUBSCRIBE: [MessageHandler(filters.FORCE_REPLY & ~filters.COMMAND, subscribe_input)]},
        fallbacks=[]
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(unsubscribe_start, pattern="^cmd:unsubscribe$")],
        states={UNSUBSCRIBE: [MessageHandler(filters.FORCE_REPLY & ~filters.COMMAND, unsubscribe_input)]},
        fallbacks=[]
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(alert_start, pattern="^cmd:alert$")],
        states={
