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
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_json(path, data):
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)

def get_target(update: Update):
    """Return the Message object whether from a direct command or callback."""
    return update.message or update.callback_query.message

# ─── Category Mapping ──────────────────────────────────────────────────────────
CATEGORY_MAP = {
    "electronics": "/Electronics-Accessories/b/?ie=UTF8&node=667823011",
    # Add additional categories as needed
}
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept-Language": "en-CA"}

def make_url(path: str) -> str:
    suffix = '?sort=price-asc-rank' if '?' not in path else '&sort=price-asc-rank'
    return f"https://www.amazon.ca{path}{suffix}"

def get_category_urls(cat: str = None) -> list[str]:
    if cat:
        path = CATEGORY_MAP.get(cat.lower())
        return [make_url(path)] if path else []
    return [make_url(p) for p in CATEGORY_MAP.values()]

# ─── Scraping ───────────────────────────────────────────────────────────────────
def scrape_category(url: str, min_discount: int = 0) -> list[dict]:
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


def scrape_deals(cat: str = None, min_discount: int = 0) -> list[dict]:
    all_deals = []
    for url in get_category_urls(cat):
        logger.info(f"Scraping {url}")
        all_deals.extend(scrape_category(url, min_discount))
    return all_deals

# ─── Bot Handlers ──────────────────────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    target = get_target(update)
    await target.reply_text("Welcome! Use /menu to choose a command.")

async def menu_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("Help", callback_data="cmd:help"), InlineKeyboardButton("Search", callback_data="cmd:search")],
        [InlineKeyboardButton("Subscribe", callback_data="cmd:subscribe"), InlineKeyboardButton("Unsubscribe", callback_data="cmd:unsubscribe")],
        [InlineKeyboardButton("My Settings", callback_data="cmd:mysettings"), InlineKeyboardButton("Alert", callback_data="cmd:alert")],
        [InlineKeyboardButton("Scrape", callback_data="cmd:scrape")]
    ]
    markup = InlineKeyboardMarkup(kb)
    target = get_target(update)
    await target.reply_text("Please choose:", reply_markup=markup)
    if update.callback_query:
        await update.callback_query.answer()

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "/search <category> <min_discount> - immediate lookup\n"
        "/subscribe <category> <min_discount> - hourly alerts\n"
        "/unsubscribe <category> - stop alerts\n"
        "/mysettings - view your subscriptions\n"
        "/alert <url_or_asin> <min_drop>% - track a product\n"
        "/scrape - manual scrape (admin only)"
    )
    target = get_target(update)
    await target.reply_text(text)
    if update.callback_query:
        await update.callback_query.answer()

async def search_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    target = get_target(update)
    args = ctx.args
    if len(args) != 2:
        return await target.reply_text("Usage: /search <category> <min_discount>")
    cat, thresh = args
    try:
        min_d = int(thresh)
    except ValueError:
        return await target.reply_text("min_discount must be an integer.")
    deals = scrape_deals(cat, min_d)
    if not deals:
        return await target.reply_text("No deals found.")
    for d in deals[:5]:
        await target.reply_text(f"📢 {d['title']} - ${d['sale']} (was ${d['orig']}, {d['discount']}% off)\n{d['link']}")
    await target.reply_text("✅ Search complete.")

async def subscribe_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    target = get_target(update)
    args = ctx.args
    if len(args) != 2:
        return await target.reply_text("Usage: /subscribe <category> <min_discount>")
    cat, thresh = args
    try:
        min_d = int(thresh)
    except ValueError:
        return await target.reply_text("min_discount must be an integer.")
    subs = load_json(SUBS_FILE)
    uid = str(target.chat.id)
    subs.setdefault(uid, {})[cat.lower()] = min_d
    save_json(SUBS_FILE, subs)
    await target.reply_text(f"Subscribed to {cat} at {min_d}% discount alerts.")

async def unsubscribe_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    target = get_target(update)
    args = ctx.args
    if len(args) != 1:
        return await target.reply_text("Usage: /unsubscribe <category>")
    cat = args[0].lower()
    subs = load_json(SUBS_FILE)
    uid = str(target.chat.id)
    if uid in subs and cat in subs[uid]:
        del subs[uid][cat]
        save_json(SUBS_FILE, subs)
        return await target.reply_text(f"Unsubscribed from {cat}.")
    await target.reply_text(f"No subscription found for {cat}.")

async def mysettings_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    target = get_target(update)
    uid = str(target.chat.id)
    subs = load_json(SUBS_FILE).get(uid, {})
    if not subs:
        return await target.reply_text("You have no subscriptions.")
    text = "Your subscriptions:\n" + "\n".join(f"{c}: {d}%" for c,d in subs.items())
    await target.reply_text(text)

async def alert_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    target = get_target(update)
    args = ctx.args
    if len(args) != 2:
        return await target.reply_text("Usage: /alert <url_or_asin> <min_drop>")
    item, thresh = args
    try:
        min_d = int(thresh)
    except ValueError:
        return await target.reply_text("min_drop must be an integer.")
    alerts = load_json(ALERTS_FILE)
    uid = str(target.chat.id)
    alerts.setdefault(uid, {})[item] = min_d
    save_json(ALERTS_FILE, alerts)
    await target.reply_text(f"Price alert set on {item} for {min_d}% drop.")

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
            await target.reply_text(f"📢 {d['title']} - ${d['sale']} (was ${d['orig']}, {d['discount']}% off)\n{d['link']}")
            seen.append(d['link'])
    save_json(SEEN_FILE, {"links": seen})
    await target.reply_text(f"✅ Completed. {count} new deal(s).")

async def cmd_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cmd = update.callback_query.data.split(':',1)[1]
    mapping = {
        'help': help_cmd,
        'search': search_cmd,
        'subscribe': subscribe_cmd,
        'unsubscribe': unsubscribe_cmd,
        'mysettings': mysettings_cmd,
        'alert': alert_cmd,
        'scrape': scrape_manual
    }
    handler = mapping.get(cmd)
    if handler:
        await handler(update, ctx)
    else:
        message = get_target(update)
        await message.reply_text("Unknown command.")
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
            if len(item) == 10:
                deals = scrape_category(f"https://www.amazon.ca/dp/{item}?tag={AFFILIATE_TAG}", 0)
                if deals:
                    d = deals[0]
                    await context.bot.send_message(
                        chat_id=int(uid),
                        text=f"🔔 Price alert: {d['title']} now at ${d['sale']}\n{d['link']}"
                    )
    if DEBUG_PING:
        # Notify first user as a ping test
        first_uid = next(iter(alerts), None)
        if first_uid:
            await context.bot.send_message(chat_id=int(first_uid), text="✅ Debug alert job ran.")

# ─── Application Setup ─────────────────────────────────────────────────────────
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CommandHandler("subscribe", subscribe_cmd))
    app.add_handler(CommandHandler("unsubscribe", unsubscribe_cmd))
    app.add_handler(CommandHandler("mysettings", mysettings_cmd))
    app.add_handler(CommandHandler("alert", alert_cmd))
    app.add_handler(CommandHandler("scrape", scrape_manual))
    # Inline menu callback
    app.add_handler(CallbackQueryHandler(cmd_router, pattern="^cmd:"))

    # Background jobs every hour
    jq = app.job_queue
    jq.run_repeating(job_subscriptions, interval=3600, first=10)
    jq.run_repeating(job_alerts, interval=3600, first=20)

    app.run_polling()

if __name__ == "__main__":
    main()
