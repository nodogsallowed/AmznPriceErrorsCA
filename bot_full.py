"""
Interactive Telegram Amazon Price Bot with Inline Menu

Features:
 - /start, /menu
 - Inline menu for all commands: Help, Search, Subscribe, Unsubscribe, My Settings, Alert, Scrape
 - /help still available
 - /search <category> <min_discount>
 - /subscribe <category> <min_discount>
 - /unsubscribe <category>
 - /mysettings
 - /alert <amazon_url_or_asin> <min_drop_percent>
 - /scrape (admin), manual scrape
 - Inline 👍/👎 feedback on deals
 - Hourly background scraping for subscriptions and alerts
 - CamelCamelCamel price history via affiliate links
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
    # same as before
}
HEADERS = {"User-Agent":"Mozilla/5.0","Accept-Language":"en-CA"}

def make_url(path): return f"https://www.amazon.ca{path}?sort=price-asc-rank"
def get_category_urls(cat=None):
    if cat and cat in CATEGORY_MAP: return [make_url(CATEGORY_MAP[cat])]
    if cat: return []
    return [make_url(p) for p in CATEGORY_MAP.values()]

# Scraper functions same as before...
# is_new, subscription, alerts, feedback same as before...

# ─── Handlers ─────────────────────────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Welcome! Use the menu below to pick a command.")
    await menu_cmd(update, ctx)

async def menu_cmd(update, ctx):
    keyboard = [
        [InlineKeyboardButton("Help", callback_data="cmd:help"),
         InlineKeyboardButton("Search", callback_data="cmd:search")],
        [InlineKeyboardButton("Subscribe", callback_data="cmd:subscribe"),
         InlineKeyboardButton("Unsubscribe", callback_data="cmd:unsubscribe")],
        [InlineKeyboardButton("My Settings", callback_data="cmd:mysettings"),
         InlineKeyboardButton("Alert", callback_data="cmd:alert")],
        [InlineKeyboardButton("Scrape", callback_data="cmd:scrape")]
    ]
    await update.message.reply_text("Please choose an action:", reply_markup=InlineKeyboardMarkup(keyboard))

async def help_cmd(update, ctx):
    text = (
        "/search <cat> <min_discount> - immediate lookup\n"
        "/subscribe <cat> <min_discount> - hourly alerts\n"
        "/unsubscribe <cat>\n"
        "/mysettings - view subscriptions\n"
        "/alert <url_or_asin> <min_drop> - price-drop alert\n"
        "/scrape - (admin) manual scrape\n"
    )
    # works for both message and callback
    target = update.message or update.callback_query.message
    await target.reply_text(text)

# other command handlers unchanged...

async def cmd_router(update, ctx):
    q = update.callback_query
    cmd = q.data.split(":",1)[1]
    # map commands that need no args
    if cmd == "help":
        await help_cmd(update, ctx)
    elif cmd == "mysettings":
        # call mysettings logic
        subs = list_sub(q.message.chat.id)
        text = "You have no subscriptions." if not subs else "Your subscriptions:\n" + "\n".join(f"{c}: {d}%" for c,d in subs)
        await q.message.reply_text(text)
    elif cmd == "scrape":
        user=q.from_user.username
        if ADMIN_USERNAME and user!=ADMIN_USERNAME:
            return await q.message.reply_text("❌ Not authorized.")
        await q.message.reply_text("🔄 Manual scrape started...")
        deals=scrape_deals()
        count=0
        for d in deals:
            if is_new(d['link']):
                count+=1
                await q.message.reply_text(f"📢 {d['title']} - ${d['sale']} (was ${d['orig']}, {d['discount']}% off)\n{d['link']}")
        await q.message.reply_text(f"✅ Completed. {count} new deal(s).")
    else:
        # commands requiring args: instruct usage
        instr = {
            'search':"Usage: /search <category> <min_discount>",
            'subscribe':"Usage: /subscribe <category> <min_discount>",
            'unsubscribe':"Usage: /unsubscribe <category>",
            'alert':"Usage: /alert <url_or_asin> <min_drop>"
        }.get(cmd, "Unknown command.")
        await q.message.reply_text(instr)
    await q.answer()  # remove loading state

# ─── Background job and main setup as before, plus add:
#    CommandHandler("menu", menu_cmd)
#    CallbackQueryHandler(cmd_router, pattern="^cmd:")

# In main():
#    app.add_handler(CommandHandler("start", start))
#    app.add_handler(CommandHandler("menu", menu_cmd))
#    app.add_handler(CallbackQueryHandler(cmd_router, pattern="^cmd:"))
#    ... rest of handlers ...

# ensure scraping, jobs, and run_polling as before
