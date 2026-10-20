r"""Telegram: chat with Ahab from your phone.

A bridge between a Telegram bot and Ahab's inbox, run as its own process
next to the heartbeat. It does two things, over and over:

  in   your Telegram messages -> the inbox, as a knock, so Ahab wakes up
       to read them (the same as `python -m ahab.inbox --now "..."`).
       Photos, files and voice notes are saved in Ahab's workspace under
       media/ first, and voice notes transcribed (see senses.py).
  out  Ahab's replies (message_owner) -> your Telegram chat, with any
       files it attached

One command is handled here instead of passed on: /talk lifts the brakes
on Ahab's email with other AI agents (see mail.py).

It also carries approvals (see approvals.py): each request Ahab files
arrives as a card with Approve and Deny buttons, your tap is recorded as
your decision, and the card is rewritten with the outcome once it's settled.

Ahab itself never touches Telegram. It just reads its inbox and writes
back, as before; this file is plumbing around it.

Anyone can find a Telegram bot and message it, so the bridge only listens to
one person: whoever paired with it. Pairing prints a one-time code here; you
send that code to the bot, and from then on only messages from your account,
in your private chat with the bot, count as "From the owner". Everything else
is dropped, and only the fact that a stranger tried is printed, never their
text, so nothing a stranger writes reaches Ahab.

  python -m ahab.telegram --pair    link the bot to your Telegram account
  python -m ahab.telegram           run the bridge (Ctrl+C stops it)
  .\scripts\install-task.ps1         run it at login instead, hidden, with
                                     the heartbeat (output: home\telegram.log)

The bot's token comes from AHAB_TELEGRAM_TOKEN (get one from @BotFather).
Pairing and progress live in <home>/telegram.json, outside the workspace, so
Ahab can't edit who its owner is.
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

from ahab import approvals, inbox, mail, senses, tools, watchdog
from ahab.memory import DEFAULT_HOME, Memory

TOKEN_ENV = "AHAB_TELEGRAM_TOKEN"
# Sent by the owner, these lift the email brakes on agents instead of reaching Ahab.
TALK_COMMANDS = ("/talk",)
STATE_FILE = "telegram.json"
API = "https://api.telegram.org/bot{token}/{method}"
# Long polling: Telegram holds each getUpdates request open up to this many
# seconds and answers the moment a message arrives, so messages come in
# instantly without asking every second.
POLL_SECONDS = 25
# How often to look for new replies from Ahab to send out.
OUTBOX_SECONDS = 2
# After a network failure, wait this long before trying again.
RETRY_SECONDS = 10
TELEGRAM_CHAR_LIMIT = 4096
CAPTION_CHAR_LIMIT = 1024
FILE_API = "https://api.telegram.org/file/bot{token}/{path}"
# The most a bot may download, and the most it may send as a photo or a file.
DOWNLOAD_BYTE_LIMIT = 20 * 1024 * 1024
PHOTO_BYTE_LIMIT = 10 * 1024 * 1024
UPLOAD_BYTE_LIMIT = 50 * 1024 * 1024
# A long voice note's words go in the message up to this; all of them are
# kept in the .txt next to the recording.
SPOKEN_CHAR_LIMIT = 12_000
# What a message without text can carry, as Telegram names it, in the words
# Ahab hears.
NON_TEXT = {"photo": "a photo", "sticker": "a sticker", "voice": "a voice message",
            "video_note": "a video message", "video": "a video", "animation": "a GIF",
            "audio": "an audio file", "document": "a file", "location": "a location",
            "venue": "a place", "contact": "a contact", "poll": "a poll"}
BRIDGE = "[From the Telegram bridge: "


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

    def download(self, file_id: str) -> bytes:
        """A file someone sent the bot (the owner, since nobody else gets this far)."""
        info = self.call("getFile", file_id=file_id)
        if (info.get("file_size") or 0) > DOWNLOAD_BYTE_LIMIT or not info.get("file_path"):
            raise TelegramError(f"it's over {DOWNLOAD_BYTE_LIMIT // 1024 // 1024} MB, more than a bot may download")
        url = FILE_API.format(token=self.token, path=info["file_path"])
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                data = response.read(DOWNLOAD_BYTE_LIMIT + 1)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise TelegramError(f"download: {str(e).replace(self.token, '<token>')}") from None
        if len(data) > DOWNLOAD_BYTE_LIMIT:
            raise TelegramError("it's too big to download")
        return data

    def upload(self, method: str, field: str, path: Path, **params) -> object:
        """sendPhoto or sendDocument: one file from Ahab's workspace, as a form upload."""
        boundary = secrets.token_hex(16)
        parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                 for k, v in params.items()]
        safe_name = "".join(c for c in path.name if c.isalnum() or c in "._-") or "file"
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{safe_name}"\r\n'
                     f"Content-Type: application/octet-stream\r\n\r\n".encode() + path.read_bytes() + b"\r\n")
        body = b"".join(parts) + f"--{boundary}--\r\n".encode()
        request = urllib.request.Request(API.format(token=self.token, method=method), data=body, method="POST",
                                         headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                data = json.loads(response.read())
        except urllib.error.HTTPError as e:
            raise TelegramError(f"{method}: HTTP {e.code} {e.read().decode('utf-8', 'replace')[:200]}") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as e:
            raise TelegramError(f"{method}: {str(e).replace(self.token, '<token>')}") from None
        if not data.get("ok"):
            raise TelegramError(f"{method}: {data.get('description', 'not ok')}")
        return data["result"]


# --- state: who the owner is, and how far we've read -------------------------

def load_state(home: Path) -> dict:
    path = home / STATE_FILE
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


# The bridge's two threads share one state dict and one file; this keeps a
# change and its save together, so neither thread writes over the other.
_STATE_LOCK = threading.RLock()


def save_state(home: Path, state: dict, **changes) -> None:
    """Apply changes (if any) to state and save it, as one step."""
    with _STATE_LOCK:
        state.update(changes)
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
        handle_media(message, state, memory, bot)
        return
    if text.strip().lower() in TALK_COMMANDS:
        # Lifting the email brakes is the owner's call, so it's done here, in
        # code, rather than by asking Ahab (see mail.py).
        mail.lift(memory)
        bot.send(state["owner_chat_id"], "Done: Ahab can email the agents again, and anything they "
                                         "sent that was waiting comes through now.")
        print(f"[{_now()}] The owner lifted the email brakes.")
        return
    try:
        inbox.owner_sends(memory, text, knock=True)
    except ValueError as e:
        bot.send(state["owner_chat_id"], f"Didn't reach Ahab: {e}")
        return
    print(f"[{_now()}] In: {len(text)} chars from the owner.")


# --- what isn't text: saved in Ahab's workspace, then seen, read or heard ---

def handle_media(message: dict, state: dict, memory: Memory, bot: Bot) -> None:
    """A photo, voice note, file and the rest: saved to media/ (senses.py),
    and passed to the inbox as the owner's caption plus a note saying what
    came and where it is. Voice notes are transcribed here, before Ahab
    wakes, so their words reach it like typed ones."""
    kind = next((NON_TEXT[k] for k in NON_TEXT if k in message), "a message that isn't text")
    caption = (message.get("caption") or "").strip()
    try:
        note, files = take_in(message, memory, bot)
    except (TelegramError, senses.SenseError) as e:
        note, files = f"{BRIDGE}the owner sent {kind}, but it couldn't be saved: {e}]", []
        bot.send(state["owner_chat_id"], f"Ahab has been told you sent {kind}, but it couldn't be saved: {e}")
    body = f"{caption}\n{note}" if caption else note
    try:
        inbox.owner_sends(memory, body, knock=True, attachments=files)
    except ValueError:   # a caption plus a long transcript over the limit
        inbox.owner_sends(memory, note[:inbox.MESSAGE_CHAR_LIMIT - 100] + "...]", knock=True, attachments=files)
    print(f"[{_now()}] In: {kind} from the owner" + (f", saved as {', '.join(files)}." if files else "."))


def _stamp() -> str:
    return f"{datetime.now():%H%M%S}"


def _keep(memory: Memory, bot: Bot, file_id: str, name: str) -> str:
    return senses.save(memory, bot.download(file_id), name)


def _heard(memory: Memory, rel: str, what: str) -> str:
    """A recording's words for the note, transcribed now and kept next to it
    the way the transcribe tool keeps them."""
    path = tools.workspace_root(memory) / rel
    try:
        said, who = senses.transcribe(path)
    except senses.SenseError as e:
        return f"{what}, saved as {rel}. It couldn't be transcribed: {e}]"
    path.with_name(path.name + ".txt").write_text(f"{who}\n{said}", encoding="utf-8")
    if len(said) > SPOKEN_CHAR_LIMIT:
        said = said[:SPOKEN_CHAR_LIMIT] + f" [...the rest is in {rel}.txt]"
    return (f"{what}, saved as {rel}, transcribed by {who} (machine transcription: it can mishear). "
            f"What they said:]\n{said or '(nothing audible)'}")


def take_in(message: dict, memory: Memory, bot: Bot) -> tuple[str, list[str]]:
    """(the note Ahab reads, the workspace paths of what was saved)."""
    stamp = _stamp()
    if "photo" in message:
        rel = _keep(memory, bot, message["photo"][-1]["file_id"], f"photo-{stamp}.jpg")   # the largest size
        return f"{BRIDGE}the owner sent a photo, saved as {rel} and shown to you with this page.]", [rel]
    if "voice" in message:
        rel = _keep(memory, bot, message["voice"]["file_id"], f"voice-{stamp}.ogg")
        seconds = message["voice"].get("duration")
        what = f"{BRIDGE}a voice message from the owner" + (f" ({seconds} s)" if seconds else "")
        return _heard(memory, rel, what), [rel]
    for key, label in (("video_note", "a video message"), ("video", "a video"), ("animation", "a GIF")):
        if key in message:
            clip, files, parts = message[key], [], []
            thumb = clip.get("thumbnail") or clip.get("thumb")
            if thumb:
                still = _keep(memory, bot, thumb["file_id"], f"{key}-{stamp}-still.jpg")
                files.append(still)
                parts.append(f"a still from it is saved as {still} and shown to you with this page")
            if key != "animation" and (clip.get("file_size") or 0) <= DOWNLOAD_BYTE_LIMIT:
                rel = _keep(memory, bot, clip["file_id"], f"{key}-{stamp}.mp4")
                files.append(rel)
                # You can't watch video, but you can hear it.
                return (_heard(memory, rel, f"{BRIDGE}{label} from the owner ({'; '.join(parts) or 'no still'}); "
                               f"you can't watch video, but its sound"), files)
            parts.append("you can't watch it")
            return f"{BRIDGE}the owner sent {label}: {'; '.join(parts)}.]", files
    if "document" in message or "audio" in message:
        doc = message.get("document") or message["audio"]
        if "document" in message:
            name = doc.get("file_name") or f"file-{stamp}"
        else:
            name = doc.get("file_name") or f"{doc.get('title') or 'audio'}-{stamp}.mp3"
        rel = _keep(memory, bot, doc["file_id"], name)
        sort = senses.kind(Path(rel))
        how = {"image": "it's shown to you with this page",
               "document": "read_file reads it",
               "audio": "transcribe turns what's said in it into text",
               "video": "you can't watch it, but transcribe gives you what's said in it",
               }.get(sort, "read_file reads it if it's text")
        return (f'{BRIDGE}the owner sent a file, "{doc.get("file_name") or name}" '
                f"({senses.size_text(doc.get('file_size') or 0)}), saved as {rel}; {how}.]"), [rel]
    if "location" in message or "venue" in message:
        venue = message.get("venue") or {}
        place = venue.get("location") or message["location"]
        lat, lon = place.get("latitude"), place.get("longitude")
        named = f"{venue.get('title')}, {venue.get('address')}: " if venue else ""
        return (f"{BRIDGE}the owner shared a location: {named}{lat}, {lon} "
                f"(https://www.openstreetmap.org/?mlat={lat}&mlon={lon}).]"), []
    if "contact" in message:
        c = message["contact"]
        name = " ".join(x for x in (c.get("first_name"), c.get("last_name")) if x)
        return f"{BRIDGE}the owner shared a contact: {name or '(no name)'}, {c.get('phone_number', 'no number')}.]", []
    if "sticker" in message:
        return f"{BRIDGE}the owner sent a sticker: {message['sticker'].get('emoji') or '(no emoji)'}.]", []
    if "poll" in message:
        poll = message["poll"]
        options = "; ".join(o.get("text", "") for o in poll.get("options") or [])
        return f"{BRIDGE}the owner sent a poll: {poll.get('question')} Options: {options}.]", []
    return f"{BRIDGE}the owner sent a message that isn't text, of a kind that can't reach you.]", []


def _send_file(bot: Bot, state: dict, home: Path, rel: str) -> None:
    """One file Ahab attached, as a picture if it's a small image, else as a file."""
    path = home / "workspace" / rel
    chat = state["owner_chat_id"]
    if not path.is_file():
        bot.send(chat, f"(Ahab attached {rel}, but it's gone.)")
        return
    size = path.stat().st_size
    if size > UPLOAD_BYTE_LIMIT:
        bot.send(chat, f"(Ahab attached {rel}, but it's over {UPLOAD_BYTE_LIMIT // 1024 // 1024} MB.)")
    elif senses.kind(path) == "image" and size <= PHOTO_BYTE_LIMIT:
        bot.upload("sendPhoto", "photo", path, chat_id=chat)
    else:
        bot.upload("sendDocument", "document", path, chat_id=chat)


def forward_replies(state: dict, memory: Memory, bot: Bot, home: Path) -> int:
    """Send Ahab's new messages to the owner's chat. Returns how many."""
    inbox._table(memory)
    rows = memory.db.execute(
        "SELECT id, text, attachments FROM messages WHERE sender = 'ahab' AND id > ? ORDER BY id",
        (state.get("last_forwarded_id", 0),),
    ).fetchall()
    for msg_id, text, files in rows:
        bot.send(state["owner_chat_id"], text)
        for rel in inbox.files_of(files):
            try:
                _send_file(bot, state, home, rel)
            except TelegramError as e:
                # Said once and moved on: retrying would send the text again too.
                print(f"[{_now()}] Couldn't send {rel}: {e}")
                try:
                    bot.send(state["owner_chat_id"], f"(Ahab attached {rel}, but it couldn't be sent: {e})")
                except TelegramError:
                    pass
        # Seen on Telegram counts as read, same as reading it in the terminal.
        memory.db.execute("UPDATE messages SET read_at = ? WHERE id = ? AND read_at IS NULL",
                          (_iso_now(), msg_id))
        memory.db.commit()
        save_state(home, state, last_forwarded_id=msg_id)
        print(f"[{_now()}] Out: {len(text)} chars to the owner.")
    return len(rows)


# --- approvals: a card with two buttons, and the owner's tap -----------------
# A tap is the only thing on Telegram that approves anything. Typing "yes"
# is an ordinary message to Ahab, and Ahab can't approve its own requests.

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
    """Newest Ahab message so far, so a fresh pairing doesn't replay history."""
    inbox._table(memory)
    row = memory.db.execute("SELECT MAX(id) FROM messages WHERE sender = 'ahab'").fetchone()
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
                bot.send(state["owner_chat_id"], "Paired. Messages here now go to Ahab.")
                print(f"Paired with @{message['from'].get('username', '?')}.")
                return
        save_state(home, state, update_offset=offset)


def run(home: Path, bot: Bot) -> None:
    state = load_state(home)
    if "owner_chat_id" not in state:
        sys.exit("Not paired yet. Run: python -m ahab.telegram --pair")
    watchdog.keep_alive(home, "telegram")
    stop = threading.Event()
    sender_failed: list[BaseException] = []

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
        except Exception as e:
            # Anything else would end this thread quietly while the bridge
            # kept taking messages in and sending nothing out.
            sender_failed.append(e)
            stop.set()
        finally:
            memory.close()

    sender = threading.Thread(target=outbox_loop, daemon=True)
    sender.start()
    memory = Memory(home)
    print(f"[{_now()}] Bridge running. Ctrl+C to stop.")
    try:
        while True:
            if sender_failed:
                # Exit loudly; the scheduled task starts the bridge again.
                raise RuntimeError("The bridge's sending side stopped") from sender_failed[0]
            try:
                updates = bot.updates(state.get("update_offset", 0))
            except TelegramError as e:
                print(f"[{_now()}] Couldn't fetch messages: {e}")
                time.sleep(RETRY_SECONDS)
                continue
            for update in updates:
                handle_update(update, state, memory, bot)
                save_state(home, state, update_offset=update["update_id"] + 1)
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
    parser = argparse.ArgumentParser(description="Chat with Ahab over Telegram.")
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
