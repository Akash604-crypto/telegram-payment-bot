# main.py
import os
import logging
import asyncio
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

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

# ----------------- CONFIG (env) -----------------
HELP_BOT_TOKEN = os.getenv("HELP_BOT_TOKEN")  # Help bot token
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))
PAYMENT_BOT_USERNAME = os.getenv("PAYMENT_BOT_USERNAME")  # for redirect


# Shared
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))  # admin Telegram ID
PAYMENT_BOT_USERNAME = os.getenv("PAYMENT_BOT_USERNAME", "")  # e.g. @YourPaymentBot

# Payment bot channel IDs (can be set at runtime by admin commands too)
VIP_CHANNEL_ID = int(os.getenv("VIP_CHANNEL_ID", "0"))
DARK_CHANNEL_ID = int(os.getenv("DARK_CHANNEL_ID", "0"))

# Payment details
UPI_ID = os.getenv("UPI_ID", "technovastore641100.rzp@icici")
UPI_QR_URL = os.getenv(
    "UPI_QR_URL",
    "https://i.ibb.co/zHrLF0Xg/Whats-App-Image-2025-12-10-at-21-15-51-06c97f44.jpg",
)
CRYPTO_ADDRESS = os.getenv("CRYPTO_ADDRESS", "0x...")
CRYPTO_NETWORK = os.getenv("CRYPTO_NETWORK", "BEP20")
REMITLY_INFO = os.getenv("REMITLY_INFO", "Send via Remitly")

HELP_BOT_USERNAME = os.getenv("HELP_BOT_USERNAME", "@Dark123222_bot")
HELP_BOT_USERNAME_MD = HELP_BOT_USERNAME.replace("_", "\\_")

# timezone IST
IST = timezone(timedelta(hours=5, minutes=30))

# ----------------- PAYMENT BOT: DATA & HELPERS -----------------
PRICE_CONFIG = {
    "vip": {"upi_inr": 499, "crypto_usd": 6, "remit_inr": 499},
    "dark": {"upi_inr": 1999, "crypto_usd": 24, "remit_inr": 1999},
    "both": {"upi_inr": 1749, "crypto_usd": 21, "remit_inr": 1749},
}
PLAN_LABELS = {"vip": "VIP Channel", "dark": "Dark Channel", "both": "VIP + Dark (Combo 30% OFF)"}

# runtime stores (in-memory)
PENDING_PAYMENTS = {}   # payment_id -> info
PURCHASE_LOG = []       # logs
KNOWN_USERS = set()     # uids
SENT_INVITES = {}       # user_id -> {"vip": link, "dark": link}

def now_ist() -> datetime:
    return datetime.now(IST)

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_CHAT_ID

def _get_price_override(context: ContextTypes.DEFAULT_TYPE, plan: str, method: str):
    """
    If user arrived via negotiated payload, context.user_data may contain 'negotiated_price' and 'negotiated_method'
    Return (amount, currency)
    """
    neg_price = context.user_data.get("negotiated_price")
    neg_method = context.user_data.get("negotiated_method")
    if neg_price and (neg_method == method):
        # negotiated price applies for same method
        if method == "upi" or method == "remitly":
            return neg_price, "INR"
        elif method == "crypto":
            return neg_price, "USD"
    # default
    cfg = PRICE_CONFIG.get(plan, {})
    if method == "upi":
        return cfg.get("upi_inr"), "INR"
    if method == "crypto":
        return cfg.get("crypto_usd"), "USD"
    if method == "remitly":
        return cfg.get("remit_inr"), "INR"
    return None, ""

async def payment_send_access_links(context: ContextTypes.DEFAULT_TYPE, user_id: int, plan: str):
    """
    Create single-use invite links (member_limit=1). Store per-user so we won't recreate repeatedly.
    """
    links_text = []
    try:
        user_links = SENT_INVITES.setdefault(user_id, {})
        if plan in ("vip", "both") and VIP_CHANNEL_ID:
            if "vip" in user_links:
                vip_link = user_links["vip"]
            else:
                vip_obj = await context.bot.create_chat_invite_link(chat_id=VIP_CHANNEL_ID, member_limit=1, name=f"user_{user_id}_vip")
                vip_link = vip_obj.invite_link
                user_links["vip"] = vip_link
            links_text.append(f"🔑 VIP Channel:\n{vip_link}")
        if plan in ("dark", "both") and DARK_CHANNEL_ID:
            if "dark" in user_links:
                dark_link = user_links["dark"]
            else:
                dark_obj = await context.bot.create_chat_invite_link(chat_id=DARK_CHANNEL_ID, member_limit=1, name=f"user_{user_id}_dark")
                dark_link = dark_obj.invite_link
                user_links["dark"] = dark_link
            links_text.append(f"🕶 Dark Channel:\n{dark_link}")
    except Exception as e:
        logger.exception("Error creating invite links: %s", e)

    if links_text:
        text = "✅ Access granted!\n\n" + "\n\n".join(links_text)
    else:
        text = (
            "✅ Payment approved.\n\n"
            f"But I couldn't generate channel links automatically. Please contact support: {HELP_BOT_USERNAME}"
        )
    await context.bot.send_message(chat_id=user_id, text=text)

# Payment bot handlers
async def payment_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /start handler for Payment Bot.
    Accepts optional args (deep link): e.g. ?start=neg_vip_499_upi
    Format we use: neg_<plan>_<amount>_<method>
    """
    user = update.effective_user
    KNOWN_USERS.add(user.id)

    # If started with deep-link args, context.args will contain items
    if context.args:
        arg = context.args[0]
        if arg.startswith("neg_"):
            # expected format: neg_<plan>_<amount>_<method>
            try:
                _, plan, amount_str, method = arg.split("_", 3)
                # set into user_data so payment flow uses negotiated price
                context.user_data["selected_plan"] = plan
                # method mapping sanity
                method = method.lower()
                if method not in ("upi", "crypto", "remitly"):
                    method = "upi"
                # store negotiated price and method (UPI/CRYPTO/REMITLY)
                # amount might be USD for crypto; try float
                try:
                    negotiated = float(amount_str)
                except Exception:
                    negotiated = None
                if negotiated:
                    context.user_data["negotiated_price"] = negotiated
                    context.user_data["negotiated_method"] = method
                # Now show payment method buttons but reflect negotiated details
                label = PLAN_LABELS.get(plan, plan.upper())
                upi_price, _ = _get_price_override(context, plan, "upi")
                crypto_price, _ = _get_price_override(context, plan, "crypto")
                remit_price, _ = _get_price_override(context, plan, "remitly")

                keyboard = [
                    [InlineKeyboardButton(f"💳 UPI (₹{upi_price})", callback_data="pay_upi")],
                    [InlineKeyboardButton(f"🪙 Crypto (${crypto_price})", callback_data="pay_crypto")],
                    [InlineKeyboardButton(f"🌍 Remitly (₹{remit_price})", callback_data="pay_remitly")],
                    [InlineKeyboardButton("⬅ Back", callback_data="back_start")],
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                text = (
                    f"You selected (negotiated): *{label}*\n\n"
                    "Choose your payment method below:"
                )
                await update.effective_message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")
                return
            except Exception as e:
                logger.exception("Failed parsing negotiation start arg: %s", e)
                # fallback to normal start
    # Normal start (no negotiation)
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
    await update.effective_message.reply_text(text, reply_markup=reply_markup)

async def payment_handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        # price may be overridden by negotiated price present in user_data
        upi_price, _ = _get_price_override(context, plan, "upi")
        crypto_price, _ = _get_price_override(context, plan, "crypto")
        remit_price, _ = _get_price_override(context, plan, "remitly")

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
            "🆘 Help & Support\n\n"
            f"For any assistance contact: {HELP_BOT_USERNAME}\n\n"
            "Type /start anytime to restart."
        )
        try:
            await query.message.edit_text(help_text)
        except Exception:
            await query.message.reply_text(help_text)
        return

    if data == "back_start":
        # emulate /start
        fake_update = Update(update.update_id, message=update.effective_message)
        await payment_start(fake_update, context)
        return

    # payment methods
    user_plan = context.user_data.get("selected_plan")
    if data in ("pay_upi", "pay_crypto", "pay_remitly") and not user_plan:
        await query.message.reply_text("First choose a plan with /start before selecting payment method.")
        return

    if data in ("pay_upi", "pay_crypto", "pay_remitly"):
        method_map = {"pay_upi": "upi", "pay_crypto": "crypto", "pay_remitly": "remitly"}
        method = method_map[data]
        context.user_data["waiting_for_proof"] = method

        amount, currency = _get_price_override(context, user_plan, method)
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
                f"⏳ Time limit: until *{deadline_str}*\n\n"
                "After payment send screenshot/photo here plus optional UTR."
            )
            await query.message.reply_text(msg, parse_mode="Markdown")
            await query.message.reply_photo(photo=UPI_QR_URL, caption=f"📷 Scan this QR to pay.\nUPI ID: `{UPI_ID}`", parse_mode="Markdown")
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

    # admin actions for approving forwarded payment proofs etc. (approve: or decline:)
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
            PURCHASE_LOG.append({"time": now_ist(), "user_id": user_id, "username": username, "plan": plan, "method": method, "amount": amount, "currency": currency})
            try:
                await payment_send_access_links(context, user_id, plan)
            except Exception as e:
                logger.exception("Error sending access links: %s", e)
            await query.message.reply_text(f"✅ Approved payment (ID: {payment_id}) for user {user_id} | Plan: {PLAN_LABELS.get(plan, plan)} | {amount} {currency}")
        else:
            try:
                await context.bot.send_message(chat_id=user_id, text=("❌ Your payment could not be verified.\nIf this is a mistake, please send a clearer screenshot or contact support: " + HELP_BOT_USERNAME))
            except Exception:
                logger.exception("Can't send decline message to user")
            await query.message.reply_text(f"❌ Declined payment (ID: {payment_id})")
        PENDING_PAYMENTS.pop(payment_id, None)
        return

async def payment_handle_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Accepts photo/document as payment proof; forward to admin and attach Approve/Decline buttons
    user = update.effective_user
    chat = update.effective_chat
    message = update.effective_message
    method = context.user_data.get("waiting_for_proof")
    plan = context.user_data.get("selected_plan")
    if not method or not plan:
        # nothing to verify
        return
    amount, currency = _get_price_override(context, plan, method)
    payment_id = str(message.message_id) + "_" + str(int(time.time()))
    PENDING_PAYMENTS[payment_id] = {"user_id": user.id, "username": user.username or "", "plan": plan, "method": method, "amount": amount, "currency": currency}
    try:
        await context.bot.forward_message(chat_id=ADMIN_CHAT_ID, from_chat_id=chat.id, message_id=message.message_id)
    except Exception:
        logger.exception("Forwarding failed")
    kb = [[InlineKeyboardButton("✅ Approve", callback_data=f"approve:{payment_id}"), InlineKeyboardButton("❌ Decline", callback_data=f"decline:{payment_id}")]]
    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=(f"💰 New payment request\nFrom: @{user.username or 'NoUsername'} (ID: {user.id})\nPlan: {PLAN_LABELS.get(plan, plan)}\nMethod: {method.upper()}\nAmount: {amount} {currency}\nPayment ID: {payment_id}\n\nCheck forwarded message and choose:"), reply_markup=InlineKeyboardMarkup(kb))
    await message.reply_text("✅ Payment proof received. We'll verify and send access after approval.")

async def payment_warn_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    method = context.user_data.get("waiting_for_proof")
    plan = context.user_data.get("selected_plan")
    if not method or not plan:
        return
    await update.message.reply_text("⚠️ Please send a screenshot/photo or document of your payment only. Plain text messages cannot be verified.", parse_mode="Markdown")


# ----------------- HELP BOT: Simple support + negotiation -----------------
# runtime store
NEGOTIATIONS = {}  # neg_id -> data

async def help_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("💳 Payment issue", callback_data="h_payment")],
        [InlineKeyboardButton("🛠 Technical issue", callback_data="h_tech")],
        [InlineKeyboardButton("✉️ Others", callback_data="h_other")],
        [InlineKeyboardButton("🤝 Negotiate price", callback_data="h_negotiate")],
    ]
    await update.effective_message.reply_text("Help Bot — choose an option:", reply_markup=InlineKeyboardMarkup(kb))

# callback handler for help bot
async def help_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    uid = query.from_user.id

    if data == "h_payment":
        await query.message.reply_text("Please send screenshot/photo/document of your payment and (optional) UTR/reference number. We'll forward to admin for manual verification.")
        return
    if data == "h_tech":
        await query.message.reply_text("Please send a screenshot of the technical issue and describe the problem in a short sentence.")
        return
    if data == "h_other":
        await query.message.reply_text("Type your issue below (image optional). We'll forward to admin.")
        return

    if data == "h_negotiate":
        kb = [
            [InlineKeyboardButton("VIP", callback_data="neg_service_vip")],
            [InlineKeyboardButton("DARK", callback_data="neg_service_dark")],
            [InlineKeyboardButton("BOTH", callback_data="neg_service_both")],
        ]
        await query.message.reply_text("Choose which service you want to negotiate:", reply_markup=InlineKeyboardMarkup(kb))
        return

    # negotiation service chosen
    if data.startswith("neg_service_"):
        service = data.split("neg_service_")[1]
        context.user_data["neg_service"] = service
        kb = [
            [InlineKeyboardButton("💳 UPI", callback_data="neg_method_upi"), InlineKeyboardButton("🪙 Crypto", callback_data="neg_method_crypto")],
            [InlineKeyboardButton("🌍 Remitly", callback_data="neg_method_remitly")],
        ]
        await query.message.reply_text(f"You picked *{service}*. Now choose preferred payment method:", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        return

    if data.startswith("neg_method_"):
        method = data.split("neg_method_")[1]
        context.user_data["neg_method"] = method
        await query.message.reply_text("Enter the amount you want to pay (numbers only). Example: 499 or 6.5")
        # next message will be captured by a handler (see below)
        return

    # admin approve/decline negotiation callback
    if data.startswith("neg_approve:") or data.startswith("neg_decline:"):
        if query.from_user.id != ADMIN_CHAT_ID:
            await query.answer("Only admin can do this.", show_alert=True)
            return
        action, neg_id = data.split(":", 1)
        neg = NEGOTIATIONS.get(neg_id)
        if not neg:
            await query.message.reply_text("Negotiation not found or already processed.")
            return
        user_id = neg["user_id"]
        plan = neg["plan"]
        amount = neg["amount"]
        method = neg["method"]
        if action == "neg_approve":
            # send user a deep link to payment bot that pre-fills negotiation
            if not PAYMENT_BOT_USERNAME:
                await query.message.reply_text("PAYMENT_BOT_USERNAME not configured in env.")
            else:
                # payload format: neg_<plan>_<amount>_<method>
                payload = f"neg_{plan}_{amount}_{method}"
                botname = PAYMENT_BOT_USERNAME.lstrip("@")
                deep_link = f"https://t.me/{botname}?start={payload}"
                await context.bot.send_message(chat_id=user_id, text=(f"✅ Admin approved your negotiated price of *{amount}* for *{plan}* ({method}).\n\n"
                                                                      f"Click to continue payment: {deep_link}"),
                                               parse_mode="Markdown")
                await query.message.reply_text(f"Negotiation approved and user notified: {user_id}")
        else:
            # declined
            await context.bot.send_message(chat_id=user_id, text="❌ Admin declined your negotiation request. You may try again or contact support.")
            await query.message.reply_text(f"Negotiation declined and user notified: {user_id}")
        NEGOTIATIONS.pop(neg_id, None)
        return

# capture negotiation amount (message after selecting method)
async def help_capture_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if "neg_service" not in context.user_data or "neg_method" not in context.user_data:
        return  # not in negotiation flow
    text = (update.effective_message.text or "").strip()
    # validate numeric
    try:
        amount = float(text)
    except Exception:
        await update.effective_message.reply_text("Please send a valid number like 499 or 6.5")
        return
    plan = context.user_data.pop("neg_service")
    method = context.user_data.pop("neg_method")
    neg_id = str(uuid4())[:8]
    NEGOTIATIONS[neg_id] = {"user_id": user.id, "username": user.username or "", "plan": plan, "method": method, "amount": amount}
    # forward to admin with approve/decline buttons
    kb = [[InlineKeyboardButton("✅ Approve", callback_data=f"neg_approve:{neg_id}"), InlineKeyboardButton("❌ Decline", callback_data=f"neg_decline:{neg_id}")]]
    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=(f"🤝 Negotiation request\nFrom: @{user.username or 'NoUsername'} (ID: {user.id})\nService: {plan}\nMethod: {method}\nAmount: {amount}\nNeg ID: {neg_id}\n\nTap Approve/Decline to respond."),
                                   reply_markup=InlineKeyboardMarkup(kb))
    await update.effective_message.reply_text("✅ Your negotiation request was sent to admin. You'll be notified once admin responds.")

# general handlers for help bot: payment proof, tech image etc - forward to admin
async def help_forward_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # forward any photo/document to admin with small label depending on last callback context
    user = update.effective_user
    message = update.effective_message
    # try forward
    try:
        await context.bot.forward_message(chat_id=ADMIN_CHAT_ID, from_chat_id=message.chat_id, message_id=message.message_id)
        await message.reply_text("Forwarded to admin. They will check and reply.")
    except Exception:
        logger.exception("Failed forward to admin")
        await message.reply_text("Couldn't forward to admin. Try again later.")

# ----------------- APP LAUNCH: two apps concurrently -----------------
async def start_bots():
    # Payment Bot app
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is required for Payment Bot (env)")
    payment_app = ApplicationBuilder().token(BOT_TOKEN).build()
    # payment handlers
    payment_app.add_handler(CommandHandler("start", payment_start))
    payment_app.add_handler(CallbackQueryHandler(payment_handle_buttons))
    payment_app.add_handler(MessageHandler((filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND, payment_handle_proof))
    payment_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, payment_warn_text))

    # Help Bot app
    if not HELP_BOT_TOKEN:
        raise RuntimeError("HELP_BOT_TOKEN is required for Help Bot (env)")
    help_app = ApplicationBuilder().token(HELP_BOT_TOKEN).build()
    help_app.add_handler(CommandHandler("start", help_start))
    help_app.add_handler(CallbackQueryHandler(help_callbacks))
    help_app.add_handler(MessageHandler((filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND, help_forward_proof))
    help_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, help_capture_amount))

    # start both run_polling concurrently
    await asyncio.gather(payment_app.run_polling(), help_app.run_polling())

if __name__ == "__main__":
    try:
        asyncio.run(start_bots())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down bots")
