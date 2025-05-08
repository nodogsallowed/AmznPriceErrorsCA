import os
import json
import logging
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ForceReply
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

# ─── File paths ─────────────────────────────────────────────────────────────────
DATA_DIR       = os.getenv("DATA_DIR", ".")
SEEN_FILE      = os.path.join(DATA_DIR, "seen.json")
SUBS_FILE      = os.path.join(DATA_DIR, "subscriptions.json")
ALERTS_FILE    = os.path.join(DATA_DIR, "alerts.json")

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

# ─── Category mapping ──────────────────────────────────────────────────────────
CATEGORY_MAP = {
    "electronics": "/Electronics-Accessories/b/?ie=UTF8&node=667823011",
    # Extend with more categories...
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

# ─── Scraping functions ────────────────────────────────────────────────────────

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
            sale = float(sale_whole.text.replace(',', '') + ".00")
            orig = float(orig_el.text.strip().lstrip('$').replace(',', ''))
        except ValueError:
            continue
        discount = int((orig - sale) / orig * 100)
        if discount < min_discount:
            continue
        href = it.select_one("h2 a[href]")["href"]
        asin = href.split("/dp/")[-1].split("/")[0]
        deals.append({
            "title": title_el.text.strip(),
            "sale": f"{sale:.2f}",
            "orig": f"{orig:.2f}",
            "discount": discount,
            "link": f"https://www.amazon.ca/dp/{asin}?tag={AFFILIATE_TAG}",
            "asin": asin
        })
    return deals


def scrape_deals(cat: str = None, min_discount: int = 0) -> list:
    results = []
    for url in get_category_urls(cat):
        logger.info(f"Scraping {url}")
        results.extend(scrape_category(url, min_discount))
    return results

# ─── Conversation states ───────────────────────────────────────────────────────
SEARCH, SUBSCRIBE, UNSUBSCRIBE, ALERT = range(4)

# ─── Menu command ──────────────────────────────────────────────────────────────
async def menu_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tgt = get_target(update)
    kb = [
        [InlineKeyboardButton("Search", callback_data="cmd:search"), InlineKeyboardButton("Subscribe", callback_data="cmd:subscribe")],
        [InlineKeyboardButton("Unsubscribe", callback_data="cmd:unsubscribe"), InlineKeyboardButton("Alert", callback_data="cmd:alert")],
        [InlineKeyboardButton("My Settings", callback_data="cmd:mysettings"), InlineKeyboardButton("Scrape", callback_data="cmd:scrape")],
        [InlineKeyboardButton("Help", callback_data="cmd:help")]
    ]
    markup = InlineKeyboardMarkup(kb)
    await tgt.reply_text("Please choose an action:", reply_markup=markup)
    if update.callback_query:
        await update.callback_query.answer()

# ─── Inline flows ─────────────────────────────────────────────────────────────
async def search_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    tgt = get_target(update)
    await tgt.reply_text("🔍 Reply with: <category> <min_discount>", reply_markup=ForceReply(selective=True))
    return SEARCH

async def search_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    parts = update.message.text.strip().split()
    if len(parts) != 2 or not parts[1].isdigit():
        await update.message.reply_text("Invalid format. Use: <category> <min_discount>")
        return SEARCH
    cat, min_d = parts[0], int(parts[1])
    deals = scrape_deals(cat, min_d)
    if not deals:
        await update.message.reply_text("No deals found.")
    else:
        for d in deals[:5]:
            await update.message.reply_text(f"📢 {d['title']} — ${d['sale']} ({d['discount']}% off)\n{d['link']}")
    return ConversationHandler.END

async def subscribe_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    tgt = get_target(update)
    await tgt.reply_text("🔔 Reply with: <category> <min_discount> to subscribe", reply_markup=ForceReply(selective=True))
    return SUBSCRIBE

async def subscribe_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    parts = update.message.text.strip().split()
    if len(parts) != 2 or not parts[1].isdigit():
        await update.message.reply_text("Invalid format. Use: <category> <min_discount>")
        return SUBSCRIBE
    cat, min_d = parts[0].lower(), int(parts[1])
    data = load_json(SUBS_FILE)
    uid = str(update.message.chat.id)
    data.setdefault(uid, {})[cat] = min_d
    save_json(SUBS_FILE, data)
    await update.message.reply_text(f"Subscribed: {cat} @ {min_d}%")
    return ConversationHandler.END

async def unsubscribe_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    tgt = get_target(update)
    await tgt.reply_text("❌ Reply with category to unsubscribe", reply_markup=ForceReply(selective=True))
    return UNSUBSCRIBE

async def unsubscribe_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cat = update.message.text.strip().lower()
    data = load_json(SUBS_FILE)
    uid = str(update.message.chat.id)
    if uid in data and cat in data[uid]:
        del data[uid][cat]
        save_json(SUBS_FILE, data)
        await update.message.reply_text(f"Unsubscribed: {cat}")
    else:
        await update.message.reply_text(f"No subscription found: {cat}")
    return ConversationHandler.END

async def alert_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    tgt = get_target(update)
    await tgt.reply_text("⚠️ Reply with: <URL_or_ASIN> <min_drop>", reply_markup=ForceReply(selective=True))
    return ALERT

async def alert_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    parts = update.message.text.strip().split()
    if len(parts) != 2 or not parts[1].isdigit():
        await update.message.reply_text("Invalid format. Use: <URL_or_ASIN> <min_drop>")
        return ALERT
    item, min_d = parts[0], int(parts[1])
    data = load_json(ALERTS_FILE)
    uid = str(update.message.chat.id)
    data.setdefault(uid, {})[item] = min_d
    save_json(ALERTS_FILE, data)
    await update.message.reply_text(f"Alert set on {item} @ {min_d}% drop")
    return ConversationHandler.END

# ─── Static callbacks ─────────────────────────────────────────────────────────
async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tgt = get_target(update)
    await tgt.reply_text(
        "/menu — show options\n"
        "/help — this message\n"
        "/mysettings — list subscriptions\n"
        "/scrape — manual scrape (admin only)"
    )

async def mysettings_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tgt = get_target(update)
    uid = str(tgt.chat.id)
    subs = load_json(SUBS_FILE).get(uid, {})
    if not subs:
        return await tgt.reply_text("No subscriptions.")
    lines = [f"{c}: {d}%" for c, d in subs.items()]
    await tgt.reply_text("Your subscriptions:\n" + "\n".join(lines))

async def scrape_manual(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tgt = get_target(update)
    user = tgt.from_user.username
    if ADMIN_USERNAME and user != ADMIN_USERNAME:
        return await tgt.reply_text("❌ Not authorized.")
    await tgt.reply_text("🔄 Scraping now...")
    deals = scrape_deals()
    seen = load_json(SEEN_FILE).get("links", [])
    count = 0
    for d in deals:
        if d['link'] not in seen:
            count += 1
            await tgt.reply_text(f"📢 {d['title']} — ${d['sale']} ({d['discount']}% off)\n{d['link']}")
            seen.append(d['link'])
    save_json(SEEN_FILE, {"links": seen})
    await tgt.reply_text(f"✅ Done: {count} new deals.")

# ─── Background jobs ──────────────────────────────────────────────────────────
async def job_subscriptions(context: ContextTypes.DEFAULT_TYPE):
    data = load_json(SUBS_FILE)
    for uid, cats in data.items():
        for cat, min_d in cats.items():
            deals = scrape_deals(cat, min_d)
            for d in deals:
                await context.bot.send_message(
                    chat_id=int(uid), text=f"🔔 {d['title']} — ${d['sale']} ({d['discount']}% off)\n{d['link']}"
                )

async def job_alerts(context: ContextTypes.DEFAULT_TYPE):
    data = load_json(ALERTS_FILE)
    for uid, items in data.items():
        for item, min_d in items.items():
            if len(item) == 10:
                deals = scrape_category(f"https://www.amazon.ca/dp/{item}?tag={AFFILIATE_TAG}")
                if deals:
                    d = deals[0]
                    await context.bot.send_message(chat_id=int(uid), text=f"🔔 {d['title']} now at ${d['sale']}\n{d['link']}"
                    )
    if DEBUG_PING and data:
        first = next(iter(data))
        await context.bot.send_message(chat_id=int(first), text="✅ Alert job ran.")

# ─── Bot setup ─────────────────────────────────────────────────────────────────

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    jq: JobQueue = app.job_queue

    # Start/menu
    app.add_handler(CommandHandler("start", menu_cmd))
    app.add_handler(CommandHandler("menu", menu_cmd))

    # Static commands
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("mysettings", mysettings_cmd))
    app.add_handler(CommandHandler("scrape", scrape_manual))

    # Inline menu flows
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(search_start, pattern="^cmd:search$")],
        states={SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, search_input)]},
        fallbacks=[]
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(subscribe_start, pattern="^cmd:subscribe$")],
        states={SUBSCRIBE: [MessageHandler(filters.TEXT & ~filters.COMMAND, subscribe_input)]},
        fallbacks=[]
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(unsubscribe_start, pattern="^cmd:unsubscribe$")],
        states={UNSUBSCRIBE: [MessageHandler(filters.TEXT & ~filters.COMMAND, unsubscribe_input)]},
        fallbacks=[]
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(alert_start, pattern="^cmd:alert$")],
        states={ALERT: [MessageHandler(filters.TEXT & ~filters.COMMAND, alert_input)]},
        fallbacks=[]
    ))

    # Inline static callbacks
    app.add_handler(CallbackQueryHandler(help_cmd, pattern="^cmd:help$"))
    app.add_handler(CallbackQueryHandler(mysettings_cmd, pattern="^cmd:mysettings$"))
    app.add_handler(CallbackQueryHandler(scrape_manual, pattern="^cmd:scrape$"))

    # Scheduled jobs
    jq.run_repeating(job_subscriptions, interval=3600, first=10)
    jq.run_repeating(job_alerts, interval=3600, first=20)

    app.run_polling()

if __name__ == "__main__":
    main()
