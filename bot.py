  
# bot.py
import os
import json
import tempfile
import shutil
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ----------------- LOGGING -----------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ----------------- CONFIG FROM ENV -----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))

VIP_CHANNEL_ID = int(os.getenv("VIP_CHANNEL_ID", "0"))
DARK_CHANNEL_ID = int(os.getenv("DARK_CHANNEL_ID", "0"))

UPI_ID = os.getenv("UPI_ID", "technovastore641100.rzp@icici")
UPI_QR_URL = os.getenv(
    "UPI_QR_URL",
    "https://i.ibb.co/zHrLF0Xg/Whats-App-Image-2025-12-10-at-21-15-51-06c97f44.jpg",
)
UPI_HOW_TO_PAY_LINK = os.getenv("UPI_HOW_TO_PAY_LINK", "https://t.me/+bGduXUnCJk8zNzNh")

CRYPTO_ADDRESS = os.getenv(
    "CRYPTO_ADDRESS", "0xfc14846229f375124d8fed5cd9a789a271a303f5"
)
CRYPTO_NETWORK = os.getenv("CRYPTO_NETWORK", "BEP20")

REMITLY_INFO = os.getenv("REMITLY_INFO", "Send via Remitly")
REMITLY_HOW_TO_PAY_LINK = os.getenv("REMITLY_HOW_TO_PAY_LINK", "https://t.me/+8jECICY--sU2MjIx")

HELP_BOT_USERNAME = os.getenv("HELP_BOT_USERNAME", "@Dark123222_bot")
HELP_BOT_USERNAME_MD = HELP_BOT_USERNAME.replace("_", "\\_")

# Persistent data location (mounted disk)
DATA_DIR = os.getenv("DATA_DIR", "/data")   # default to /data (Render disk mount)
DATA_FILE = os.path.join(DATA_DIR, "paymentbot.json")

# ----------------- CONSTANTS -----------------
IST = timezone(timedelta(hours=5, minutes=30))

PRICE_CONFIG = {
    "vip": {"upi_inr": 499, "crypto_usd": 6, "remit_inr": 499},
    "dark": {"upi_inr": 1999, "crypto_usd": 24, "remit_inr": 1999},
    "both": {"upi_inr": 1749, "crypto_usd": 21, "remit_inr": 1749},
}
PLAN_LABELS = {
    "vip": "VIP Channel",
    "dark": "Dark Channel",
    "both": "VIP + Dark (Combo 30% OFF)",
}

# ----------------- RUNTIME STORAGE (in-memory) -----------------
PENDING_PAYMENTS: Dict[str, Dict[str, Any]] = {}
PURCHASE_LOG: list = []
KNOWN_USERS: set = set()
SENT_INVITES: dict = {}

# ----------------- HELPERS -----------------
def now_ist() -> datetime:
    return datetime.now(IST)

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_CHAT_ID

# Persistence helpers -------------------------------------------------------
def _ensure_data_dir():
    try:
        Path(DATA_DIR).mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.exception("Could not ensure data dir: %s", e)

def _serialize_state() -> dict:
    """Return a JSON-serializable snapshot of runtime state."""
    return {
        "pending_payments": PENDING_PAYMENTS,
        # convert datetimes in purchase log to ISO strings
        "purchase_log": [
            {**{k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in p.items()}}
            for p in PURCHASE_LOG
        ],
        "known_users": list(KNOWN_USERS),
        # SENT_INVITES: convert keys to strings for JSON
        "sent_invites": {str(k): v for k, v in SENT_INVITES.items()},
    }

def _deserialize_state(data: dict):
    """Load JSON data into the runtime variables."""
    global PENDING_PAYMENTS, PURCHASE_LOG, KNOWN_USERS, SENT_INVITES
    if not data:
        return
    PENDING_PAYMENTS = data.get("pending_payments", {}) or {}
    PURCHASE_LOG = []
    for p in data.get("purchase_log", []) or []:
        # parse 'time' field back to datetime if present
        p_copy = dict(p)
        t = p_copy.get("time")
        if isinstance(t, str):
            try:
                p_copy["time"] = datetime.fromisoformat(t)
            except Exception:
                # fallback: leave as string
                pass
        PURCHASE_LOG.append(p_copy)
    KNOWN_USERS = set(data.get("known_users", []) or [])
    sent = data.get("sent_invites", {}) or {}
    # convert keys back to int if possible
    new_sent = {}
    for k, v in sent.items():
        try:
            new_sent[int(k)] = v
        except Exception:
            new_sent[k] = v
    SENT_INVITES = new_sent

def save_state():
    """Atomically save runtime state to disk file."""
    try:
        _ensure_data_dir()
        payload = _serialize_state()
        tmp_fd, tmp_path = tempfile.mkstemp(dir=DATA_DIR)
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        # atomic replace
        shutil.move(tmp_path, DATA_FILE)
        logger.info("State saved to %s", DATA_FILE)
    except Exception as e:
        logger.exception("Failed to save state: %s", e)

def load_state():
    """Load state from disk if file exists."""
    try:
        if not os.path.exists(DATA_FILE):
            logger.info("No data file found at %s — starting fresh", DATA_FILE)
            return
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        _deserialize_state(data)
        logger.info("Loaded state from %s", DATA_FILE)
    except Exception as e:
        logger.exception("Failed to load state: %s", e)

# ----------------- Bot functionality (same as before) -----------------
async def send_access_links(context: ContextTypes.DEFAULT_TYPE, user_id: int, plan: str):
    links_text = []
    try:
        user_links = SENT_INVITES.setdefault(user_id, {})
        if plan in ("vip", "both") and VIP_CHANNEL_ID:
            if "vip" in user_links:
                vip_link = user_links["vip"]
            else:
                vip_link_obj = await context.bot.create_chat_invite_link(
                    chat_id=VIP_CHANNEL_ID, member_limit=1, name=f"user_{user_id}_vip"
                )
                vip_link = vip_link_obj.invite_link
                user_links["vip"] = vip_link
                # save invites after creation
                save_state()
            links_text.append(f"🔑 VIP Channel:\n{vip_link}")

        if plan in ("dark", "both") and DARK_CHANNEL_ID:
            if "dark" in user_links:
                dark_link = user_links["dark"]
            else:
                dark_link_obj = await context.bot.create_chat_invite_link(
                    chat_id=DARK_CHANNEL_ID, member_limit=1, name=f"user_{user_id}_dark"
                )
                dark_link = dark_link_obj.invite_link
                user_links["dark"] = dark_link
                save_state()
            links_text.append(f"🕶 Dark Channel:\n{dark_link}")

    except Exception as e:
        logger.exception("Error creating invite links for user %s: %s", user_id, e)

    if links_text:
        text = "✅ Access granted!\n\n" + "\n\n".join(links_text)
    else:
        text = (
            "✅ Payment approved.\n\n"
            "But I couldn't generate channel links automatically. "
            f"Please contact support: {HELP_BOT_USERNAME}"
        )
    await context.bot.send_message(chat_id=user_id, text=text)

def get_price(plan: str, method: str):
    cfg = PRICE_CONFIG.get(plan, {})
    if method == "upi":
        return cfg.get("upi_inr"), "INR"
    if method == "crypto":
        return cfg.get("crypto_usd"), "USD"
    if method == "remitly":
        return cfg.get("remit_inr"), "INR"
    return None, ""

# Handlers -----------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    KNOWN_USERS.add(user.id)
    # persist known users
    save_state()

    keyboard = [
        [InlineKeyboardButton("💎 VIP Channel (₹499)", callback_data="plan_vip")],
        [InlineKeyboardButton("🕶 Dark Channel (₹1999)", callback_data="plan_dark")],
        [InlineKeyboardButton("🔥 Both (30% OFF)", callback_data="plan_both")],
        [InlineKeyboardButton("🆘 Help", callback_data="plan_help")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = (
        "Welcome to Payment Bot 👋\n\n"
        "Choose what you want to unlock:\n"
        "• 💎 VIP Channel – premium content\n"
        "• 🕶 Dark Channel – ultra premium\n"
        "• 🔥 Both – combo offer with 30% OFF\n\n"
        "After you choose a plan, I'll show payment options."
    )
    if update.message:
        await update.message.reply_text(text, reply_markup=reply_markup)
    else:
        await update.callback_query.message.reply_text(text, reply_markup=reply_markup)

async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user

    if data in ("plan_vip", "plan_dark", "plan_both"):
        plan = data.split("_", 1)[1]
        context.user_data["selected_plan"] = plan
        context.user_data["waiting_for_proof"] = None
        context.user_data["payment_deadline"] = None

        label = PLAN_LABELS.get(plan, plan.upper())
        upi_price, _ = get_price(plan, "upi")
        crypto_price, _ = get_price(plan, "crypto")
        remit_price, _ = get_price(plan, "remitly")

        keyboard = [
            [InlineKeyboardButton(f"💳 UPI (₹{upi_price})", callback_data="pay_upi")],
            [InlineKeyboardButton(f"🪙 Crypto (${crypto_price})", callback_data="pay_crypto")],
            [InlineKeyboardButton(f"🌍 Remitly (₹{remit_price})", callback_data="pay_remitly")],
            [InlineKeyboardButton("⬅ Back", callback_data="back_start")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        text = f"You selected: *{label}*\n\nChoose your payment method below:"
        try:
            await query.message.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown")
        except Exception:
            await query.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")
        return

    if data == "plan_help":
        help_text = (
            "🆘 *Help & Support*\n\n"
            f"For any assistance, contact: {HELP_BOT_USERNAME_MD}\n\n"
            "Type /start anytime to restart."
        )
        try:
            await query.message.edit_text(help_text, parse_mode="Markdown")
        except Exception:
            await query.message.reply_text(help_text, parse_mode="Markdown")
        return

    if data == "back_start":
        fake_update = Update(update.update_id, message=update.effective_message)
        await start(fake_update, context)
        return

    user_plan = context.user_data.get("selected_plan")
    if data in ("pay_upi", "pay_crypto", "pay_remitly") and not user_plan:
        await query.message.reply_text("First choose a plan with /start before selecting payment method.")
        return

    if data in ("pay_upi", "pay_crypto", "pay_remitly"):
        method_map = {"pay_upi": "upi", "pay_crypto": "crypto", "pay_remitly": "remitly"}
        method = method_map[data]
        context.user_data["waiting_for_proof"] = method

        amount, currency = get_price(user_plan, method)
        label = PLAN_LABELS.get(user_plan, user_plan.upper())

        deadline = now_ist() + timedelta(minutes=30)
        context.user_data["payment_deadline"] = deadline.timestamp()
        deadline_str = deadline.strftime("%d %b %Y, %I:%M %p IST")

        if method == "upi":
            msg = (
                "🧾 *UPI Payment Instructions*\n\n"
                f"Plan: *{label}*\n"
                f"Amount: *₹{amount}*\n\n"
                f"UPI ID: `{UPI_ID}`\n\n"
                "1️⃣ Open any UPI app (GPay, PhonePe, Paytm, etc.)\n"
                "2️⃣ Choose *Scan & Pay* or *Pay UPI ID*\n"
                "3️⃣ Either scan the QR image below or pay directly to the UPI ID above.\n"
                "4️⃣ Enter the amount shown above and confirm.\n\n"
                f"If you're confused, see this guide: {UPI_HOW_TO_PAY_LINK}\n\n"
                f"⏳ Time limit: until *{deadline_str}*\n\n"
                "After payment send screenshot/photo here plus optional UTR."
            )
            await query.message.reply_text(msg, parse_mode="Markdown")
            await query.message.reply_photo(
                photo=UPI_QR_URL,
                caption=f"📷 Scan this QR to pay.\nUPI ID: `{UPI_ID}`",
                parse_mode="Markdown"
            )

        elif method == "crypto":
            msg = (
                "🪙 *Crypto Payment Instructions*\n\n"
                f"Plan: *{label}*\n"
                f"Amount: *${amount}*\n\n"
                f"Network: `{CRYPTO_NETWORK}`\n"
                f"Address: `{CRYPTO_ADDRESS}`\n\n"
                f"⏳ Time limit: until *{deadline_str}*\n\n"
                "After payment send screenshot/photo + TXID here."
            )
            await query.message.reply_text(msg, parse_mode="Markdown")

        else:
            msg = (
                "🌍 *Remitly Payment Instructions*\n\n"
                f"Plan: *{label}*\n"
                f"Amount: *₹{amount}*\n\n"
                f"Extra info: {REMITLY_INFO}\n\n"
                f"⏳ Time limit: until *{deadline_str}*\n\n"
                "After payment send screenshot/photo here."
            )
            await query.message.reply_text(msg, parse_mode="Markdown")

        return


    if data.startswith("approve:") or data.startswith("decline:"):
        action, payment_id = data.split(":", 1)
        payment = PENDING_PAYMENTS.get(payment_id)
        if query.from_user.id != ADMIN_CHAT_ID:
            await query.answer("Only admin can use this.", show_alert=True)
            return
        if not payment:
            await query.message.reply_text("⚠️ This payment request was not found or already processed.")
            return
        user_id = payment["user_id"]
        plan = payment["plan"]
        method = payment["method"]
        amount = payment["amount"]
        currency = payment["currency"]
        username = payment["username"]
        if action == "approve":
            PURCHASE_LOG.append({
                "time": now_ist(),
                "user_id": user_id,
                "username": username,
                "plan": plan,
                "method": method,
                "amount": amount,
                "currency": currency,
            })
            # persist purchase log
            save_state()
            try:
                await send_access_links(context, user_id, plan)
            except Exception:
                logger.exception("Error sending access links")
            await query.message.reply_text(f"✅ Approved payment (ID: {payment_id}) for user {user_id} | {amount} {currency}")
        else:
            try:
                await context.bot.send_message(chat_id=user_id, text=("❌ Your payment could not be verified.\nIf this is a mistake, please send a clearer screenshot or contact support: " + HELP_BOT_USERNAME))
            except Exception:
                logger.exception("Can't send decline message to user")
            await query.message.reply_text(f"❌ Declined payment (ID: {payment_id})")
        # remove pending
        PENDING_PAYMENTS.pop(payment_id, None)
        save_state()
        return

async def handle_payment_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    message = update.effective_message

    method = context.user_data.get("waiting_for_proof")
    plan = context.user_data.get("selected_plan")
    if not method or not plan:
        return
    amount, currency = get_price(plan, method)
    payment_id = str(message.message_id) + "_" + str(int(datetime.now().timestamp()))
    PENDING_PAYMENTS[payment_id] = {
        "user_id": user.id,
        "username": user.username or "",
        "plan": plan,
        "method": method,
        "amount": amount,
        "currency": currency,
    }
    # persist pending payments
    save_state()
    try:
        await context.bot.forward_message(chat_id=ADMIN_CHAT_ID, from_chat_id=chat.id, message_id=message.message_id)
    except Exception:
        logger.exception("Forwarding failed")
    kb = [[InlineKeyboardButton("✅ Approve", callback_data=f"approve:{payment_id}"), InlineKeyboardButton("❌ Decline", callback_data=f"decline:{payment_id}")]]
    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=(f"💰 New payment request\nFrom: @{user.username or 'NoUsername'} (ID: {user.id})\nPlan: {PLAN_LABELS.get(plan, plan)}\nMethod: {method.upper()}\nAmount: {amount} {currency}\nPayment ID: {payment_id}\n\nCheck forwarded message and choose:"), reply_markup=InlineKeyboardMarkup(kb))
    await message.reply_text("✅ Payment proof received. We'll verify and send access after approval.")

async def warn_text_not_allowed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    method = context.user_data.get("waiting_for_proof")
    plan = context.user_data.get("selected_plan")
    if not method or not plan:
        return
    await update.message.reply_text("⚠️ Please send a screenshot/photo or document of your payment only. Plain text messages cannot be verified.", parse_mode="Markdown")

# Admin commands (broadcast, income, set_price, set_upi, set_crypto, set_remitly)
async def set_vip_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global VIP_CHANNEL_ID
    user = update.effective_user
    if not is_admin(user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /set_vip <channel_id>")
        return
    try:
        VIP_CHANNEL_ID = int(context.args[0])
        await update.message.reply_text(f"VIP_CHANNEL_ID updated to {VIP_CHANNEL_ID}")
    except ValueError:
        await update.message.reply_text("channel_id must be an integer (e.g. -1001234567890)")

async def set_dark_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global DARK_CHANNEL_ID
    user = update.effective_user
    if not is_admin(user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /set_dark <channel_id>")
        return
    try:
        DARK_CHANNEL_ID = int(context.args[0])
        await update.message.reply_text(f"DARK_CHANNEL_ID updated to {DARK_CHANNEL_ID}")
    except ValueError:
        await update.message.reply_text("channel_id must be an integer (e.g. -1009876543210)")

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /broadcast your message text\n\nThis will send the text to all users who started the bot.")
        return
    text = " ".join(context.args)
    sent = 0
    failed = 0
    for uid in KNOWN_USERS:
        try:
            await context.bot.send_message(chat_id=uid, text=text)
            sent += 1
        except Exception:
            failed += 1
    await update.message.reply_text(f"Broadcast done.\n✅ Sent: {sent}\n❌ Failed: {failed}")

async def income(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        return
    mode = "today"
    if context.args:
        mode = context.args[0].lower()
    now = now_ist()
    if mode == "yesterday":
        start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        label = "Yesterday"
    elif mode in ("7d", "7days", "last7"):
        end = now
        start = now - timedelta(days=7)
        label = "Last 7 days"
    else:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        label = "Today"
    total_inr = 0
    total_usd = 0
    count = 0
    for p in PURCHASE_LOG:
        t = p["time"]
        if start <= t < end:
            count += 1
            if p["currency"] == "INR":
                total_inr += p["amount"] or 0
            elif p["currency"] == "USD":
                total_usd += p["amount"] or 0
    msg = (f"📊 *Income Insights – {label}*\n\n"
           f"Total orders: *{count}*\n"
           f"INR collected: *₹{total_inr}*\n"
           f"USD collected (crypto): *${total_usd}*\n\n"
           "_Note: stats reset if the bot restarts or redeploys._")
    await update.message.reply_text(msg, parse_mode="Markdown")

async def set_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        return
    if len(context.args) != 3:
        await update.message.reply_text("Usage: /set_price <vip|dark|both> <upi|crypto|remitly> <amount>\nExample: /set_price vip upi 599")
        return
    plan, method, amount_str = context.args
    plan = plan.lower()
    method = method.lower()
    if plan not in PRICE_CONFIG or method not in ("upi", "crypto", "remitly"):
        await update.message.reply_text("Invalid plan or method.")
        return
    try:
        amount = float(amount_str)
    except ValueError:
        await update.message.reply_text("Amount must be a number.")
        return
    if method == "upi":
        PRICE_CONFIG[plan]["upi_inr"] = amount
    elif method == "crypto":
        PRICE_CONFIG[plan]["crypto_usd"] = amount
    else:
        PRICE_CONFIG[plan]["remit_inr"] = amount
    await update.message.reply_text(f"Updated price for {PLAN_LABELS.get(plan, plan)} [{method}] to {amount}.")

async def set_upi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global UPI_ID
    user = update.effective_user
    if not is_admin(user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /set_upi <upi_id>")
        return
    UPI_ID = context.args[0]
    await update.message.reply_text(f"UPI ID updated to: {UPI_ID}")

async def set_crypto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global CRYPTO_ADDRESS
    user = update.effective_user
    if not is_admin(user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /set_crypto <address>")
        return
    CRYPTO_ADDRESS = context.args[0]
    await update.message.reply_text(f"Crypto address updated to: {CRYPTO_ADDRESS}")

async def set_remitly(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Admin command to set Remitly help text and optional how-to link.
    Usage:
      /set_remitly Short instructions text
    Or:
      /set_remitly Short instructions text | https://t.me/yourlink
    """
    global REMITLY_INFO, REMITLY_HOW_TO_PAY_LINK
    user = update.effective_user
    if not is_admin(user.id):
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: /set_remitly <instructions>  OR  /set_remitly <instructions> | <how_to_link>"
        )
        return

    # join args then allow optional '|' to separate text and link
    raw = " ".join(context.args)
    parts = [p.strip() for p in raw.split("|", 1)]
    REMITLY_INFO = parts[0]
    # ensure at least one trailing space/newline in stored info so templates concatenate cleanly
    if not REMITLY_INFO.endswith("\n"):
        REMITLY_INFO = REMITLY_INFO.strip()

    if len(parts) > 1 and parts[1]:
        REMITLY_HOW_TO_PAY_LINK = parts[1]
    # persist both
    CONFIG.setdefault("payment", {})["remitly_info"] = REMITLY_INFO
    CONFIG.setdefault("payment", {})["remitly_how_to_pay_link"] = REMITLY_HOW_TO_PAY_LINK
    save_state()

    reply = "Remitly info updated."
    if REMITLY_HOW_TO_PAY_LINK:
        reply += f"\nHow-to-pay link saved: {REMITLY_HOW_TO_PAY_LINK}"
    await update.message.reply_text(reply)


# ----------------- MAIN -----------------
def main():
    # Ensure data dir exists & load existing state
    _ensure_data_dir()
    load_state()

    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set in environment variables.")
    if not ADMIN_CHAT_ID:
        raise RuntimeError("ADMIN_CHAT_ID is not set properly.")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # user handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_buttons))
    app.add_handler(MessageHandler((filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND, handle_payment_proof))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, warn_text_not_allowed))

    # admin handlers
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("income", income))
    app.add_handler(CommandHandler("set_price", set_price))
    app.add_handler(CommandHandler("set_upi", set_upi))
    app.add_handler(CommandHandler("set_crypto", set_crypto))
    app.add_handler(CommandHandler("set_remitly", set_remitly))
    app.add_handler(CommandHandler("set_vip", set_vip_channel))
    app.add_handler(CommandHandler("set_dark", set_dark_channel))

    # start
    app.run_polling()

if __name__ == "__main__":
    main()
