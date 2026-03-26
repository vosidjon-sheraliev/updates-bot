#!/usr/bin/env python3
"""
Updates Bot — Private relay chat: admin (@vosidjonn) ↔ approved user (@farangis_f23)
No database or file storage needed — everything runs in memory.
"""

import html
import logging
import os
from datetime import datetime, timezone, timedelta
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
    filters,
    ContextTypes,
)

load_dotenv()
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN            = os.getenv("BOT_TOKEN")
ADMIN_USERNAME   = os.getenv("ADMIN_USERNAME", "vosidjonn").lstrip("@").lower()
ALLOWED_USERNAME = os.getenv("ALLOWED_USERNAME", "farangis_f23").lstrip("@").lower()

# In-memory state (resets on bot restart — admin just clicks Approve once)
state = {
    "admin_id":   None,   # filled when @vosidjonn sends /start
    "allowed_id": None,   # filled when @farangis_f23 sends /start
    "approved":   False,  # set to True when admin approves
}

QUICK_REPLIES = [
    ("👍 Got it!",       "Got it!"),
    ("🕐 Running late",  "I'm running a bit late, sorry!"),
    ("🚗 On my way",     "On my way!"),
    ("📞 Call me",       "Can you give me a call?"),
    ("💬 Let's talk",    "Can we talk when you're free?"),
    ("✅ Done!",         "Done! All finished."),
    ("❌ Can't make it", "Sorry, I can't make it."),
    ("🔜 BRB",           "Be right back!"),
    ("🌙 Good night",    "Good night! Talk tomorrow."),
    ("☀️ Good morning",  "Good morning!"),
    ("❤️ Miss you",      "Missing you ❤️"),
    ("🎯 On it",         "On it, leave it to me!"),
    ("🏠 At Home",       "I'm at home!"),
]

TASHKENT = timezone(timedelta(hours=5))


# ── Helpers ────────────────────────────────────────────────────────────────────

def e(text: str) -> str:
    return html.escape(str(text))

def fmt_time(dt: datetime) -> str:
    local = dt.astimezone(TASHKENT)
    now   = datetime.now(TASHKENT)
    if local.date() == now.date():
        return f"<i>⏱ {local.strftime('%H:%M')}</i>"
    return f"<i>⏱ {local.strftime('%d %b %H:%M')}</i>"

def fmt_quote(reply_msg) -> str:
    if not reply_msg:
        return ""
    if reply_msg.text:
        p = e(reply_msg.text[:60] + ("…" if len(reply_msg.text) > 60 else ""))
    elif reply_msg.photo:       p = "📷 Photo"
    elif reply_msg.voice:       p = "🎤 Voice"
    elif reply_msg.video:       p = "🎥 Video"
    elif reply_msg.video_note:  p = "🎥 Video note"
    elif reply_msg.document:    p = "📎 File"
    elif reply_msg.sticker:     p = f"{reply_msg.sticker.emoji} Sticker" if reply_msg.sticker.emoji else "Sticker"
    elif reply_msg.audio:       p = "🎵 Audio"
    elif reply_msg.location:    p = "📍 Location"
    else:                       p = "Message"
    return f"┊ <i>{p}</i>\n"

def username_of(user) -> str:
    return (user.username or "").lower()

def is_admin(user)   -> bool: return username_of(user) == ADMIN_USERNAME
def is_allowed(user) -> bool: return username_of(user) == ALLOWED_USERNAME
def is_auth(user)    -> bool: return is_admin(user) or is_allowed(user)

def register(user):
    if is_admin(user)   and not state["admin_id"]:
        state["admin_id"]   = user.id
    if is_allowed(user) and not state["allowed_id"]:
        state["allowed_id"] = user.id

def main_kb() -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton("📊 Status"), KeyboardButton("❓ Help")]]
    btns = [KeyboardButton(lbl) for lbl, _ in QUICK_REPLIES]
    for i in range(0, len(btns), 2):
        rows.append(btns[i:i+2])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def approve_kb(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"approve_{uid}"),
        InlineKeyboardButton("❌ Deny",    callback_data=f"deny_{uid}"),
    ]])


# ── /start ─────────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_auth(user):
        await update.message.reply_text("⛔ You are not authorised to use this bot.")
        return

    register(user)

    if is_admin(user):
        await update.message.reply_text(
            f"👑 <b>Welcome, admin!</b>\n\n"
            f"Commands:\n"
            f"/approve — Approve @{ALLOWED_USERNAME}\n"
            f"/revoke  — Revoke access\n"
            f"/status  — See current state\n\n"
            f"Just type to chat.",
            parse_mode="HTML", reply_markup=main_kb(),
        )
        return

    # Allowed user
    if state["approved"]:
        await update.message.reply_text("✅ You're approved! Just send a message.", reply_markup=main_kb())
        return

    admin_id = state["admin_id"]
    if not admin_id:
        await update.message.reply_text(
            "⏳ The admin hasn't started the bot yet. Ask @" + ADMIN_USERNAME + " to open the bot first."
        )
        return

    name  = e(user.first_name or user.username or "Someone")
    uname = f"@{user.username}" if user.username else f"(ID: {user.id})"
    try:
        await context.bot.send_message(
            chat_id=admin_id,
            text=f"🔔 <b>Access request</b>\n\n{name} ({uname}) wants to chat.\n\nApprove or deny:",
            parse_mode="HTML",
            reply_markup=approve_kb(user.id),
        )
        await update.message.reply_text(
            "⏳ Approval request sent to the admin. You'll be notified when approved.",
            parse_mode="HTML",
        )
    except Exception as ex:
        logger.error(f"Admin notify failed: {ex}")
        await update.message.reply_text("⚠️ Could not reach the admin right now. Try again later.")


# ── Approve / Deny button ──────────────────────────────────────────────────────

async def on_approval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user):
        await query.answer("Only the admin can do this.", show_alert=True)
        return

    action, uid_str = query.data.split("_", 1)
    uid = int(uid_str)

    if action == "approve":
        state["approved"]   = True
        state["allowed_id"] = uid
        await query.edit_message_text("✅ Access <b>approved.</b>", parse_mode="HTML")
        try:
            await context.bot.send_message(
                chat_id=uid,
                text="✅ <b>Approved!</b> You can now chat — just send a message!",
                parse_mode="HTML", reply_markup=main_kb(),
            )
        except Exception as ex:
            logger.error(f"Could not notify allowed user: {ex}")
    else:
        state["approved"] = False
        await query.edit_message_text("❌ Access <b>denied.</b>", parse_mode="HTML")
        try:
            await context.bot.send_message(chat_id=uid, text="❌ Your access request was denied.")
        except Exception:
            pass


# ── /approve  /revoke ──────────────────────────────────────────────────────────

async def cmd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user): return
    state["approved"] = True
    await update.message.reply_text(f"✅ @{ALLOWED_USERNAME} is approved.")
    if state["allowed_id"]:
        try:
            await context.bot.send_message(
                chat_id=state["allowed_id"],
                text="✅ <b>Access granted!</b> You can now send messages.",
                parse_mode="HTML", reply_markup=main_kb(),
            )
        except Exception: pass

async def cmd_revoke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user): return
    state["approved"] = False
    await update.message.reply_text(f"🔒 @{ALLOWED_USERNAME}'s access revoked.")
    if state["allowed_id"]:
        try:
            await context.bot.send_message(
                chat_id=state["allowed_id"],
                text="🔒 Your access has been revoked.",
                reply_markup=ReplyKeyboardRemove(),
            )
        except Exception: pass


# ── /status ────────────────────────────────────────────────────────────────────

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_auth(user): return
    if is_admin(user):
        st = "✅ Approved" if state["approved"] else "❌ Not approved"
        text = (
            f"📊 <b>Admin Status</b>\n\n"
            f"@{ALLOWED_USERNAME}: {st}\n"
            f"/approve — grant  |  /revoke — remove"
        )
    else:
        text = "📊 ✅ Approved — you can chat." if state["approved"] else "📊 ⏳ Waiting for admin approval."
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=main_kb())


# ── /help ──────────────────────────────────────────────────────────────────────

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_auth(user): return
    admin_part = (
        f"\n<b>Admin commands:</b>\n"
        f"/approve — grant access to @{ALLOWED_USERNAME}\n"
        f"/revoke  — remove access\n"
    ) if is_admin(user) else ""
    await update.message.reply_text(
        "❓ <b>Help</b>\n\n"
        "/start — Start / request access\n"
        "/status — Check status\n"
        "/help — This message\n"
        + admin_part +
        "\n<b>Supports:</b> text, photos, videos, voice, files, stickers, locations\n\n"
        "<i>Messages are private — only between admin and approved user.</i>",
        parse_mode="HTML", reply_markup=main_kb(),
    )


# ── Message relay ──────────────────────────────────────────────────────────────

async def relay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_auth(user):
        await update.message.reply_text("⛔ You are not authorised to use this bot.")
        return

    register(user)

    text = update.message.text or ""
    if text == "📊 Status": await status(update, context); return
    if text == "❓ Help":   await help_command(update, context); return

    if is_allowed(user) and not state["approved"]:
        await update.message.reply_text("⏳ You're not approved yet. Use /start to request access.")
        return

    partner_id = state["allowed_id"] if is_admin(user) else state["admin_id"]
    if not partner_id:
        await update.message.reply_text("⚠️ Partner hasn't started the bot yet.")
        return

    qr_map = {lbl: msg for lbl, msg in QUICK_REPLIES}
    if text in qr_map:
        name = e(user.first_name or "Partner")
        ts   = fmt_time(update.message.date)
        await context.bot.send_message(
            chat_id=partner_id,
            text=f"💬 <b>{name}:</b>\n{e(qr_map[text])}\n\n{ts}", parse_mode="HTML",
        )
        await update.message.reply_text(f"✓ Sent: <i>{e(qr_map[text])}</i>", parse_mode="HTML")
        return

    msg   = update.message
    name  = e(user.first_name or "Partner")
    ts    = fmt_time(msg.date)
    quote = fmt_quote(msg.reply_to_message)

    try:
        if msg.text:
            await context.bot.send_message(
                chat_id=partner_id,
                text=f"💬 <b>{name}:</b>\n{quote}{e(msg.text)}\n\n{ts}", parse_mode="HTML",
            )
        elif msg.photo:
            cap = f"📷 <b>{name}</b>\n{quote}" + (e(msg.caption) if msg.caption else "") + f"\n\n{ts}"
            await context.bot.send_photo(chat_id=partner_id, photo=msg.photo[-1].file_id, caption=cap, parse_mode="HTML")
        elif msg.voice:
            await context.bot.send_voice(chat_id=partner_id, voice=msg.voice.file_id,
                caption=f"🎤 <b>{name}</b>\n{quote}\n{ts}", parse_mode="HTML")
        elif msg.video:
            cap = f"🎥 <b>{name}</b>\n{quote}" + (e(msg.caption) if msg.caption else "") + f"\n\n{ts}"
            await context.bot.send_video(chat_id=partner_id, video=msg.video.file_id, caption=cap, parse_mode="HTML")
        elif msg.video_note:
            await context.bot.send_video_note(chat_id=partner_id, video_note=msg.video_note.file_id)
            await context.bot.send_message(chat_id=partner_id, text=f"🎥 <b>{name}</b>\n{quote}{ts}", parse_mode="HTML")
        elif msg.document:
            cap = f"📎 <b>{name}</b>\n{quote}" + (e(msg.caption) if msg.caption else "") + f"\n\n{ts}"
            await context.bot.send_document(chat_id=partner_id, document=msg.document.file_id, caption=cap, parse_mode="HTML")
        elif msg.sticker:
            await context.bot.send_sticker(chat_id=partner_id, sticker=msg.sticker.file_id)
            await context.bot.send_message(chat_id=partner_id, text=f"<b>{name}</b>\n{quote}{ts}", parse_mode="HTML")
        elif msg.audio:
            cap = f"🎵 <b>{name}</b>\n{quote}" + (e(msg.caption) if msg.caption else "") + f"\n\n{ts}"
            await context.bot.send_audio(chat_id=partner_id, audio=msg.audio.file_id, caption=cap, parse_mode="HTML")
        elif msg.location:
            await context.bot.send_message(chat_id=partner_id,
                text=f"📍 <b>{name}</b> shared a location\n{quote}\n{ts}", parse_mode="HTML")
            await context.bot.send_location(chat_id=partner_id,
                latitude=msg.location.latitude, longitude=msg.location.longitude)
        else:
            await update.message.reply_text("⚠️ This message type isn't supported yet.")
    except Exception as ex:
        logger.error(f"Relay error: {ex}")
        await update.message.reply_text(f"⚠️ Could not deliver. Error: {ex}")


# ── Edit relay ─────────────────────────────────────────────────────────────────

async def relay_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_auth(user): return
    if is_allowed(user) and not state["approved"]: return
    partner_id = state["allowed_id"] if is_admin(user) else state["admin_id"]
    if not partner_id: return
    msg = update.edited_message
    if not msg or not msg.text: return
    name = e(user.first_name or "Partner")
    ts   = fmt_time(msg.edit_date or msg.date)
    await context.bot.send_message(
        chat_id=partner_id,
        text=f"✏️ <b>{name}</b> edited:\n{e(msg.text)}\n\n{ts}", parse_mode="HTML",
    )


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",   start))
    app.add_handler(CommandHandler("status",  status))
    app.add_handler(CommandHandler("approve", cmd_approve))
    app.add_handler(CommandHandler("revoke",  cmd_revoke))
    app.add_handler(CommandHandler("help",    help_command))
    app.add_handler(CallbackQueryHandler(on_approval, pattern="^(approve|deny)_"))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, relay))
    app.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE, relay_edit))
    logger.info("Bot running...")
    app.run_polling(drop_pending_updates=False)

if __name__ == "__main__":
    main()
