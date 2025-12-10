import os
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
import logging

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

# 🔹 CONFIG


BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID"))
ACCESS_LINK = os.getenv("ACCESS_LINK")


UPI_ID = "technovastore641100.rzp@icici"
UPI_QR_URL = "https://i.ibb.co/zHrLF0Xg/Whats-App-Image-2025-12-10-at-21-15-51-06c97f44.jpg"
UPI_HOW_TO_PAY_LINK = "https://t.me/+bGduXUnCJk8zNzNh"

CRYPTO_ADDRESS = "0xfc14846229f375124d8fed5cd9a789a271a303f5"
CRYPTO_NETWORK = "BEP20"

REMITLY_HOW_TO_PAY_LINK = "https://t.me/+8jECICY--sU2MjIx"

HELP_BOT_USERNAME = "@Dark123222_bot"

# In-memory store: payment_id -> {user_id, payment_type}
PENDING_PAYMENTS = {}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("💳 UPI Payment", callback_data="pay_upi")],
        [InlineKeyboardButton("🪙 Crypto Payment", callback_data="pay_crypto")],
        [InlineKeyboardButton("🌍 Remitly Payment", callback_data="pay_remitly")],
        [InlineKeyboardButton("🆘 Help", callback_data="help")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    text = (
        "Welcome! 👋\n\n"
        "Choose your payment method below. After payment, send the screenshot and (optional) UTR number here.\n"
        "Once I verify, you'll receive your access link. 🔑"
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

    # ---- Payment methods selected by user ----
    if data == "pay_upi":
        context.user_data["waiting_for_proof"] = "upi"
        msg = (
            "🧾 *UPI Payment Instructions*\n\n"
            f"UPI ID: `{UPI_ID}`\n\n"
            "1️⃣ Open any UPI app (GPay, PhonePe, Paytm, etc.)\n"
            "2️⃣ Choose *Scan & Pay* or *Pay UPI ID*\n"
            f"3️⃣ You can scan this QR: {UPI_QR_URL}\n"
            f"   Or pay directly to UPI ID above.\n"
            "4️⃣ Enter the correct amount (e.g. ₹499 or your plan amount)\n"
            "5️⃣ Confirm the payment.\n\n"
            f"If you're confused, see this full guide: {UPI_HOW_TO_PAY_LINK}\n\n"
            "*After payment send me here:*\n"
            "• Payment screenshot (photo)\n"
            "• UTR number (optional, as text)\n\n"
            "I will verify and then send your access link. ✅"
        )
        await query.message.reply_text(msg, parse_mode="Markdown")

    elif data == "pay_crypto":
        context.user_data["waiting_for_proof"] = "crypto"
        msg = (
            "🪙 *Crypto Payment Instructions*\n\n"
            f"Network: `{CRYPTO_NETWORK}`\n"
            f"Address: `{CRYPTO_ADDRESS}`\n\n"
            "1️⃣ Open your crypto wallet.\n"
            f"2️⃣ Select *Send* on `{CRYPTO_NETWORK}` network.\n"
            "3️⃣ Paste the address above.\n"
            "4️⃣ Enter the amount and confirm.\n\n"
            "*After payment send me here:*\n"
            "• Transaction screenshot\n"
            "• TxID / Hash (optional)\n\n"
            "I will verify and then send your access link. ✅"
        )
        await query.message.reply_text(msg, parse_mode="Markdown")

    elif data == "pay_remitly":
        context.user_data["waiting_for_proof"] = "remitly"
        msg = (
            "🌍 *Remitly Payment Instructions*\n\n"
            "👉 Select *India* as destination and enter *₹499*.\n\n"
            "👉 Enter Recipient Name: *Govind Mahto*\n"
            "👉 Enter UPI ID: `govindmahto21@axl`\n"
            "👉 Reason for Payment: *Family Support*\n\n"
            "⚠ *IMPORTANT:*\n"
            "• Ensure I receive exactly ₹499 INR.\n"
            "• Take a screenshot of the *Transfer Complete* screen.\n\n"
            f"Extra help / how to pay: {REMITLY_HOW_TO_PAY_LINK}\n\n"
            "*After payment send me here:*\n"
            "• Transfer complete screenshot\n"
            "• Reference/UTR number (optional)\n\n"
            "I will verify and then send your access link. ✅"
        )
        await query.message.reply_text(msg, parse_mode="Markdown")

    elif data == "help":
        msg = (
            "🆘 *Help & Support*\n\n"
            f"For support, message: {HELP_BOT_USERNAME}\n\n"
            "Or type your question here and we'll assist you."
        )
        await query.message.reply_text(msg, parse_mode="Markdown")

    # ---- Admin buttons for approve/decline ----
    elif data.startswith("approve:") or data.startswith("decline:"):
        action, payment_id = data.split(":", 1)
        payment = PENDING_PAYMENTS.get(payment_id)

        # Only admin should be able to approve/decline
        if query.from_user.id != ADMIN_CHAT_ID:
            await query.answer("Only admin can use this.", show_alert=True)
            return

        if not payment:
            await query.message.reply_text("⚠️ This payment request was not found or already processed.")
            return

        user_id = payment["user_id"]
        pay_type = payment["payment_type"]

        if action == "approve":
            # Send access to user
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=(
                        "✅ Your payment has been *approved*.\n\n"
                        f"Here is your access link:\n{ACCESS_LINK}\n\n"
                        "If any issue, reply here. 😊"
                    ),
                    parse_mode="Markdown",
                )
            except Exception as e:
                logging.error(f"Error sending access to user {user_id}: {e}")

            await query.message.reply_text(
                f"✅ Approved payment (ID: {payment_id}) for user `{user_id}` ({pay_type}).",
                parse_mode="Markdown",
            )

        else:  # decline
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=(
                        "❌ Your payment could not be verified.\n"
                        f"If you think this is a mistake, please send a clearer screenshot or contact support: {HELP_BOT_USERNAME}"
                    ),
                )
            except Exception as e:
                logging.error(f"Error sending decline to user {user_id}: {e}")

            await query.message.reply_text(
                f"❌ Declined payment (ID: {payment_id}) for user `{user_id}` ({pay_type}).",
                parse_mode="Markdown",
            )

        # Remove from pending after action
        PENDING_PAYMENTS.pop(payment_id, None)


async def handle_payment_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    User sends screenshot / proof after selecting any payment method.
    We forward it to admin with Approve/Decline buttons.
    """
    user = update.effective_user
    chat = update.effective_chat
    message = update.effective_message

    payment_type = context.user_data.get("waiting_for_proof")
    if not payment_type:
        # User sent random photo/document/text without choosing method
        return

    # Create a payment id based on message id (simple but okay)
    payment_id = str(message.message_id)

    # Save pending info
    PENDING_PAYMENTS[payment_id] = {
        "user_id": user.id,
        "payment_type": payment_type,
    }

    # Forward their message to admin (screenshot, etc.)
    try:
        await context.bot.forward_message(
            chat_id=ADMIN_CHAT_ID,
            from_chat_id=chat.id,
            message_id=message.message_id,
        )
    except Exception as e:
        logging.error(f"Error forwarding message to admin: {e}")

    # Send a separate message to admin with buttons
    keyboard = [
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"approve:{payment_id}"),
            InlineKeyboardButton("❌ Decline", callback_data=f"decline:{payment_id}"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

      admin_text = (
        "💰 New payment request\n\n"
        f"From: @{user.username or 'NoUsername'} (ID: {user.id})\n"
        f"Method: {payment_type.upper()}\n"
        f"Payment ID: {payment_id}\n\n"
        "Check the forwarded screenshot/message above, then tap Approve or Decline:"
    )

    try:
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=admin_text,
            reply_markup=reply_markup,
        )
    except Exception as e:
        logging.error(f"Error sending admin decision message: {e}")


    # Confirm to user
    await message.reply_text(
        "✅ Payment proof received.\n\n"
        "Please wait while we manually verify it. "
        "You will get your access link here after approval. ⏳"
    )

    # Clear waiting flag (optional)
    context.user_data["waiting_for_proof"] = None


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_buttons))

    # Photos, documents, text as proof
    app.add_handler(
        MessageHandler(
            (filters.PHOTO | filters.Document.ALL | filters.TEXT) & ~filters.COMMAND,
            handle_payment_proof,
        )
    )

    app.run_polling()


if __name__ == "__main__":
    main()




