# bot.py
import os
import logging
from datetime import datetime, timedelta, timezone
import re

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
from telegram.constants import ParseMode

# ----------------- LOGGING -----------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ----------------- CONFIG FROM ENV -----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))

VIP_CHANNEL_ID = int(os.getenv("VIP_CHANNEL_ID", "0"))   # e.g. -1001234567890
DARK_CHANNEL_ID = int(os.getenv("DARK_CHANNEL_ID", "0")) # e.g. -1009876543210

# Base payment details (can be changed via admin commands)
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

REMITLY_INFO = os.getenv("REMITLY_INFO", "Send ₹499 via Remitly to given UPI.")
REMITLY_HOW_TO_PAY_LINK = os.getenv(
    "REMITLY_HOW_TO_PAY_LINK", "https://t.me/+8jECICY--sU2MjIx"
)

HELP_BOT_USERNAME = os.getenv("HELP_BOT_USERNAME", "@Dark123222_bot")

# Timezone (IST)
IST = timezone(timedelta(hours=5, minutes=30))

# ----------------- PRODUCTS & PRICES -----------------
PRICE_CONFIG = {
    "vip": {
        "upi_inr": 499,
        "crypto_usd": 6,
        "remit_inr": 499,
    },
    "dark": {
        "upi_inr": 1999,
        "crypto_usd": 24,
        "remit_inr": 1999,
    },
    "both": {
        "upi_inr": 1749,
        "crypto_usd": 21,
        "remit_inr": 1749,
    },
}

PLAN_LABELS = {
    "vip": "VIP Channel",
    "dark": "Dark Channel",
    "both": "VIP + Dark (Combo 30% OFF)",
}

# ----------------- RUNTIME STORAGE -----------------
PENDING_PAYMENTS = {}    # payment_id -> {user_id,...}
PURCHASE_LOG = []        # simple income log
KNOWN_USERS = set()      # for broadcast
SENT_INVITES = {}        # user_id -> {"vip": invite_link, "dark": invite_link}

# ----------------- HELPERS -----------------


def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_CHAT_ID


def now_ist() -> datetime:
    return datetime.now(IST)


# Markdown v2 escaping helper for safety (we're using Markdown here)
def md_escape(text: str) -> str:
    if not text:
        return ""
    # Escape characters that Markdown interprets in PTB when using ParseMode.MARKDOWN
    # This escapes common troublesome characters for basic Markdown (underscores/backticks/asterisks/brackets)
    replace_map = {
        "\\": "\\\\",
        "_": "\\_",
        "*": "\\*",
        "[": "\\[",
        "]": "\\]",
        "`": "\\`",
        "<": "\\<",
        ">": "\\>",
        "(": "\\(",
        ")": "\\)",
        "~": "\\~",
        "-": "\\-",
        "+": "\\+",
        "=": "\\=",
        "|": "\\|",
        "{": "\\{",
        "}": "\\}",
        ".": "\\.",  # dot sometimes causes offsets, safe to escape
        "!": "\\!",
        "#": "\\#",
    }
    # perform escaping
    pattern = re.compile("|".join(re.escape(k) for k in replace_map.keys()))
    return pattern.sub(lambda m: replace_map[m.group(0)], text)


async def safe_edit_or_reply(message, text: str, reply_markup=None, parse_mode=ParseMode.MARKDOWN):
    """Try to edit message; if it fails (deleted/parse problems), reply instead."""
    try:
        return await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception as e:
        logger.debug("edit_text failed (%s) — falling back to reply_text", e)
        try:
            return await message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        except Exception as e2:
            logger.error("reply_text also failed: %s", e2)
            # last fallback: send without parse_mode
            try:
                return await message.reply_text(text, reply_markup=reply_markup)
            except Exception as e3:
                logger.exception("Failed to send help message: %s", e3)
                return None


async def send_access_links(context: ContextTypes.DEFAULT_TYPE, user_id: int, plan: str):
    """
    Create one single-use invite link per channel for this user and send it.
    Uses member_limit=1 and optional name so links are identifiable in channel settings.
    Stores generated links in SENT_INVITES so you can resend without creating new links.
    """
    links_text = []
    try:
        user_links = SENT_INVITES.setdefault(user_id, {})

        # VIP
        if plan in ("vip", "both") and VIP_CHANNEL_ID:
            if "vip" in user_links:
                vip_link = user_links["vip"]
            else:
                try:
                    vip_link_obj = await context.bot.create_chat_invite_link(
                        chat_id=VIP_CHANNEL_ID,
                        member_limit=1,
                        name=f"user_{user_id}_vip"
                    )
                    vip_link = vip_link_obj.invite_link
                    user_links["vip"] = vip_link
                except Exception as e:
                    logger.error("Failed to create VIP invite link: %s", e)
                    vip_link = None
            if vip_link:
                links_text.append(f"🔑 VIP Channel:\n{vip_link}")

        # DARK
        if plan in ("dark", "both") and DARK_CHANNEL_ID:
            if "dark" in user_links:
                dark_link = user_links["dark"]
            else:
                try:
                    dark_link_obj = await context.bot.create_chat_invite_link(
                        chat_id=DARK_CHANNEL_ID,
                        member_limit=1,
                        name=f"user_{user_id}_dark"
                    )
                    dark_link = dark_link_obj.invite_link
                    user_links["dark"] = dark_link
                except Exception as e:
                    logger.error("Failed to create DARK invite link: %s", e)
                    dark_link = None
            if dark_link:
                links_text.append(f"🕶 Dark Channel:\n{dark_link}")

    except Exception as e:
        logger.exception("Error building invite links for %s: %s", user_id, e)

    if links_text:
        text = "✅ Access granted!\n\n" + "\n\n".join(links_text)
        try:
            await context.bot.send_message(chat_id=user_id, text=text)
        except Exception as e:
            logger.error("Failed to send access links to user %s: %s", user_id, e)
    else:
        text = (
            "✅ Payment approved.\n\n"
            "But I couldn't generate channel links automatically.\n"
            f"Please contact support: {HELP_BOT_USERNAME}"
        )
        try:
            await context.bot.send_message(chat_id=user_id, text=text)
        except Exception as e:
            logger.error("Failed to send fallback help to user %s: %s", user_id, e)


def get_price(plan: str, method: str):
    cfg = PRICE_CONFIG.get(plan, {})
    if method == "upi":
        return cfg.get("upi_inr"), "INR"
    if method == "crypto":
        return cfg.get("crypto_usd"), "USD"
    if method == "remitly":
        return cfg.get("remit_inr"), "INR"
    return None, ""


# ----------------- HANDLERS -----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user:
        KNOWN_USERS.add(user.id)

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

    # If this update has a message -> normal /start; if it's callback_query we'll reply to message
    if update.message:
        await update.message.reply_text(text, reply_markup=reply_markup)
    else:
        # callback scenario
        try:
            await update.callback_query.message.reply_text(text, reply_markup=reply_markup)
        except Exception:
            # fallback
            await context.bot.send_message(chat_id=update.callback_query.from_user.id, text=text, reply_markup=reply_markup)


async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user

    # ---------- PLAN SELECTION ----------
    if data in ("plan_vip", "plan_dark", "plan_both"):
        plan = data.split("_", 1)[1]  # 'vip' | 'dark' | 'both'
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

        text = f"You selected: *{md_escape(label)}*\n\nChoose your payment method below:"
        await safe_edit_or_reply(query.message, text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
        return

    # ---------- HELP BUTTON ----------
    if data == "plan_help":
        help_text = (
            "🆘 *Help & Support*\n\n"
            f"For any assistance, contact: {md_escape(HELP_BOT_USERNAME)}\n\n"
            "Type /start anytime to restart."
        )
        await safe_edit_or_reply(query.message, help_text, parse_mode=ParseMode.MARKDOWN)
        return

    # ---------- BACK TO START ----------
    if data == "back_start":
        # call start with the same update (it will handle callback path)
        await start(update, context)
        return

    # ---------- PAYMENT METHOD BUTTONS ----------
    user_plan = context.user_data.get("selected_plan")
    if data in ("pay_upi", "pay_crypto", "pay_remitly") and not user_plan:
        await query.message.reply_text("First choose a plan with /start before selecting payment method.")
        return

    if data in ("pay_upi", "pay_crypto", "pay_remitly"):
        method_map = {
            "pay_upi": "upi",
            "pay_crypto": "crypto",
            "pay_remitly": "remitly",
        }
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
                f"Plan: *{md_escape(label)}*\n"
                f"Amount: *₹{amount}*\n\n"
                f"UPI ID: `{md_escape(UPI_ID)}`\n\n"
                "1️⃣ Open any UPI app (GPay, PhonePe, Paytm, etc.)\n"
                "2️⃣ Choose *Scan & Pay* or *Pay UPI ID*\n"
                "3️⃣ Either scan the QR image below or pay directly to the UPI ID above.\n"
                "4️⃣ Enter the amount shown above and confirm.\n\n"
                f"If you're confused, see this guide: {md_escape(UPI_HOW_TO_PAY_LINK)}\n\n"
                f"⏳ *Time limit:* Please pay within 30 minutes.\n"
                f"Your slot expires at: *{md_escape(deadline_str)}*\n\n"
                "*After payment send me here:*\n"
                "• Payment screenshot (photo)\n"
                "• UTR number (optional, as text)\n"
                "I’ll verify and then send your access links. ✅"
            )

            await query.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
            try:
                await query.message.reply_photo(
                    photo=UPI_QR_URL,
                    caption=f"📷 Scan this QR to pay via UPI.\nUPI ID: `{md_escape(UPI_ID)}`",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                # if caption parse fails, send without parse mode
                await query.message.reply_photo(photo=UPI_QR_URL, caption=f"📷 Scan this QR to pay via UPI.\nUPI ID: {UPI_ID}")

        elif method == "crypto":
            msg = (
                "🪙 *Crypto Payment Instructions*\n\n"
                f"Plan: *{md_escape(label)}*\n"
                f"Amount: *${md_escape(str(amount))}*\n\n"
                f"Network: `{md_escape(CRYPTO_NETWORK)}`\n"
                f"Address: `{md_escape(CRYPTO_ADDRESS)}`\n\n"
                "1️⃣ Open your crypto wallet.\n"
                f"2️⃣ Select *Send* on `{md_escape(CRYPTO_NETWORK)}` network.\n"
                "3️⃣ Paste the address above.\n"
                "4️⃣ Enter the amount and confirm.\n\n"
                f"⏳ *Time limit:* 30 minutes (until *{md_escape(deadline_str)}*).\n\n"
                "*After payment send me here:*\n"
                "• Transaction screenshot\n"
                "• TxID / Hash (optional)\n"
                "I’ll verify and then send your access links. ✅"
            )
            await query.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

        elif method == "remitly":
            msg = (
                "🌍 *Remitly Payment Instructions*\n\n"
                f"Plan: *{md_escape(label)}*\n"
                f"Amount: *₹{md_escape(str(amount))}*\n\n"
                "👉 Select *India* as destination and enter the amount above.\n\n"
                "👉 Recipient Name: *Govind Mahto*\n"
                "👉 UPI ID: `govindmahto21@axl`\n"
                "👉 Reason for Payment: *Family Support*\n\n"
                "⚠ *IMPORTANT:*\n"
                "• Ensure I receive the exact INR amount.\n"
                "• Take a screenshot of the *Transfer Complete* screen.\n\n"
                f"Extra help / how to pay: {md_escape(REMITLY_HOW_TO_PAY_LINK)}\n\n"
                f"⏳ *Time limit:* 30 minutes (until *{md_escape(deadline_str)}*).\n\n"
                "*After payment send me here:*\n"
                "• Transfer complete screenshot\n"
                "• Reference/UTR number (optional)\n"
                "I’ll verify and then send your access links. ✅"
            )
            await query.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

        return

    # ---------- APPROVE / DECLINE BY ADMIN ----------
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
        username = payment.get("username", "")

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

            try:
                await send_access_links(context, user_id, plan)
            except Exception as e:
                logger.exception("Error sending access links to user %s: %s", user_id, e)

            await query.message.reply_text(
                f"✅ Approved payment (ID: {payment_id}) for user {user_id} | Plan: {PLAN_LABELS.get(plan, plan)} | {amount} {currency}"
            )

        else:  # decline
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=(
                        "❌ Your payment could not be verified.\n"
                        f"If this is a mistake, please send a clearer screenshot or contact support: {HELP_BOT_USERNAME}"
                    ),
                )
            except Exception as e:
                logger.exception("Error sending decline notice to user %s: %s", user_id, e)

            await query.message.reply_text(
                f"❌ Declined payment (ID: {payment_id}) for user {user_id} | Plan: {PLAN_LABELS.get(plan, plan)}"
            )

        PENDING_PAYMENTS.pop(payment_id, None)
        return


# ----------------- PAYMENT PROOF HANDLING -----------------
async def handle_payment_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    message = update.effective_message

    method = context.user_data.get("waiting_for_proof")
    plan = context.user_data.get("selected_plan")

    if not method or not plan:
        return

    deadline_ts = context.user_data.get("payment_deadline")
    expired_note = ""
    if deadline_ts:
        deadline = datetime.fromtimestamp(deadline_ts, tz=IST)
        if now_ist() > deadline:
            expired_note = "\n\n⚠️ NOTE: Payment window (30 mins) is expired. Please double-check manually."

    amount, currency = get_price(plan, method)

    payment_id = str(message.message_id)

    PENDING_PAYMENTS[payment_id] = {
        "user_id": user.id,
        "username": user.username or "",
        "plan": plan,
        "method": method,
        "amount": amount,
        "currency": currency,
    }

    try:
        await context.bot.forward_message(
            chat_id=ADMIN_CHAT_ID,
            from_chat_id=chat.id,
            message_id=message.message_id,
        )
    except Exception as e:
        logger.exception("Error forwarding payment proof to admin: %s", e)

    keyboard = [
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"approve:{payment_id}"),
            InlineKeyboardButton("❌ Decline", callback_data=f"decline:{payment_id}"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    admin_text = (
        "💰 New payment request\n\n"
        f"From: @{md_escape(user.username or 'NoUsername')} (ID: {user.id})\n"
        f"Plan: {md_escape(PLAN_LABELS.get(plan, plan))}\n"
        f"Method: {md_escape(method.upper())}\n"
        f"Amount: {md_escape(str(amount))} {md_escape(currency)}\n"
        f"Payment ID: {md_escape(payment_id)}{md_escape(expired_note)}\n\n"
        "Check the forwarded screenshot/message above, then tap Approve or Decline:"
    )

    try:
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.exception("Error sending admin decision message: %s", e)
        # try without markdown
        try:
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_text, reply_markup=reply_markup)
        except Exception as e2:
            logger.exception("Failed to notify admin at all: %s", e2)

    try:
        await message.reply_text(
            "✅ Payment proof received.\n\nPlease wait while we manually verify it. You will get your channel access links here after approval. ⏳"
        )
    except Exception:
        pass


async def warn_text_not_allowed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    method = context.user_data.get("waiting_for_proof")
    plan = context.user_data.get("selected_plan")
    if not method or not plan:
        return

    try:
        await update.message.reply_text(
            "⚠️ Please send a screenshot/photo or document of your payment only. Plain text messages cannot be verified."
        )
    except Exception:
        pass


# ----------------- ADMIN COMMANDS -----------------
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
        await update.message.reply_text("Usage: /broadcast your message text\nThis will send the text to all users who started the bot.")
        return

    text = " ".join(context.args)
    sent = 0
    failed = 0
    for uid in list(KNOWN_USERS):
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

    msg = (
        f"📊 *Income Insights – {md_escape(label)}*\n\n"
        f"Total orders: *{count}*\n"
        f"INR collected: *₹{total_inr}*\n"
        f"USD collected (crypto): *${total_usd}*\n\n"
        "_Note: stats reset if the bot restarts or redeploys._"
    )

    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)


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
    global REMITLY_INFO
    user = update.effective_user
    if not is_admin(user.id):
        return

    if not context.args:
        await update.message.reply_text("Usage: /set_remitly <short description>")
        return

    REMITLY_INFO = " ".join(context.args)
    await update.message.reply_text(f"Remitly info updated to:\n{REMITLY_INFO}")


# ----------------- MAIN -----------------
def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set in environment variables.")
    if not ADMIN_CHAT_ID:
        raise RuntimeError("ADMIN_CHAT_ID is not set properly.")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # user commands
    app.add_handler(CommandHandler("start", start))

    # admin commands
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("income", income))
    app.add_handler(CommandHandler("set_price", set_price))
    app.add_handler(CommandHandler("set_upi", set_upi))
    app.add_handler(CommandHandler("set_crypto", set_crypto))
    app.add_handler(CommandHandler("set_remitly", set_remitly))

    # admin runtime channel setters
    app.add_handler(CommandHandler("set_vip", set_vip_channel))
    app.add_handler(CommandHandler("set_dark", set_dark_channel))

    # callbacks and message handlers
    app.add_handler(CallbackQueryHandler(handle_buttons))

    app.add_handler(
        MessageHandler(
            (filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND,
            handle_payment_proof,
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            warn_text_not_allowed,
        )
    )

    logger.info("Starting bot polling...")
    app.run_polling()


if __name__ == "__main__":
    main()
