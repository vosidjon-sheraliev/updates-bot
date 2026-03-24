#!/usr/bin/env python3
"""
Updates Bot — Private relay chat between two people.
"""

import json
import os
import random
import string
import logging
from dotenv import load_dotenv
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)

load_dotenv()
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")
DATA_FILE = "data.json"

# Conversation state
ENTER_CODE = 1

# Quick reply templates: (button label, message text)
QUICK_REPLIES = [
    ("👍 Got it!", "Got it!"),
    ("🕐 Running late", "I'm running a bit late, sorry!"),
    ("🚗 On my way", "On my way!"),
    ("📞 Call me", "Can you give me a call?"),
    ("💬 Let's talk", "Can we talk when you're free?"),
    ("✅ Done!", "Done! All finished."),
    ("❌ Can't make it", "Sorry, I can't make it."),
    ("🔜 BRB", "Be right back!"),
    ("🌙 Good night", "Good night! Talk tomorrow."),
    ("☀️ Good morning", "Good morning!"),
    ("❤️ Miss you", "Missing you ❤️"),
    ("🎯 On it", "On it, leave it to me!"),
]


# ── Storage ──────────────────────────────────────────────────────────────────

def load_data() -> dict:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {"pairs": {}, "pending_codes": {}}


def save_data(data: dict):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)


def get_partner_id(user_id: str, data: dict) -> str | None:
    return data["pairs"].get(str(user_id))


# ── Keyboards ─────────────────────────────────────────────────────────────────

def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton("⚡ Quick Replies"), KeyboardButton("📊 Status")],
         [KeyboardButton("❓ Help")]],
        resize_keyboard=True,
    )


def quick_replies_inline() -> InlineKeyboardMarkup:
    buttons, row = [], []
    for i, (label, _) in enumerate(QUICK_REPLIES):
        row.append(InlineKeyboardButton(label, callback_data=f"qr_{i}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("✖ Cancel", callback_data="qr_cancel")])
    return InlineKeyboardMarkup(buttons)


# ── /start ────────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    data = load_data()

    if get_partner_id(user_id, data):
        await update.message.reply_text(
            "✅ You're already connected!\n\nJust send a message to chat.",
            reply_markup=main_keyboard(),
        )
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔑 Generate pairing code", callback_data="gen_code")],
        [InlineKeyboardButton("🔗 Enter pairing code", callback_data="enter_code")],
    ])
    await update.message.reply_text(
        "👋 *Welcome to Updates!*\n\n"
        "This is a private channel between you and one other person.\n\n"
        "To get started:\n"
        "• *Generate a code* — share it with your partner\n"
        "• *Enter a code* — if your partner already generated one",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


# ── Pairing ───────────────────────────────────────────────────────────────────

async def handle_gen_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    data = load_data()

    if get_partner_id(user_id, data):
        await query.edit_message_text("✅ You're already paired!")
        return

    # Remove any previous pending code from this user
    data["pending_codes"] = {
        c: u for c, u in data["pending_codes"].items() if u != user_id
    }

    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    data["pending_codes"][code] = user_id
    save_data(data)

    await query.edit_message_text(
        f"🔑 *Your pairing code:*\n\n`{code}`\n\n"
        "Share this with your partner. It expires once used.\n\n"
        "_Waiting for your partner to connect..._",
        parse_mode="Markdown",
    )


async def handle_enter_code_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🔗 Type the 6-character pairing code your partner shared with you:"
    )
    return ENTER_CODE


async def receive_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    code = update.message.text.strip().upper()
    data = load_data()

    if code not in data["pending_codes"]:
        await update.message.reply_text(
            "❌ Invalid or expired code. Check the code and try again, "
            "or ask your partner to generate a new one."
        )
        return ENTER_CODE

    partner_id = data["pending_codes"][code]

    if partner_id == user_id:
        await update.message.reply_text("❌ You can't pair with yourself!")
        return ENTER_CODE

    data["pairs"][user_id] = partner_id
    data["pairs"][partner_id] = user_id
    del data["pending_codes"][code]
    save_data(data)

    await update.message.reply_text(
        "✅ *Connected!*\n\nYou're now paired. Start chatting!",
        parse_mode="Markdown",
        reply_markup=main_keyboard(),
    )
    await context.bot.send_message(
        chat_id=int(partner_id),
        text="✅ *Connected!*\n\nYour partner has joined. Start chatting!",
        parse_mode="Markdown",
        reply_markup=main_keyboard(),
    )
    return ConversationHandler.END


# ── Message relay ─────────────────────────────────────────────────────────────

async def relay_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    data = load_data()

    # Keyboard shortcut buttons
    text = update.message.text if update.message.text else ""
    if text == "⚡ Quick Replies":
        await show_quick_replies(update, context)
        return
    if text == "📊 Status":
        await status(update, context)
        return
    if text == "❓ Help":
        await help_command(update, context)
        return

    partner_id = get_partner_id(user_id, data)
    if not partner_id:
        await update.message.reply_text(
            "You're not connected to anyone. Use /start to pair with someone."
        )
        return

    name = update.effective_user.first_name or "Partner"

    try:
        msg = update.message
        pid = int(partner_id)

        if msg.text:
            await context.bot.send_message(
                chat_id=pid,
                text=f"💬 *{name}:*\n{msg.text}",
                parse_mode="Markdown",
            )
        elif msg.photo:
            await context.bot.send_photo(
                chat_id=pid,
                photo=msg.photo[-1].file_id,
                caption=f"📷 *{name}*" + (f"\n{msg.caption}" if msg.caption else ""),
                parse_mode="Markdown",
            )
        elif msg.voice:
            await context.bot.send_voice(
                chat_id=pid,
                voice=msg.voice.file_id,
                caption=f"🎤 *{name}*",
                parse_mode="Markdown",
            )
        elif msg.video:
            await context.bot.send_video(
                chat_id=pid,
                video=msg.video.file_id,
                caption=f"🎥 *{name}*" + (f"\n{msg.caption}" if msg.caption else ""),
                parse_mode="Markdown",
            )
        elif msg.video_note:
            await context.bot.send_video_note(chat_id=pid, video_note=msg.video_note.file_id)
        elif msg.document:
            await context.bot.send_document(
                chat_id=pid,
                document=msg.document.file_id,
                caption=f"📎 *{name}*" + (f"\n{msg.caption}" if msg.caption else ""),
                parse_mode="Markdown",
            )
        elif msg.sticker:
            await context.bot.send_sticker(chat_id=pid, sticker=msg.sticker.file_id)
        elif msg.audio:
            await context.bot.send_audio(
                chat_id=pid,
                audio=msg.audio.file_id,
                caption=f"🎵 *{name}*" + (f"\n{msg.caption}" if msg.caption else ""),
                parse_mode="Markdown",
            )
        elif msg.location:
            await context.bot.send_message(chat_id=pid, text=f"📍 *{name}* shared a location", parse_mode="Markdown")
            await context.bot.send_location(chat_id=pid, latitude=msg.location.latitude, longitude=msg.location.longitude)
        else:
            await update.message.reply_text("⚠️ This message type isn't supported yet.")
            return

    except Exception as e:
        logger.error(f"Relay failed: {e}")
        await update.message.reply_text(
            "⚠️ Message could not be delivered. Your partner may have blocked the bot."
        )


# ── Quick replies ─────────────────────────────────────────────────────────────

async def show_quick_replies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if not get_partner_id(user_id, load_data()):
        await update.message.reply_text("You need to be connected to use quick replies.")
        return
    await update.message.reply_text(
        "⚡ *Quick Replies* — tap to send:",
        parse_mode="Markdown",
        reply_markup=quick_replies_inline(),
    )


async def handle_quick_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "qr_cancel":
        await query.edit_message_text("Cancelled.")
        return

    index = int(query.data.split("_")[1])
    _, message = QUICK_REPLIES[index]

    user_id = str(query.from_user.id)
    data = load_data()
    partner_id = get_partner_id(user_id, data)
    if not partner_id:
        await query.edit_message_text("You're not connected to anyone.")
        return

    name = query.from_user.first_name or "Partner"
    await context.bot.send_message(
        chat_id=int(partner_id),
        text=f"💬 *{name}:*\n{message}",
        parse_mode="Markdown",
    )
    await query.edit_message_text(f"✓ Sent: _{message}_", parse_mode="Markdown")


# ── /status ───────────────────────────────────────────────────────────────────

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    partner_id = get_partner_id(user_id, load_data())

    if partner_id:
        try:
            partner = await context.bot.get_chat(int(partner_id))
            name = partner.first_name or "your partner"
            text = f"📊 *Status*\n\n✅ Connected to *{name}*\n\nUse /disconnect to unpair."
        except Exception:
            text = "📊 *Status*\n\n✅ Connected\n\nUse /disconnect to unpair."
    else:
        text = "📊 *Status*\n\n❌ Not connected\n\nUse /start to pair with someone."

    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_keyboard())


# ── /disconnect ───────────────────────────────────────────────────────────────

async def disconnect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    data = load_data()
    partner_id = get_partner_id(user_id, data)

    if not partner_id:
        await update.message.reply_text("You're not connected to anyone.")
        return

    data["pairs"].pop(user_id, None)
    data["pairs"].pop(str(partner_id), None)
    save_data(data)

    await update.message.reply_text(
        "🔌 *Disconnected.*\n\nUse /start to connect again.",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    try:
        await context.bot.send_message(
            chat_id=int(partner_id),
            text="🔌 *Your partner has disconnected.*\n\nUse /start to connect with someone new.",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove(),
        )
    except Exception:
        pass


# ── /help ─────────────────────────────────────────────────────────────────────

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❓ *Updates — Help*\n\n"
        "*Commands:*\n"
        "/start — Pair with someone\n"
        "/status — Check your connection\n"
        "/disconnect — Unpair from your partner\n"
        "/help — Show this message\n\n"
        "*Supported content:*\n"
        "Text, photos, videos, voice messages, video notes, files, stickers, audio, locations\n\n"
        "⚡ *Quick Replies* — send preset messages with one tap\n\n"
        "_All messages are private and only sent to your paired partner._",
        parse_mode="Markdown",
        reply_markup=main_keyboard(),
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(handle_enter_code_button, pattern="^enter_code$")],
        states={ENTER_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_code)]},
        fallbacks=[CommandHandler("start", start)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("disconnect", disconnect))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(handle_gen_code, pattern="^gen_code$"))
    app.add_handler(CallbackQueryHandler(handle_quick_reply, pattern="^qr_"))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, relay_message))

    logger.info("Updates Bot is running...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
