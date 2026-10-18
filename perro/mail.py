"""Sending email from Perro's own Gmail, to a short list of addresses only.

Reading mail lives in n8n.py. Sending is an outward action, and the usual
way for those is the approval step (approvals.py). For mail the owner chose
a lighter fence instead: Perro may write freely, but only to addresses the
owner listed, and the owner gets a blind copy of everything. Four fences:

- The list is home/email.json, outside the workspace, so Perro's file tools
  can't add to it. It holds the owner's address and the allowed ones:
      {"owner": "you@example.com", "allowed": ["friend@example.com", ...]}
  The repo is public, so the real addresses live only there (and in n8n).
- One recipient per email, no CC; the owner is BCC'd unless the mail is to
  them. A reply goes to whoever sent the original, and is refused if they
  aren't on the list.
- At most DAILY_LIMIT emails a day, so a confused loop can't get the
  account flagged as a spammer.
- The relay ("Perro: email send" in n8n) checks the list again, by SHA-256
  of each address, and builds the message itself, so a bug here can't
  smuggle in another header or recipient.

Every send is logged as an `email_sent` event with the whole text.
"""

from __future__ import annotations

import email.utils
import json
import re
from datetime import datetime, timezone

from perro import n8n
from perro.memory import Memory

SETTINGS = "email.json"
DAILY_LIMIT = 20
SUBJECT_LIMIT = 200
BODY_LIMIT = 20_000
ADDRESS = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
THREAD = re.compile(r"[0-9a-f]{6,40}")
MESSAGE_ID = re.compile(r"<[^<>\s]{3,300}>")


def settings(memory: Memory) -> tuple[str, set[str]]:
    """The owner's address and everyone Perro may write to (the owner included)."""
    try:
        data = json.loads((memory.home / SETTINGS).read_text(encoding="utf-8"))
        owner = _address(data["owner"])
        allowed = {_address(a) for a in data.get("allowed", [])}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise n8n.N8nError("You can't send email yet: your owner hasn't set up who you may "
                           f"write to (home/{SETTINGS}).") from None
    return owner, allowed | {owner}


def _address(value: str) -> str:
    """One plain address, lowercased, from "Name <a@b.c>" or "a@b.c"."""
    value = str(value)
    # Some Pythons read "a@x.com, b@y.com" as just the first address; say no instead.
    if value.count("@") != 1 or re.search(r"[,;]", value):
        raise ValueError(f"{value!r} isn't one email address.")
    _, found = email.utils.parseaddr(value)
    found = found.strip().lower()
    if not ADDRESS.fullmatch(found):
        raise ValueError(f"{value!r} isn't one email address.")
    return found


def sent_today(memory: Memory) -> int:
    """Emails sent since midnight, local time."""
    midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    since = midnight.astimezone(timezone.utc).isoformat(timespec="seconds")
    return memory.db.execute("SELECT COUNT(*) FROM events WHERE kind = 'email_sent' AND time >= ?",
                             (since,)).fetchone()[0]


def _original(id: str) -> dict:
    """What a reply needs from the email it answers."""
    id = str(id).strip()
    if not re.fullmatch(r"[0-9A-Za-z]{6,40}", id):
        raise ValueError("reply_to must be an email id from search_email.")
    message = n8n.call("perro/email/read", id=id).get("message")
    if not message:
        raise n8n.N8nError("No email with that id to reply to.")
    h = {x.get("name", "").lower(): x.get("value", "") for x in (message.get("payload") or {}).get("headers") or []}
    return {"thread": str(message.get("threadId") or ""), "from": h.get("reply-to") or h.get("from", ""),
            "subject": h.get("subject", ""), "message_id": h.get("message-id", "").strip()}


def send_email(memory: Memory, to: str = "", subject: str = "", body: str = "", reply_to: str = "") -> str:
    owner, allowed = settings(memory)
    payload: dict = {}
    if reply_to:
        original = _original(reply_to)
        to = to or original["from"]
        subject = subject or original["subject"]
        if subject and not subject.lower().startswith("re:"):
            subject = "Re: " + subject
        if THREAD.fullmatch(original["thread"]):
            payload["threadId"] = original["thread"]
        if MESSAGE_ID.fullmatch(original["message_id"]):
            payload["inReplyTo"] = original["message_id"]
    if not to:
        raise ValueError("Say who to write to (to), or which email you're answering (reply_to).")
    to = _address(to)
    if to not in allowed:
        raise n8n.N8nError(f"{to} isn't on the list of people you may email. Your owner keeps that "
                           "list; ask them if you think someone should be on it.")
    subject = " ".join(str(subject).split())
    body = str(body).replace("\r\n", "\n").strip()
    if not subject or not body:
        raise ValueError("An email needs a subject and a body.")
    if len(subject) > SUBJECT_LIMIT or len(body) > BODY_LIMIT:
        raise ValueError(f"Too long: subjects up to {SUBJECT_LIMIT} chars, bodies up to {BODY_LIMIT}.")
    if sent_today(memory) >= DAILY_LIMIT:
        raise n8n.N8nError(f"You've sent {DAILY_LIMIT} emails today, the daily limit. It resets at midnight.")

    payload.update(to=to, subject=subject, body=body)
    if to != owner:
        payload["bcc"] = owner
    answer = n8n.send("perro/email/send", payload)
    if answer.get("error") or not answer.get("id"):
        raise n8n.N8nError(f"The email wasn't sent: {answer.get('error') or 'Gmail gave no id back'}.")
    memory.record("email_sent", f"To {to}: {subject} (id {answer['id']})\n{body}")
    copy = "" if to == owner else " Your owner got a blind copy, as with every email you send."
    return f"Sent to {to}: {subject!r} (id {answer['id']}).{copy}"
