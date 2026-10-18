r"""Telegram: chat with Perro from your phone.

A bridge between a Telegram bot and Perro's inbox, run as its own process
next to the heartbeat. It does two things, over and over:

  in   your Telegram messages -> the inbox, as a knock, so Perro wakes up
       to read them (the same as `python -m perro.inbox --now "..."`)
  out  Perro's replies (message_owner) -> your Telegram chat

One command is handled here instead of passed on: /talk lifts the brakes
on Perro's email with other AI agents (see mail.py).

It also carries approvals (see approvals.py): each request Perro files
arrives as a card with Approve and Deny buttons, your tap is recorded as
your decision, and the card is rewritten with the outcome once it's settled.

Perro itself never touches Telegram. It just reads its inbox and writes
back, as before; this file is plumbing around it.

Anyone can find a Telegram bot and message it, so the bridge only listens to
one person: whoever paired with it. Pairing prints a one-time code here; you
send that code to the bot, and from then on only messages from your account,
in your private chat with the bot, count as "From the owner". Everything else
is dropped, and only the fact that a stranger tried is printed, never their
text, so nothing a stranger writes reaches Perro.

  python -m perro.telegram --pair    link the bot to your Telegram account
  python -m perro.telegram           run the bridge (Ctrl+C stops it)
  .\scripts\install-task.ps1         run it at login instead, hidden, with
                                     the heartbeat (output: home\telegram.log)

The bot's token comes from PERRO_TELEGRAM_TOKEN (get one from @BotFather).
Pairing and progress live in <home>/telegram.json, outside the workspace, so
Perro can't edit who its owner is.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from perro import approvals, inbox, mail
from perro.memory import DEFAULT_HOME, Memory

TOKEN_ENV = "PERRO_TELEGRAM_TOKEN"
# Sent by the owner, these lift the email brakes on agents instead of reaching Perro.
TALK_COMMANDS = ("/talk",)
STATE_FILE = "telegram.json"
API = "https://api.telegram.org/bot{token}/{method}"
# Long polling: Telegram holds each getUpdates request open up to this many
# seconds and answers the moment a message arrives, so messages come in
# instantly without asking every second.
POLL_SECONDS = 25
# How often to look for new replies from Perro to send out.
OUTBOX_SECONDS = 2
# After a network failure, wait this long before trying again.
RETRY_SECONDS = 10
TELEGRAM_CHAR_LIMIT = 4096


class TelegramError(Exception):
    pass


class Bot:
    """The few Telegram Bot API calls the bridge needs."""

    def __init__(self, token: str):
        self.token = token

    def call(self, method: str, http_timeout: float = 30, **params) -> object:
        url = API.format(token=self.token, method=method)
        body = json.dumps(params).encode("utf-8")
        request = urllib.request.Request(url, data=body, method="POST",
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=http_timeout) as response:
                data = json.loads(response.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:200]
            raise TelegramError(f"{method}: HTTP {e.code} {detail}") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as e:
            # The token is part of the URL; make sure it never lands in a log.
            raise TelegramError(f"{method}: {str(e).replace(self.token, '<token>')}") from None
        if not data.get("ok"):
            raise TelegramError(f"{method}: {data.get('description', 'not ok')}")
        return data["result"]

    def updates(self, offset: int) -> list[dict]:
        # The HTTP timeout outlasts Telegram's hold, so a quiet poll isn't an error.
        # callback_query is a tap on an Approve/Deny button.
        return self.call("getUpdates", http_timeout=POLL_SECONDS + 10, offset=offset,
                         timeout=POLL_SECONDS, allowed_updates=["message", "callback_query"])

    def send(self, chat_id: int, text: str) -> None:
        for start in range(0, len(text), TELEGRAM_CHAR_LIMIT):
            self.call("sendMessage", chat_id=chat_id, text=text[start:start + TELEGRAM_CHAR_LIMIT])


# --- state: who the owner is, and how far we've read -------------------------

def load_state(home: Path) -> dict:
    path = home / STATE_FILE
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def save_state(home: Path, state: dict) -> None:
    path = home / STATE_FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(path)  # all at once, so a crash can't leave half a file


def is_owner(message: dict, state: dict) -> bool:
    """A message counts as the owner's only if it's from the paired account,
    in the paired private chat."""
    return (message.get("chat", {}).get("type") == "private"
            and message.get("chat", {}).get("id") == state.get("owner_chat_id")
            and message.get("from", {}).get("id") == state.get("owner_user_id"))


# --- the two directions --------------------------------------------------------

def handle_update(update: dict, state: dict, memory: Memory, bot: Bot) -> None:
    """One incoming Telegram update: hand the owner's text to the inbox, or
    record their tap on an approval button."""
    if "callback_query" in update:
        handle_tap(update["callback_query"], state, memory, bot)
        return
    message = update.get("message")
    if not message:
        return
    if not is_owner(message, state):
        sender = message.get("from", {})
        print(f"[{_now()}] Ignored a message from outside the paired chat "
              f"(from id {sender.get('id')}, @{sender.get('username', '?')}).")
        return
    text = message.get("text")
    if not text:
        bot.send(state["owner_chat_id"], "I can only read text messages for now.")
        return
    if text.strip().lower() in TALK_COMMANDS:
        # Lifting the email brakes is the owner's call, so it's done here, in
        # code, rather than by asking Perro (see mail.py).
        mail.lift(memory)
        bot.send(state["owner_chat_id"], "Done: Perro can email the agents again, and anything they "
                                         "sent that was waiting comes through now.")
        print(f"[{_now()}] The owner lifted the email brakes.")
        return
    try:
        inbox.owner_sends(memory, text, knock=True)
    except ValueError as e:
        bot.send(state["owner_chat_id"], f"Didn't reach Perro: {e}")
        return
    print(f"[{_now()}] In: {len(text)} chars from the owner.")


def forward_replies(state: dict, memory: Memory, bot: Bot, home: Path) -> int:
    """Send Perro's new messages to the owner's chat. Returns how many."""
    inbox._table(memory)
    rows = memory.db.execute(
        "SELECT id, text FROM messages WHERE sender = 'perro' AND id > ? ORDER BY id",
        (state.get("last_forwarded_id", 0),),
    ).fetchall()
    for msg_id, text in rows:
        bot.send(state["owner_chat_id"], text)
        # Seen on Telegram counts as read, same as reading it in the terminal.
        memory.db.execute("UPDATE messages SET read_at = ? WHERE id = ? AND read_at IS NULL",
                          (_iso_now(), msg_id))
        memory.db.commit()
        state["last_forwarded_id"] = msg_id
        save_state(home, state)
        print(f"[{_now()}] Out: {len(text)} chars to the owner.")
    return len(rows)


# --- approvals: a card with two buttons, and the owner's tap -----------------
# A tap is the only thing on Telegram that approves anything. Typing "yes"
# is an ordinary message to Perro, and Perro can't approve its own requests.

def _buttons(request_id: int) -> dict:
    return {"inline_keyboard": [[{"text": "Approve", "callback_data": f"ap:{request_id}:yes"},
                                 {"text": "Deny", "callback_data": f"ap:{request_id}:no"}]]}


def forward_approvals(state: dict, memory: Memory, bot: Bot) -> None:
    """Send new approval cards, and close the ones that are settled."""
    approvals.expire(memory)
    for req in approvals.unsent(memory):
        sent = bot.call("sendMessage", chat_id=state["owner_chat_id"], text=req.text(),
                        reply_markup=_buttons(req.id))
        approvals.mark_sent(memory, req.id, sent["message_id"])
        print(f"[{_now()}] Out: approval card #{req.id}.")
    for req in approvals.to_close(memory):
        _settle_card(bot, state, req.telegram_message, req.closed_text())
        approvals.mark_closed(memory, req.id)


def _settle_card(bot: Bot, state: dict, message_id: int, text: str) -> None:
    """Rewrite a card without its buttons, so it can't be tapped again."""
    try:
        bot.call("editMessageText", chat_id=state["owner_chat_id"], message_id=message_id,
                 text=text[:TELEGRAM_CHAR_LIMIT], reply_markup={"inline_keyboard": []})
    except TelegramError as e:
        # "message is not modified" and the like: the card already says it.
        print(f"[{_now()}] Couldn't update an approval card: {e}")


def handle_tap(tap: dict, state: dict, memory: Memory, bot: Bot) -> None:
    """The owner tapped Approve or Deny. Checked as strictly as a message:
    the paired account, in the paired chat, on the card sent for that request."""
    message = tap.get("message") or {}
    parts = str(tap.get("data", "")).split(":")
    req = None
    if len(parts) == 3 and parts[0] == "ap" and parts[1].isdigit() and parts[2] in ("yes", "no"):
        req = approvals.get(memory, int(parts[1]))
    if not is_owner({"chat": message.get("chat", {}), "from": tap.get("from", {})}, state):
        print(f"[{_now()}] Ignored a button tap from outside the paired chat "
              f"(from id {tap.get('from', {}).get('id')}).")
        answer = "Not yours to answer."
    elif req is None or req.telegram_message != message.get("message_id"):
        answer = "That button doesn't match a request."
    else:
        answer = approvals.decide(memory, req.id, parts[2] == "yes", "owner on Telegram")
        print(f"[{_now()}] Tap: {answer}")
        req = approvals.get(memory, req.id)
        if req.status in ("approved", "running"):
            _settle_card(bot, state, req.telegram_message, req.closed_text())
    try:
        bot.call("answerCallbackQuery", callback_query_id=tap["id"], text=answer[:200])
    except (TelegramError, KeyError) as e:
        print(f"[{_now()}] Couldn't answer a button tap: {e}")


def _start_point(memory: Memory) -> int:
    """Newest Perro message so far, so a fresh pairing doesn't replay history."""
    inbox._table(memory)
    row = memory.db.execute("SELECT MAX(id) FROM messages WHERE sender = 'perro'").fetchone()
    return row[0] or 0


# --- commands --------------------------------------------------------------------

def pair(home: Path, bot: Bot) -> None:
    code = f"{secrets.randbelow(10**6):06d}"
    me = bot.call("getMe")
    print(f"Open Telegram, find @{me['username']}, and send it this code: {code}")
    print("Waiting (Ctrl+C to give up)...")
    state = load_state(home)
    offset = state.get("update_offset", 0)
    while True:
        for update in bot.updates(offset):
            offset = update["update_id"] + 1
            message = update.get("message") or {}
            if (message.get("chat", {}).get("type") == "private"
                    and (message.get("text") or "").strip() == code):
                memory = Memory(home)
                try:
                    state.update(owner_chat_id=message["chat"]["id"],
                                 owner_user_id=message["from"]["id"],
                                 update_offset=offset,
                                 last_forwarded_id=_start_point(memory))
                finally:
                    memory.close()
                save_state(home, state)
                bot.send(state["owner_chat_id"], "Paired. Messages here now go to Perro.")
                print(f"Paired with @{message['from'].get('username', '?')}.")
                return
        state["update_offset"] = offset
        save_state(home, state)


def run(home: Path, bot: Bot) -> None:
    state = load_state(home)
    if "owner_chat_id" not in state:
        sys.exit("Not paired yet. Run: python -m perro.telegram --pair")
    stop = threading.Event()

    def outbox_loop() -> None:
        # Its own database connection: SQLite connections stay in one thread.
        memory = Memory(home)
        try:
            while not stop.is_set():
                try:
                    forward_replies(state, memory, bot, home)
                    forward_approvals(state, memory, bot)
                except TelegramError as e:
                    print(f"[{_now()}] Couldn't send: {e}")
                    stop.wait(RETRY_SECONDS)
                stop.wait(OUTBOX_SECONDS)
        finally:
            memory.close()

    sender = threading.Thread(target=outbox_loop, daemon=True)
    sender.start()
    memory = Memory(home)
    print(f"[{_now()}] Bridge running. Ctrl+C to stop.")
    try:
        while True:
            try:
                updates = bot.updates(state.get("update_offset", 0))
            except TelegramError as e:
                print(f"[{_now()}] Couldn't fetch messages: {e}")
                time.sleep(RETRY_SECONDS)
                continue
            for update in updates:
                handle_update(update, state, memory, bot)
                state["update_offset"] = update["update_id"] + 1
                save_state(home, state)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        stop.set()
        memory.close()


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _now() -> str:
    return f"{datetime.now():%Y-%m-%d %H:%M:%S}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Chat with Perro over Telegram.")
    parser.add_argument("--pair", action="store_true", help="link the bot to your account")
    parser.add_argument("--home", type=Path, default=DEFAULT_HOME)
    parser.add_argument("--log", action="store_true",
                        help="write output to <home>/telegram.log (for running hidden)")
    args = parser.parse_args()
    if args.log:
        # Run hidden (pythonw) there's no console, so output and any crash
        # traceback go to a file instead.
        args.home.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = open(args.home / "telegram.log", "a",
                                       encoding="utf-8", errors="replace", buffering=1)
    else:
        # Some Windows consoles aren't UTF-8; printing should never crash it.
        # (Only with a console: hidden under pythonw, sys.stdout is None.)
        sys.stdout.reconfigure(errors="replace")
    token = os.environ.get(TOKEN_ENV)
    if not token:
        sys.exit(f"{TOKEN_ENV} is not set. Get a token from @BotFather; see README.md.")
    bot = Bot(token)
    args.home.mkdir(parents=True, exist_ok=True)
    try:
        if args.pair:
            pair(args.home, bot)
        else:
            run(args.home, bot)
    except TelegramError as e:
        sys.exit(f"Telegram said no: {e}")
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
