"""
telegram_bot.py - Two-way Telegram command channel (send SMS, place calls, read history).

notify_push.py pushes events OUT to Telegram; this module is the return path. A bot
long-polls getUpdates and executes chat commands, so a line can be operated from a phone
without opening the WebUI: /sms sends a message, /call rings the softphone and dials out,
/status, /messages and /calls read state back. Replying to an incoming-SMS notification in
Telegram answers that sender on that line, which is the common case.

It shares settings.telegram's bot token and proxy mode with the notification channel — one
bot, both directions — and owns no gateway logic: every action goes through a GatewayActions
implementation supplied by main.py, which calls exactly the same control-plane functions the
WebUI uses.

A chat message bypasses the WebUI login, so authorisation is the whole security story here:
an update runs only when the sender's id or the chat's id appears in
settings.telegram.commands.allowed_chat_ids. Everything else is dropped with a log line and
no reply, so an unauthorised prober learns nothing. Commands older than
MAX_COMMAND_AGE_SECONDS are ignored too: a queued "/call" must not fire hours later when the
gateway comes back up.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

from . import config as cfg
from . import notify_push

log = logging.getLogger("vowifi.telegram")

POLL_TIMEOUT_SECONDS = 30           # server-side long poll
_REQUEST_TIMEOUT = POLL_TIMEOUT_SECONDS + 15
_SEND_TIMEOUT = 8                   # a reply must never hold the poll loop for long
IDLE_POLL_SECONDS = 15              # re-read settings this often while the bot is off
MAX_COMMAND_AGE_SECONDS = 180
_MAX_REPLY_CHARS = 3500             # Telegram rejects messages over 4096
_ERROR_BACKOFF = (5, 15, 30, 60)
_NUMBER_RE = re.compile(r"^\+?\d{3,20}$")
_HISTORY_DEFAULT = 10
_HISTORY_MAX = 30


class GatewayActions:
    """What the bot needs from the control plane. main.py supplies the implementation; tests
    substitute a fake. Every method is async and must not raise for ordinary failures —
    return {"ok": False, "error": "..."} so the caller can be told what went wrong."""

    async def lines(self) -> list[dict]:
        """[{id, name, msisdn, iccid, enabled, running, state, reason}] for every line."""
        raise NotImplementedError

    async def send_sms(self, line_id: str, to: str, text: str) -> dict:
        raise NotImplementedError

    async def place_call(self, line_id: str, to: str) -> dict:
        raise NotImplementedError

    async def hangup(self, line_id: str) -> dict:
        raise NotImplementedError

    async def recent_messages(self, line_id: str, limit: int) -> list[dict]:
        raise NotImplementedError

    async def recent_calls(self, line_id: str, limit: int) -> list[dict]:
        raise NotImplementedError

    async def gateway_summary(self) -> dict:
        """{version, timezone} — identity shown by /status."""
        raise NotImplementedError

    async def record_action(self, command: str, chat_id: str, ok: bool) -> None:
        """Audit hook. Command name and outcome only: never the number or the SMS body."""
        return None


HELP = """MDD Sim Gateway bot

/status — gateway and line overview
/lines — configured lines and their ids
/sms <line> <number> <text> — send an SMS
/call <line> <number> — ring the softphone, then dial the number
/hangup <line> — end every call on the line
/messages <line> [count] — recent SMS
/calls <line> [count] — recent call log
/help — this message

<line> is a line id, name or own number; omit it when only one line is configured.
Lines are auto-named MCC-MNC, so two SIMs on one carrier can share a name — the id shown by
/lines is always unique, and an ambiguous name is refused rather than guessed.
Numbers must be full E.164 (for example +447700900123) — the carrier rejects or misroutes
a national number sent over IMS.
Replying to an incoming-SMS notification sends your reply back to that sender."""


# ----------------------------- helpers -----------------------------
def parse_ids(raw) -> list[str]:
    """Normalize the allow-list, which the WebUI edits as one comma/space separated field."""
    if isinstance(raw, str):
        raw = re.split(r"[,\s]+", raw)
    seen: list[str] = []
    for value in raw or []:
        value = str(value).strip()
        if value and value not in seen:
            seen.append(value)
    return seen


def allowed_ids(commands: dict, telegram: dict) -> set[str]:
    """Ids permitted to command the gateway. Falls back to the notification chat id, so a
    working notification setup only needs the enable switch flipped."""
    ids = set(parse_ids(commands.get("allowed_chat_ids")))
    if not ids:
        fallback = str(telegram.get("chat_id") or "").strip()
        if fallback:
            ids = {fallback}
    return ids


def is_authorised(update: dict, allowed: set[str]) -> bool:
    """A named sender may command from anywhere; a named chat authorises that chat. Both are
    explicit operator choices — listing a group id does trust everyone in that group."""
    if not allowed:
        return False
    message = update.get("message") or {}
    chat_id = str(((message.get("chat") or {}).get("id")) or "")
    sender_id = str(((message.get("from") or {}).get("id")) or "")
    return (chat_id and chat_id in allowed) or (sender_id and sender_id in allowed)


def normalize_number(value: str) -> str:
    """Accept the separators people type, reject anything that is not a dialable number."""
    cleaned = re.sub(r"[\s\-()./]", "", str(value or ""))
    return cleaned if _NUMBER_RE.fullmatch(cleaned) else ""


def match_lines(lines: list[dict], token: str) -> list[dict]:
    """Every line a token could mean, from the most specific tier that matches: id, then name,
    then own number. Only ids are unique by construction — auto-named lines are `MCC-MNC`, so
    two SIMs from one carrier share a name until an operator renames one. Returning the whole
    tier lets the caller refuse to guess instead of silently texting from the wrong SIM."""
    token = str(token or "").strip()
    if not token:
        return []
    by_id = [line for line in lines if str(line.get("id")) == token]
    if by_id:
        return by_id
    lowered = token.lower()
    by_name = [line for line in lines
               if str(line.get("name") or "").strip().lower() == lowered]
    if by_name:
        return by_name
    digits = re.sub(r"\D", "", token)
    if not digits:
        return []
    return [line for line in lines
            if re.sub(r"\D", "", str(line.get("msisdn") or "")) == digits
            and str(line.get("msisdn") or "").strip()]


def resolve_line(lines: list[dict], token: str) -> dict | None:
    """The one line a token names — None when it names none, or more than one."""
    matches = match_lines(lines, token)
    return matches[0] if len(matches) == 1 else None


def _line_label(line: dict) -> str:
    name = str(line.get("name") or "").strip() or f"line {line.get('id')}"
    msisdn = str(line.get("msisdn") or "").strip()
    return f"{name} ({msisdn})" if msisdn else name


def _format_time(ts, tz_name: str) -> str:
    try:
        tz = ZoneInfo(tz_name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        tz = timezone.utc
    try:
        return datetime.fromtimestamp(int(ts), tz).strftime("%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return "?"


def _truncate(text: str) -> str:
    return text if len(text) <= _MAX_REPLY_CHARS else text[:_MAX_REPLY_CHARS - 1] + "…"


def _history_limit(token: str | None) -> int:
    try:
        return max(1, min(_HISTORY_MAX, int(token)))
    except (TypeError, ValueError):
        return _HISTORY_DEFAULT


# ----------------------------- command dispatch -----------------------------
async def _pick_line(actions: GatewayActions, token: str | None) -> tuple[dict | None, str]:
    """Resolve the line a command targets. Returns (line, error_text)."""
    lines = await actions.lines()
    if not lines:
        return None, "No line is configured yet."
    if token:
        matches = match_lines(lines, token)
        if len(matches) == 1:
            return matches[0], ""
        if len(matches) > 1:
            # Never pick one. Sending an SMS or placing a call from the wrong SIM is not
            # something the operator can undo.
            choices = ", ".join(f"{item.get('id')} ({item.get('name') or '—'}"
                                + (f", {item['msisdn']}" if item.get("msisdn") else "") + ")"
                                for item in matches)
            return None, (f"“{token}” matches {len(matches)} lines — name one by its id: "
                          f"{choices}")
    if len(lines) == 1:
        return lines[0], ""
    names = ", ".join(f"{item.get('id')} ({item.get('name') or '—'})" for item in lines)
    if token:
        return None, f"No line matches “{token}”. Configured lines: {names}"
    return None, f"Several lines are configured — name one: {names}"


async def _cmd_status(actions: GatewayActions) -> str:
    summary = await actions.gateway_summary()
    lines = await actions.lines()
    out = [f"MDD Sim Gateway {summary.get('version') or ''}".strip(),
           f"{len(lines)} line(s) configured"]
    for line in lines:
        state = str(line.get("state") or "unknown")
        reason = str(line.get("reason") or "").strip()
        out.append(f"• {_line_label(line)} [{line.get('id')}] — {state}"
                   + (f": {reason}" if reason and state != "OK" else ""))
    return "\n".join(out)


async def _cmd_lines(actions: GatewayActions) -> str:
    lines = await actions.lines()
    if not lines:
        return "No line is configured yet."
    out = []
    for line in lines:
        flags = "running" if line.get("running") else (
            "enabled" if line.get("enabled", True) else "disabled")
        iccid = str(line.get("iccid") or "")
        out.append(f"[{line.get('id')}] {_line_label(line)} — {flags}"
                   + (f", ICCID {iccid}" if iccid else ""))
    return "\n".join(out)


async def _cmd_sms(actions: GatewayActions, rest: str) -> tuple[str, bool]:
    """Returns (reply, executed). `executed` is False when nothing was sent — a usage or
    resolution error — which keeps the audit record honest about what actually happened."""
    tokens = rest.split(None, 2)
    lines = await actions.lines()
    # Ambiguous still counts as "names a line": _pick_line then explains the ambiguity
    # instead of misreading the line name as the destination number.
    named = bool(match_lines(lines, tokens[0])) if tokens else False
    if len(tokens) < 2 or (named and len(tokens) < 3):
        return "Usage: /sms <line> <number> <text>", False
    if named:
        line_token, to_raw, body = tokens[0], tokens[1], tokens[2]
    else:
        # No leading line: the first token is the number, the rest of the line is the text.
        line_token, to_raw = None, tokens[0]
        body = rest.split(None, 1)[1]
    line, error = await _pick_line(actions, line_token)
    if not line:
        return error, False
    to = normalize_number(to_raw)
    if not to:
        return (f"“{to_raw}” is not a phone number. Use full E.164, "
                "for example +447700900123."), False
    if not body.strip():
        return "The message text is empty.", False
    return await _send(actions, line, to, body)


async def _send(actions: GatewayActions, line: dict, to: str, body: str) -> tuple[str, bool]:
    result = await actions.send_sms(str(line["id"]), to, body)
    if not result.get("ok"):
        return (f"SMS not sent on {_line_label(line)}: "
                f"{result.get('error') or 'unknown error'}"), False
    return (f"SMS accepted on {_line_label(line)} → {to}.\n"
            "The carrier confirms delivery separately; check /messages for the final status."
            ), True


async def _cmd_call(actions: GatewayActions, rest: str) -> tuple[str, bool]:
    tokens = rest.split()
    if not tokens:
        return "Usage: /call <line> <number>", False
    lines = await actions.lines()
    named = bool(match_lines(lines, tokens[0]))
    # Nothing follows the number, so re-join the rest: a number typed with spaces
    # ("+44 7700 900123") is still one number.
    line_token, to_raw = ((tokens[0], "".join(tokens[1:])) if named and len(tokens) >= 2
                          else (None, "".join(tokens)))
    line, error = await _pick_line(actions, line_token)
    if not line:
        return error, False
    to = normalize_number(to_raw)
    if not to:
        return (f"“{to_raw}” is not a phone number. Use full E.164, "
                "for example +447700900123."), False
    result = await actions.place_call(str(line["id"]), to)
    if not result.get("ok"):
        return (f"Call not placed on {_line_label(line)}: "
                f"{result.get('error') or 'unknown error'}"), False
    return (f"Calling {to} from {_line_label(line)}.\n"
            "Answer the softphone (browser or SIP client) to be bridged to the call. "
            "/hangup ends it."), True


async def _cmd_hangup(actions: GatewayActions, rest: str) -> tuple[str, bool]:
    line, error = await _pick_line(actions, (rest.split() or [None])[0])
    if not line:
        return error, False
    result = await actions.hangup(str(line["id"]))
    if not result.get("ok"):
        return (f"Hangup failed on {_line_label(line)}: "
                f"{result.get('error') or 'unknown error'}"), False
    return f"All calls on {_line_label(line)} were ended.", True


async def _history_args(actions: GatewayActions, rest: str) -> tuple[str | None, int]:
    """Split "[line] [count]" — either may be omitted. A leading token that is not a line is
    read as the count, so "/messages 5" means five, not "line 5" silently ignored."""
    tokens = rest.split()
    if tokens and match_lines(await actions.lines(), tokens[0]):
        return tokens[0], _history_limit(tokens[1] if len(tokens) > 1 else None)
    return None, _history_limit(tokens[0] if tokens else None)


async def _cmd_messages(actions: GatewayActions, rest: str, tz_name: str) -> str:
    line_token, limit = await _history_args(actions, rest)
    line, error = await _pick_line(actions, line_token)
    if not line:
        return error
    rows = await actions.recent_messages(str(line["id"]), limit)
    if not rows:
        return f"No messages on {_line_label(line)}."
    out = [f"Last {len(rows)} message(s) on {_line_label(line)}:"]
    for row in rows:
        arrow = "←" if row.get("direction") == "in" else "→"
        status = str(row.get("status") or "")
        body = " ".join(str(row.get("body") or "").split())
        out.append(f"{_format_time(row.get('ts'), tz_name)} {arrow} {row.get('peer')}"
                   + (f" [{status}]" if status and status != "ok" else "") + f"\n  {body}")
    return "\n".join(out)


async def _cmd_calls(actions: GatewayActions, rest: str, tz_name: str) -> str:
    line_token, limit = await _history_args(actions, rest)
    line, error = await _pick_line(actions, line_token)
    if not line:
        return error
    rows = await actions.recent_calls(str(line["id"]), limit)
    if not rows:
        return f"No calls on {_line_label(line)}."
    out = [f"Last {len(rows)} call(s) on {_line_label(line)}:"]
    for row in rows:
        arrow = "←" if row.get("direction") == "in" else "→"
        out.append(f"{_format_time(row.get('start_ts'), tz_name)} {arrow} {row.get('peer')}"
                   f" — {row.get('status') or '?'}")
    return "\n".join(out)


async def handle_message(actions: GatewayActions, message: dict, tz_name: str = "UTC") -> str:
    """Turn one authorised Telegram message into a reply. Returns "" when there is nothing
    to answer (plain chatter, or an unknown command in a group)."""
    text = str(message.get("text") or "").strip()
    if not text:
        return ""

    chat_id = str(((message.get("chat") or {}).get("id")) or "")

    if not text.startswith("/"):
        # A reply to an incoming-SMS notification answers that sender on that line — the
        # shortest path from "you got a text" to "text back".
        target = notify_push.reply_target(
            ((message.get("reply_to_message") or {}).get("message_id")))
        if not target:
            return ""
        # Resolve strictly: if that line is gone, say so rather than answering the stranger
        # from whichever line happens to remain.
        line = resolve_line(await actions.lines(), target.get("instance"))
        if not line:
            return "That line no longer exists — use /sms to choose one."
        reply, executed = await _send(actions, line, target["peer"], text)
        await actions.record_action("reply", chat_id, executed)
        return reply

    head, _, rest = text.partition(" ")
    command = head[1:].split("@", 1)[0].lower()   # /sms@mybot in groups
    rest = rest.strip()

    if command in {"help", "start"}:
        return HELP
    if command == "status":
        return await _cmd_status(actions)
    if command == "lines":
        return await _cmd_lines(actions)
    if command == "messages":
        return await _cmd_messages(actions, rest, tz_name)
    if command == "calls":
        return await _cmd_calls(actions, rest, tz_name)
    # Everything below changes the world, so it is audited with its real outcome.
    if command == "sms":
        reply, executed = await _cmd_sms(actions, rest)
    elif command == "call":
        reply, executed = await _cmd_call(actions, rest)
    elif command == "hangup":
        reply, executed = await _cmd_hangup(actions, rest)
    else:
        return f"Unknown command /{command}. Send /help for the command list."
    await actions.record_action(command, chat_id, executed)
    return reply


# ----------------------------- Telegram transport -----------------------------
def _offset_path() -> str:
    root = os.environ.get("MDD_DATA", os.path.join(os.getcwd(), "data"))
    return os.path.join(root, "notifications", "telegram-offset.json")


def _token_fingerprint(token: str) -> str:
    """Identify the bot without storing its token on disk."""
    return hashlib.sha256(token.encode()).hexdigest()[:16]


def load_offset(token: str) -> int | None:
    """The next update id to fetch, or None when this bot has no usable checkpoint (first
    run, or the token was replaced) and its backlog should be skipped instead of replayed."""
    try:
        with open(_offset_path(), encoding="utf-8") as handle:
            saved = json.load(handle)
    except (OSError, ValueError):
        return None
    if str(saved.get("bot") or "") != _token_fingerprint(token):
        return None
    try:
        return int(saved.get("offset"))
    except (TypeError, ValueError):
        return None


def save_offset(token: str, offset: int) -> None:
    path = _offset_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"bot": _token_fingerprint(token), "offset": int(offset)}, handle)
    except OSError:
        log.warning("could not persist the Telegram update offset")


def _api(session: requests.Session, token: str, method: str, payload: dict,
         timeout: float) -> dict:
    response = session.post(f"https://api.telegram.org/bot{token}/{method}",
                            json=payload, timeout=timeout)
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict) or not body.get("ok"):
        raise RuntimeError(f"Telegram rejected {method}")
    return body


def _log_command_failure(exc: Exception) -> None:
    """Log only the exception class; requests errors embed the bot-token URL in repr/str."""
    log.warning("Telegram command failed (%s)", type(exc).__name__)


def _get_updates(session: requests.Session, token: str, offset: int | None) -> list[dict]:
    payload: dict = {"timeout": POLL_TIMEOUT_SECONDS if offset is not None else 0,
                     "allowed_updates": ["message"]}
    # offset=-1 asks for the last update only: on a fresh checkpoint we use it to skip the
    # backlog rather than execute commands queued while the gateway was down.
    payload["offset"] = offset if offset is not None else -1
    result = _api(session, token, "getUpdates", payload, _REQUEST_TIMEOUT)["result"]
    return list(result) if isinstance(result, list) else []


def _send_reply(session: requests.Session, token: str, chat_id, text: str,
                reply_to: int | None) -> None:
    payload = {"chat_id": chat_id, "text": _truncate(text), "disable_web_page_preview": True}
    if reply_to:
        payload["reply_to_message_id"] = reply_to
    _api(session, token, "sendMessage", payload, _SEND_TIMEOUT)


async def _process(actions: GatewayActions, session: requests.Session, token: str,
                   update: dict, allowed: set[str], tz_name: str) -> None:
    message = update.get("message") or {}
    chat_id = ((message.get("chat") or {}).get("id"))
    if not is_authorised(update, allowed):
        log.warning("ignored a Telegram command from an unauthorised chat/sender")
        return
    age = time.time() - float(message.get("date") or 0)
    if age > MAX_COMMAND_AGE_SECONDS:
        log.info("ignored a Telegram command queued %.0fs ago", age)
        return
    reply = await handle_message(actions, message, tz_name)
    if reply and chat_id is not None:
        await asyncio.to_thread(_send_reply, session, token, chat_id, reply,
                                message.get("message_id"))


async def run(actions: GatewayActions) -> None:
    """Poll for commands until cancelled. Settings are re-read every cycle, so enabling the
    bot, rotating its token or switching proxy mode takes effect without a restart."""
    offset: int | None = None
    active_token = ""
    failures = 0
    while True:
        try:
            settings = cfg.get_settings()
        except Exception as exc:  # noqa: an unreadable config must not kill the channel
            log.warning("Telegram command channel could not read settings: %r", exc)
            await asyncio.sleep(IDLE_POLL_SECONDS)
            continue
        telegram = settings.get("telegram") or {}
        commands = telegram.get("commands") or {}
        token = str(telegram.get("bot_token") or "").strip()
        allowed = allowed_ids(commands, telegram)
        if not commands.get("enabled") or not token or not allowed:
            if commands.get("enabled") and token and not allowed:
                log.warning("Telegram commands are enabled but no chat id is authorised")
            active_token = ""
            await asyncio.sleep(IDLE_POLL_SECONDS)
            continue
        if token != active_token:
            active_token, offset = token, load_offset(token)
        try:
            session = await asyncio.to_thread(notify_push.telegram_session, telegram)
        except Exception as exc:  # noqa: a misconfigured/unready proxy is a normal state
            log.warning("Telegram command channel cannot build its connection: %s",
                        type(exc).__name__)
            await asyncio.sleep(IDLE_POLL_SECONDS)
            continue
        try:
            updates = await asyncio.to_thread(_get_updates, session, token, offset)
            failures = 0
            if offset is None:
                # Checkpoint only; the backlog itself is intentionally discarded.
                offset = (max(int(u["update_id"]) for u in updates) + 1) if updates else 0
                save_offset(token, offset)
                continue
            for update in sorted(updates, key=lambda u: int(u.get("update_id") or 0)):
                # Advance and persist BEFORE running the command: a crash mid-command must
                # not make the gateway send the same SMS or place the same call again.
                offset = int(update.get("update_id") or 0) + 1
                save_offset(token, offset)
                try:
                    await _process(actions, session, token, update, allowed,
                                   str(settings.get("timezone") or "UTC"))
                except Exception as exc:  # noqa: one bad command must not stop the channel
                    _log_command_failure(exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: network/API errors are expected and transient
            delay = _ERROR_BACKOFF[min(failures, len(_ERROR_BACKOFF) - 1)]
            failures += 1
            log.warning("Telegram command polling failed (%s); retrying in %ss",
                        type(exc).__name__, delay)
            await asyncio.sleep(delay)
        finally:
            await asyncio.to_thread(session.close)
