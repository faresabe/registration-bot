"""
Ethiopian Church T-Shirt Registration Telegram Bot — WEBHOOK version
----------------------------------------------------------------------
Same bot as the polling version, adapted to run under Flask so it can be
hosted on PythonAnywhere's free tier (which only serves HTTP requests,
not long-running background loops like run_polling()).

Everything else — language selection, validation, admin approve/reject,
/status, /export — is unchanged.

Key difference from the polling version:
  - We build the python-telegram-bot Application as before, but instead
    of app.run_polling(), we keep ONE persistent asyncio event loop alive
    for the life of the process, and feed it each incoming Telegram
    update via that loop's run_until_complete(). This matters because
    PTB creates internal locks/queues tied to a specific event loop
    during initialize() — mixing event loops per request (e.g. calling
    asyncio.run() fresh each time) can break those internals.

Run locally for testing:
    python webhook_bot.py
  This starts a local Flask dev server on port 5000. For real use you
  still need a public HTTPS URL, which is what PythonAnywhere provides.

Deploy: see DEPLOY_PYTHONANYWHERE.md
"""

import asyncio
import csv
import io
import logging
import os
import re
import sqlite3
import threading
from datetime import datetime

from dotenv import load_dotenv
from flask import Flask, request
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
BANK_NAME = os.getenv("BANK_NAME", "Commercial Bank of Ethiopia")
ACCOUNT_NAME = os.getenv("ACCOUNT_NAME", "Church Name")
ACCOUNT_NUMBER = os.getenv("ACCOUNT_NUMBER", "1000000000000")
PRICE = os.getenv("PRICE", "500 ብር")
DB_PATH = os.path.join(os.path.dirname(__file__), "registrations.db")
DEFAULT_LANG = "am"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

LANG, NAME, PHONE, SIZE, SCREENSHOT = range(5)
SIZES = ["S", "M", "L", "XL", "XXL"]

# ---------------------------------------------------------------------------
# Text templates (unchanged from the polling version)
# ---------------------------------------------------------------------------
TEXTS = {
    "am": {
        "choose_lang": (
            "🙏 እንኳን ደህና መጡ ወደ የቲሸርት ምዝገባ ቦት!\n\n"
            "ይህ ቦት የሚከተሉትን ደረጃዎች በመከተል ይመዘግብዎታል፦\n"
            "1️⃣ ስምዎን እና ስልክ ቁጥርዎን ይላካሉ\n"
            "2️⃣ የቲሸርት መጠንዎን ይመርጣሉ\n"
            "3️⃣ ክፍያ ይፈጽማሉ እና ስክሪንሾት ይልካሉ\n"
            "4️⃣ ክፍያዎ ከተረጋገጠ በኋላ በይፋ ይመዘገባሉ ✅\n\n"
            "ቋንቋ ይምረጡ / Choose your language 👇"
        ),
        "welcome": "🎉 እናመሰግናለን! በጥቂት ደቂቃዎች ውስጥ እንመዘግብዎታለን። 😊\n\nእባክዎ ሙሉ ስምዎን ያስገቡ 📝",
        "ask_phone": "በጣም አመሰግናለሁ፣ {name}! 🙌\nአሁን የስልክ ቁጥርዎን ያስገቡ 📱",
        "invalid_name": (
            "⚠️ ይህ ትክክለኛ ስም አይመስልም። እባክዎ ቁጥር ወይም ምልክት ሳይኖረው ሙሉ ስምዎን ብቻ ያስገቡ "
            "(ለምሳሌ: አበበ ከበደ) 📝"
        ),
        "invalid_phone": (
            "⚠️ ትክክለኛ የኢትዮጵያ ስልክ ቁጥር አይደለም።\n"
            "እባክዎ በ 09 ወይም 07 የሚጀምር 10 አሃዝ ቁጥር ያስገቡ (ለምሳሌ: 0912345678)፣\n"
            "ወይም በ +251 የሚጀምር ይላኩ (ለምሳሌ: +251912345678) 📱"
        ),
        "ask_size": "እሺ! 👕 የቲሸርት መጠንዎ ስንት ነው?",
        "invalid_size": "እባክዎ ከሚከተሉት ውስጥ አንዱን ይምረጡ፦ {sizes}",
        "payment_info": (
            "🙏 አመሰግናለሁ! የመጨረሻው እርምጃ ክፍያ ነው።\n\n"
            "💰 ዋጋ: *{price}*\n"
            "🏦 ባንክ: {bank}\n"
            "👤 የባለቤት ስም: {acc_name}\n"
            "🔢 የአካውንት ቁጥር: `{acc_no}`\n\n"
            "ክፍያውን ከፈጸሙ በኋላ፣ የክፍያ ማረጋገጫ *ስክሪንሾት* 📸 እዚህ ይላኩልን፣ እናረጋግጣለን!"
        ),
        "ask_photo_again": "እባክዎ የክፍያ ስክሪንሾት እንደ ፎቶ ይላኩ (እንደ ፋይል/ዶክመንት አይላኩ) 📸",
        "waitlisted": (
            "✅ ደርሶናል! እናመሰግናለን።\n\n"
            "እርስዎ አሁን በተጠባባቂ ዝርዝር (waitlist) ላይ ነዎት 📋\n"
            "ክፍያዎን በቅርቡ አረጋግጠን ምዝገባዎን እናጠናቅቃለን። ትንሽ ብቻ ይታገሱን! 🙏😊"
        ),
        "approved": (
            "🎉🎊 እንኳን ደስ አለዎት! በይፋ ተመዝግበዋል! ✅\n\n"
            "👤 ስም: {name}\n"
            "👕 መጠን: {size}\n\n"
            "እናመሰግናለን፣ በዝግጅቱ ላይ እንገናኝ! 🙌"
        ),
        "rejected": "⚠️ ይቅርታ፣ የክፍያ ማረጋገጫዎን ማረጋገጥ አልቻልንም።\n\nእባክዎ /start ብለው እንደገና ይሞክሩ፣ ወይም በቀጥታ ያግኙን። 🙏",
        "cancelled": "ምዝገባው ተሰርዟል። እንደገና ለመጀመር /start ይጫኑ። 👋",
        "no_registration": "እስካሁን ምዝገባ አልጀመሩም። /start ብለው ይጀምሩ። 😊",
        "restart_notice": "🔄 ምዝገባዎን እንደገና እየጀመርን ነው።\n\n",
        "status_map": {
            "in_progress": "🕐 ዝርዝርዎን በመሙላት ላይ",
            "awaiting_payment": "💳 የክፍያ ስክሪንሾት በመጠበቅ ላይ",
            "pending_review": "📋 በተጠባባቂ ዝርዝር ላይ (admin እያረጋገጠ ነው)",
            "approved": "✅ ተመዝግበዋል!",
            "rejected": "❌ ውድቅ ተደርጓል — እባክዎ /start ብለው እንደገና ይሞክሩ",
        },
        "status_prefix": "📌 ሁኔታዎ: {status}",
    },
    "en": {
        "choose_lang": (
            "🙏 Welcome to our church T-shirt registration bot!\n\n"
            "Here's how it works:\n"
            "1️⃣ Share your name and phone number\n"
            "2️⃣ Pick your T-shirt size\n"
            "3️⃣ Make the payment and send a screenshot\n"
            "4️⃣ Once verified, you're officially registered ✅\n\n"
            "Choose your language / ቋንቋ ይምረጡ 👇"
        ),
        "welcome": "🎉 Thanks! This'll only take a minute. 😊\n\nWhat's your full name? 📝",
        "ask_phone": "Thanks, {name}! 🙌\nWhat's your phone number? 📱",
        "invalid_name": (
            "⚠️ That doesn't look like a valid name. Please enter your full "
            "name using letters only, no numbers or symbols (e.g. Abebe Kebede) 📝"
        ),
        "invalid_phone": (
            "⚠️ That's not a valid Ethiopian phone number.\n"
            "Please enter a 10-digit number starting with 09 or 07 (e.g. 0912345678),\n"
            "or one starting with +251 (e.g. +251912345678) 📱"
        ),
        "ask_size": "Awesome! 👕 What size do you need?",
        "invalid_size": "Please pick one of: {sizes}",
        "payment_info": (
            "🙏 Thank you! Last step — payment.\n\n"
            "💰 Price: *{price}*\n"
            "🏦 Bank: {bank}\n"
            "👤 Account name: {acc_name}\n"
            "🔢 Account number: `{acc_no}`\n\n"
            "Once you've paid, send a *screenshot* 📸 of the confirmation here "
            "and we'll verify it!"
        ),
        "ask_photo_again": "Please send the payment screenshot as a photo (not a file/document) 📸",
        "waitlisted": (
            "✅ Got it, thank you!\n\n"
            "You're now on the *waitlist* 📋\n"
            "We'll verify your payment and confirm your registration soon. Thanks for your patience! 🙏😊"
        ),
        "approved": (
            "🎉🎊 Congratulations! You're officially registered! ✅\n\n"
            "👤 Name: {name}\n"
            "👕 Size: {size}\n\n"
            "Thank you, see you there! 🙌"
        ),
        "rejected": "⚠️ Sorry, we couldn't verify your payment screenshot.\n\nPlease send /start to try again, or contact us directly. 🙏",
        "cancelled": "Registration cancelled. Send /start to begin again. 👋",
        "no_registration": "You haven't started registration yet. Send /start. 😊",
        "restart_notice": "🔄 Restarting your registration.\n\n",
        "status_map": {
            "in_progress": "🕐 Filling in your details",
            "awaiting_payment": "💳 Waiting for your payment screenshot",
            "pending_review": "📋 On the waitlist (admin is reviewing)",
            "approved": "✅ Registered!",
            "rejected": "❌ Rejected — please /start again",
        },
        "status_prefix": "📌 Status: {status}",
    },
}


def t(lang: str, key: str, **kwargs) -> str:
    template = TEXTS.get(lang, TEXTS[DEFAULT_LANG])[key]
    return template.format(**kwargs) if kwargs else template


# ---------------------------------------------------------------------------
# Validation (unchanged)
# ---------------------------------------------------------------------------
NAME_RE = re.compile(r"^[A-Za-z\u1200-\u137F][A-Za-z\u1200-\u137F\s.'-]{1,79}$")
PHONE_RE = re.compile(r"^(?:\+?251|0)(7|9)\d{8}$")


def is_valid_name(name: str) -> bool:
    name = name.strip()
    if not name or any(ch.isdigit() for ch in name):
        return False
    return bool(NAME_RE.match(name))


def is_valid_ethiopian_phone(phone: str) -> bool:
    cleaned = re.sub(r"[\s-]", "", phone.strip())
    return bool(PHONE_RE.match(cleaned))


def normalize_ethiopian_phone(phone: str) -> str:
    cleaned = re.sub(r"[\s-]", "", phone.strip())
    if cleaned.startswith("+251"):
        return "0" + cleaned[4:]
    if cleaned.startswith("251"):
        return "0" + cleaned[3:]
    return cleaned


# ---------------------------------------------------------------------------
# Database helpers (unchanged)
# ---------------------------------------------------------------------------
def init_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS registrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            username TEXT,
            lang TEXT DEFAULT 'am',
            name TEXT,
            phone TEXT,
            size TEXT,
            screenshot_file_id TEXT,
            status TEXT DEFAULT 'in_progress',
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def create_registration(user_id, chat_id, **fields) -> int:
    """Insert a brand new registration row and return its id."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    cur = conn.cursor()
    now = datetime.utcnow().isoformat()
    columns = ["user_id", "chat_id", "created_at", "updated_at"] + list(fields.keys())
    placeholders = ", ".join("?" for _ in columns)
    values = [user_id, chat_id, now, now] + list(fields.values())
    cur.execute(
        f"INSERT INTO registrations ({', '.join(columns)}) VALUES ({placeholders})",
        values,
    )
    new_id = cur.lastrowid
    conn.commit()
    conn.close()
    return new_id


def update_registration(reg_id, **fields):
    """Update an existing registration row by its own id."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    cur = conn.cursor()
    now = datetime.utcnow().isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    cur.execute(
        f"UPDATE registrations SET {set_clause}, updated_at = ? WHERE id = ?",
        (*fields.values(), now, reg_id),
    )
    conn.commit()
    conn.close()


def get_registration_by_id(reg_id):
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM registrations WHERE id = ?", (reg_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_registrations_by_user(user_id):
    """Return ALL registrations submitted from this Telegram account,
    newest first — since one account can now register multiple people."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM registrations WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Conversation handlers (unchanged)
# ---------------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    context.user_data["lang"] = DEFAULT_LANG
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🇪🇹 አማርኛ", callback_data="lang:am"),
          InlineKeyboardButton("🇬🇧 English", callback_data="lang:en")]]
    )
    await update.message.reply_text(t(DEFAULT_LANG, "choose_lang"), reply_markup=keyboard)
    return LANG


async def set_lang(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    lang = query.data.split(":")[1]
    context.user_data["lang"] = lang
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(t(lang, "welcome"))
    return NAME


async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    lang = context.user_data.get("lang", DEFAULT_LANG)
    name = update.message.text.strip()
    if not is_valid_name(name):
        await update.message.reply_text(t(lang, "invalid_name"))
        return NAME
    context.user_data["name"] = name
    await update.message.reply_text(t(lang, "ask_phone", name=name))
    return PHONE


async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    lang = context.user_data.get("lang", DEFAULT_LANG)
    phone = update.message.text.strip()
    if not is_valid_ethiopian_phone(phone):
        await update.message.reply_text(t(lang, "invalid_phone"))
        return PHONE
    context.user_data["phone"] = normalize_ethiopian_phone(phone)

    # Inline buttons instead of a custom reply keyboard: reply keyboards
    # are hidden behind a small icon on Telegram Desktop/Web that many
    # people never notice, so the bot looks "stuck" waiting for a size
    # that was never actually sent. Inline buttons show up immediately
    # and identically on every client (mobile, desktop, web).
    rows = [SIZES[i : i + 3] for i in range(0, len(SIZES), 3)]
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(s, callback_data=f"size:{s}") for s in row] for row in rows]
    )
    await update.message.reply_text(
        t(lang, "ask_size"),
        reply_markup=keyboard,
    )
    return SIZE


async def get_size(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    lang = context.user_data.get("lang", DEFAULT_LANG)
    size = query.data.split(":")[1]

    if size not in SIZES:
        await query.message.reply_text(t(lang, "invalid_size", sizes=", ".join(SIZES)))
        return SIZE

    context.user_data["size"] = size
    await query.edit_message_reply_markup(reply_markup=None)  # remove buttons once picked

    user = update.effective_user
    reg_id = create_registration(
        user_id=user.id, chat_id=update.effective_chat.id,
        username=user.username or "", lang=lang,
        name=context.user_data["name"], phone=context.user_data["phone"],
        size=size, status="awaiting_payment",
    )
    context.user_data["reg_id"] = reg_id
    await query.message.reply_text(
        t(lang, "payment_info", price=PRICE, bank=BANK_NAME, acc_name=ACCOUNT_NAME, acc_no=ACCOUNT_NUMBER),
        parse_mode=ParseMode.MARKDOWN,
    )
    return SCREENSHOT


async def get_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    lang = context.user_data.get("lang", DEFAULT_LANG)
    if not update.message.photo:
        await update.message.reply_text(t(lang, "ask_photo_again"))
        return SCREENSHOT

    file_id = update.message.photo[-1].file_id
    user = update.effective_user
    reg_id = context.user_data.get("reg_id")
    if not reg_id:
        # Safety net: shouldn't normally happen, but avoids a crash if
        # someone sends a photo out of sequence after a restart.
        await update.message.reply_text(t(lang, "ask_photo_again"))
        return SCREENSHOT

    update_registration(reg_id, screenshot_file_id=file_id, status="pending_review")
    await update.message.reply_text(t(lang, "waitlisted"), parse_mode=ParseMode.MARKDOWN)

    reg = get_registration_by_id(reg_id)
    caption = (
        f"🆕 New payment proof (reg #{reg_id})\n\nName: {reg['name']}\nPhone: {reg['phone']}\n"
        f"Size: {reg['size']}\nLanguage: {reg['lang']}\n"
        f"Telegram: @{reg['username'] or 'N/A'} (id {user.id})"
    )
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ Approve", callback_data=f"approve:{reg_id}"),
          InlineKeyboardButton("❌ Reject", callback_data=f"reject:{reg_id}")]]
    )
    if ADMIN_CHAT_ID:
        try:
            await context.bot.send_photo(
                chat_id=ADMIN_CHAT_ID, photo=file_id, caption=caption, reply_markup=keyboard
            )
        except Exception:
            logger.exception(
                "Failed to notify admin chat (%s) about registration for user %s.",
                ADMIN_CHAT_ID, user.id,
            )
    else:
        logger.warning("ADMIN_CHAT_ID not set - admin was not notified.")

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    lang = context.user_data.get("lang", DEFAULT_LANG)
    await update.message.reply_text(t(lang, "cancelled"), reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


async def admin_decision(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    action, reg_id_str = query.data.split(":")
    reg_id = int(reg_id_str)
    reg = get_registration_by_id(reg_id)
    if not reg:
        await query.edit_message_caption(caption="⚠️ Registration not found.")
        return
    lang = reg.get("lang") or DEFAULT_LANG
    chat_id = reg["chat_id"]
    if action == "approve":
        update_registration(reg_id, status="approved")
        await context.bot.send_message(
            chat_id=chat_id, text=t(lang, "approved", name=reg["name"], size=reg["size"]),
            parse_mode=ParseMode.MARKDOWN,
        )
        new_caption = query.message.caption + "\n\n✅ APPROVED"
    else:
        update_registration(reg_id, status="rejected")
        await context.bot.send_message(chat_id=chat_id, text=t(lang, "rejected"))
        new_caption = query.message.caption + "\n\n❌ REJECTED"
    await query.edit_message_caption(caption=new_caption, reply_markup=None)


async def my_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = context.user_data.get("lang", DEFAULT_LANG)
    regs = get_registrations_by_user(update.effective_user.id)
    if not regs:
        await update.message.reply_text(t(lang, "no_registration"))
        return

    lines = []
    for reg in regs:
        reg_lang = reg.get("lang") or lang
        status_label = TEXTS[reg_lang]["status_map"].get(reg["status"], reg["status"])
        lines.append(f"{reg['name']} ({reg['size']}) — {status_label}")
    await update.message.reply_text("\n".join(lines))


EXPORTABLE_STATUSES = {"approved", "pending_review", "awaiting_payment", "in_progress", "rejected"}


async def export_registrations(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not ADMIN_CHAT_ID or str(update.effective_chat.id) != str(ADMIN_CHAT_ID):
        return
    status = "approved"
    if context.args:
        arg = context.args[0].lower()
        status = arg if arg in EXPORTABLE_STATUSES or arg == "all" else "approved"

    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    if status == "all":
        cur.execute("SELECT * FROM registrations ORDER BY updated_at ASC")
    else:
        cur.execute("SELECT * FROM registrations WHERE status = ? ORDER BY updated_at ASC", (status,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    if not rows:
        await update.message.reply_text(f"No registrations found with status '{status}'.")
        return

    # Raw status codes (e.g. "pending_review") aren't obvious in a
    # spreadsheet, so translate them to plain labels before writing.
    EXPORT_STATUS_LABELS = {
        "in_progress": "In Progress",
        "awaiting_payment": "Awaiting Payment",
        "pending_review": "Waitlist",
        "approved": "Approved",
        "rejected": "Rejected",
    }
    for row in rows:
        row["status"] = EXPORT_STATUS_LABELS.get(row["status"], row["status"])

    buffer = io.StringIO()
    fieldnames = ["name", "phone", "size", "status"]
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    csv_bytes = io.BytesIO(buffer.getvalue().encode("utf-8-sig"))
    csv_bytes.name = f"registrations_{status}_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.csv"

    await update.message.reply_document(
        document=csv_bytes, filename=csv_bytes.name,
        caption=f"📋 {len(rows)} registration(s) — status: {status}",
    )


async def post_init(application: Application) -> None:
    await application.bot.set_my_description(
        description=(
            "🙏 እንኳን ደህና መጡ! ይህ ቦት የቤተ ክርስቲያናችንን የቲሸርት ምዝገባ ያስተናግዳል፦ "
            "ስም፣ ስልክ እና መጠን ይሰበስባል፣ ክፍያ እንዲፈጽሙ ይጠይቃል፣ እና ክፍያዎ ከተረጋገጠ በኋላ "
            "ይመዘገባሉ። ለመጀመር ከታች ያለውን Start የሚለውን ቁልፍ ይጫኑ ወይም /start ብለው ይላኩ 👇\n\n"
            "Welcome! This bot handles our church T-shirt registration — it "
            "collects your name, phone, and size, asks you to pay, and "
            "registers you once payment is confirmed. Press Start below or "
            "send /start to begin."
        )
    )
    await application.bot.set_my_short_description(
        short_description="የቲሸርት ምዝገባ / T-shirt registration — press Start to begin"
    )
    await application.bot.set_my_commands(
        [
            BotCommand("start", "ምዝገባ ጀምር / Start (or restart) registration"),
            BotCommand("status", "ሁኔታዎን ይመልከቱ / Check your status"),
            BotCommand("cancel", "ሰርዝ / Cancel registration"),
        ]
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled exception while processing an update", exc_info=context.error)


# ---------------------------------------------------------------------------
# Build the Application and its handlers (same structure as polling version)
# ---------------------------------------------------------------------------
if not BOT_TOKEN:
    raise SystemExit("Set BOT_TOKEN in your .env file first.")

init_db()

# PythonAnywhere's free tier has no direct internet access — outbound
# requests must go through their proxy, and the library needs to be told
# about it explicitly (it doesn't happen automatically). PythonAnywhere
# sets the http_proxy/https_proxy environment variables for you, so we
# just need to actually use them. On your own laptop these variables
# won't exist, so this has no effect there.
proxy_url = os.environ.get("https_proxy") or os.environ.get("http_proxy")

builder = Application.builder().token(BOT_TOKEN).post_init(post_init)
if proxy_url:
    logger.info("Using proxy for Telegram API requests: %s", proxy_url)
    builder = builder.proxy(proxy_url).get_updates_proxy(proxy_url)

telegram_app = builder.build()

conv_handler = ConversationHandler(
    entry_points=[CommandHandler("start", start)],
    states={
        LANG: [CallbackQueryHandler(set_lang, pattern=r"^lang:(am|en)$")],
        NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
        PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
        SIZE: [CallbackQueryHandler(get_size, pattern=r"^size:(S|M|L|XL|XXL)$")],
        SCREENSHOT: [MessageHandler(filters.PHOTO, get_screenshot)],
    },
    fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)],
    allow_reentry=True,
)

telegram_app.add_handler(conv_handler)
telegram_app.add_handler(CommandHandler("status", my_status))
telegram_app.add_handler(CommandHandler("export", export_registrations))
telegram_app.add_handler(CallbackQueryHandler(admin_decision, pattern=r"^(approve|reject):\d+$"))
telegram_app.add_error_handler(error_handler)

# ---------------------------------------------------------------------------
# One persistent event loop for the life of the process. PTB's internal
# locks/queues are created against whichever loop is active during
# initialize(), so we reuse this same loop for every webhook call rather
# than spinning up a fresh one per request (asyncio.run() would do that
# and break things).
# ---------------------------------------------------------------------------
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
loop.run_until_complete(telegram_app.initialize())
loop.run_until_complete(post_init(telegram_app))

# A single shared asyncio loop is not safe to enter from two threads at
# once. PythonAnywhere (and most WSGI servers) can serve concurrent
# requests on separate threads, so if two people message the bot at the
# same instant, this lock makes the second request simply wait its turn
# instead of racing the first one into the same loop. For a small event,
# this adds at most a few milliseconds of wait — invisible to users —
# and removes the risk entirely.
webhook_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Flask app — this is what PythonAnywhere's WSGI config points to.
# ---------------------------------------------------------------------------
flask_app = Flask(__name__)


@flask_app.route(f"/webhook/{BOT_TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), telegram_app.bot)
    with webhook_lock:
        loop.run_until_complete(telegram_app.process_update(update))
    return "OK"


@flask_app.route("/")
def index():
    return "T-shirt registration bot is running."


# PythonAnywhere's WSGI config expects a variable named `application`
# pointing at the Flask app object — this is that variable.
application = flask_app

if __name__ == "__main__":
    # Local testing only (won't be reached on PythonAnywhere, which
    # imports `application` from this module directly instead).
    flask_app.run(port=5000)
