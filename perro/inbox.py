"""Inbox: messages between the owner and Perro.

This is where the owner talks to Perro, hands it work, and where its
instructions come from. Other channels feed into it: telegram.py checks a
message really is the owner's before passing it on, since Perro treats
anything logged "From the owner" as coming from them. A message from the
owner waits in a mailbox (a `messages` table in memory.db) until Perro's next
wake, when it's delivered as a `message` event, right in the recent log
Perro reads. A message sent with --now is a knock instead: the sleeping
heartbeat notices within a few seconds and wakes Perro early.

Perro answers in the "message" field of its final reply, or mid-work with the
message_owner tool; both end up in perro_sends. Both directions
are also logged as `message` events, so Perro can recall its conversations.
Every wake-up page shows the latest messages under "Recent conversation"
(describe_recent), so the thread survives busy wake-ups full of tool calls;
the event log then shows message events by their header line only.

  python -m perro.inbox "hello"          leave a letter (read at next wake)
  python -m perro.inbox --now "hello"    knock: wake Perro to read it
  python -m perro.inbox                  show the conversation; marks Perro's
                                        replies as read by you
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from perro.memory import DEFAULT_HOME, Memory

MESSAGE_CHAR_LIMIT = 4000
# How much of the conversation Perro's page shows every wake-up.
RECENT_LIMIT = 16
RECENT_CHAR_LIMIT = 4000


def _table(memory: Memory) -> None:
    memory.db.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            time      TEXT NOT NULL,
            sender    TEXT NOT NULL,      -- 'owner' or 'perro'
            text      TEXT NOT NULL,
            knock     INTEGER NOT NULL DEFAULT 0,
            read_at   TEXT                -- when the other side saw it
        )
    """)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _check(text: str) -> str:
    text = str(text).strip()
    if not text:
        raise ValueError("A message can't be empty.")
    if len(text) > MESSAGE_CHAR_LIMIT:
        raise ValueError(f"Message is {len(text)} chars; the limit is {MESSAGE_CHAR_LIMIT}.")
    return text


# --- owner side --------------------------------------------------------------

def owner_sends(memory: Memory, text: str, knock: bool = False) -> int:
    """Put a letter in Perro's mailbox. Nothing is logged until Perro reads it."""
    _table(memory)
    cur = memory.db.execute(
        "INSERT INTO messages (time, sender, text, knock) VALUES (?, 'owner', ?, ?)",
        (_now(), _check(text), int(knock)),
    )
    memory.db.commit()
    return cur.lastrowid


def conversation(memory: Memory, limit: int = 20) -> list[tuple]:
    """(id, time, sender, text, read_at), oldest first."""
    _table(memory)
    rows = memory.db.execute(
        "SELECT id, time, sender, text, read_at FROM messages ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return list(reversed(rows))


def describe_recent(memory: Memory, limit: int = RECENT_LIMIT,
                    char_limit: int = RECENT_CHAR_LIMIT) -> str:
    """The "Recent conversation" section of Perro's page, oldest first.

    Shown apart from the event log so tool calls and reasoning can't push the
    conversation off the page. Owner messages count once Perro has been given
    them (read_at set); a letter still waiting in the mailbox isn't shown.
    Newest messages win the space; the newest one is always shown in full.
    """
    _table(memory)
    rows = memory.db.execute(
        "SELECT time, sender, text FROM messages "
        "WHERE sender = 'perro' OR read_at IS NOT NULL ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    lines, used = [], 0
    for sent, sender, text in rows:
        when = datetime.fromisoformat(sent).astimezone().strftime("%a %d %b %H:%M")
        line = f"[{when}] {'Owner' if sender == 'owner' else 'You'}: {text}"
        if lines and used + len(line) > char_limit:
            break
        lines.append(line)
        used += len(line)
    if not lines:
        return "(no messages yet)"
    return "\n\n".join(reversed(lines))


def owner_reads(memory: Memory) -> None:
    _table(memory)
    memory.db.execute(
        "UPDATE messages SET read_at = ? WHERE sender = 'perro' AND read_at IS NULL", (_now(),))
    memory.db.commit()


# --- Perro side ---------------------------------------------------------------

def deliver(memory: Memory) -> int:
    """At wake-up: log every unread owner message as an event, mark it read."""
    _table(memory)
    unread = memory.db.execute(
        "SELECT id, time, text FROM messages WHERE sender = 'owner' AND read_at IS NULL "
        "ORDER BY id"
    ).fetchall()
    for msg_id, sent, text in unread:
        memory.record("message", f"From the owner (sent {sent}):\n{text}")
        memory.db.execute("UPDATE messages SET read_at = ? WHERE id = ?", (_now(), msg_id))
        memory.db.commit()
    return len(unread)


def knocked(memory: Memory) -> bool:
    """Is there an unread knock? Checked by the sleeping heartbeat."""
    _table(memory)
    return memory.db.execute(
        "SELECT 1 FROM messages WHERE sender = 'owner' AND knock = 1 AND read_at IS NULL"
    ).fetchone() is not None


def perro_sends(memory: Memory, text: str) -> str:
    _table(memory)
    text = _check(text)
    memory.db.execute(
        "INSERT INTO messages (time, sender, text) VALUES (?, 'perro', ?)", (_now(), text))
    memory.db.commit()
    memory.record("message", f"To the owner:\n{text}")
    return "Sent. The owner will see it whenever they next check."


# --- the owner's command line ------------------------------------------------

def main() -> None:
    sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description="Write to Perro, or read the conversation.")
    parser.add_argument("text", nargs="*", help="your message (omit to read)")
    parser.add_argument("--now", action="store_true", help="knock: wake Perro to read it")
    parser.add_argument("--home", type=Path, default=DEFAULT_HOME)
    args = parser.parse_args()

    memory = Memory(args.home)
    try:
        if args.text:
            try:
                owner_sends(memory, " ".join(args.text), knock=args.now)
            except ValueError as e:
                print(e)
                sys.exit(1)
            print("Knocked. Perro wakes within seconds to read it." if args.now
                  else "Left in the mailbox. Perro reads it at its next wake.")
            return
        rows = conversation(memory)
        if not rows:
            print("No messages yet.")
        for _, when, sender, text, read_at in rows:
            local = datetime.fromisoformat(when).astimezone()
            who = "You" if sender == "owner" else "Perro"
            status = "" if sender == "perro" else ("  (read)" if read_at else "  (not read yet)")
            new = "  [new]" if sender == "perro" and not read_at else ""
            print(f"--- {who}, {local:%a %H:%M}{status}{new}\n{text}\n")
        owner_reads(memory)
    finally:
        memory.close()


if __name__ == "__main__":
    main()
