"""Inbox: messages between the owner and Ahab.

This is where the owner talks to Ahab, hands it work, and where its
instructions come from. Other channels feed into it: telegram.py checks a
message really is the owner's before passing it on, since Ahab treats
anything logged "From the owner" as coming from them. A message from the
owner waits in a mailbox (a `messages` table in memory.db) until Ahab's next
wake, when it's delivered as a `message` event, right in the recent log
Ahab reads. A message sent with --now is a knock instead: the sleeping
heartbeat notices within a few seconds and wakes Ahab early.

Ahab answers in the "message" field of its final reply, or mid-work with the
message_owner tool; both end up in ahab_sends. Both directions
are also logged as `message` events, so Ahab can recall its conversations.
The owner's messages reach Ahab's page in a wake-up's opening, in its running
session (session.py), and Ahab's own are in its replies there.
describe_recent writes the conversation out on its own: the session's first
summary on a home that had none, and the owner's view at the command line.

  python -m ahab.inbox "hello"          leave a letter (read at next wake)
  python -m ahab.inbox --now "hello"    knock: wake Ahab to read it
  python -m ahab.inbox                  show the conversation; marks Ahab's
                                        replies as read by you
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from ahab.memory import DEFAULT_HOME, Memory

MESSAGE_CHAR_LIMIT = 16_000
# How much of the conversation Ahab's page shows every wake-up.
RECENT_LIMIT = 30
RECENT_CHAR_LIMIT = 16_000


def _table(memory: Memory) -> None:
    memory.db.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            time      TEXT NOT NULL,
            sender    TEXT NOT NULL,      -- 'owner' or 'ahab'
            text      TEXT NOT NULL,
            knock     INTEGER NOT NULL DEFAULT 0,
            read_at   TEXT,               -- when the other side saw it
            attachments TEXT              -- JSON list of workspace paths, or NULL
        )
    """)
    # Homes from before the rename to Ahab called the agent's side 'perro'.
    if memory.db.execute("SELECT 1 FROM messages WHERE sender = 'perro' LIMIT 1").fetchone():
        memory.db.execute("UPDATE messages SET sender = 'ahab' WHERE sender = 'perro'")
        memory.db.commit()
    # Homes from before attachments existed get the column added.
    if "attachments" not in {row[1] for row in memory.db.execute("PRAGMA table_info(messages)")}:
        memory.db.execute("ALTER TABLE messages ADD COLUMN attachments TEXT")
        memory.db.commit()


def files_of(attachments: str | None) -> list[str]:
    return json.loads(attachments) if attachments else []


def with_files(text: str, attachments: str | None) -> str:
    """A message as the conversation shows it: Ahab's own files are named
    after its text (an owner's message already says what came with it)."""
    files = files_of(attachments)
    return text + (f"\n[Attached: {', '.join(files)}]" if files else "")


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

def owner_sends(memory: Memory, text: str, knock: bool = False, attachments: list[str] = ()) -> int:
    """Put a letter in Ahab's mailbox. Nothing is logged until Ahab reads it.
    attachments: workspace paths of what came with it (a photo, a file); the
    text should say what they are, since that's what Ahab reads."""
    _table(memory)
    cur = memory.db.execute(
        "INSERT INTO messages (time, sender, text, knock, attachments) VALUES (?, 'owner', ?, ?, ?)",
        (_now(), _check(text), int(knock), json.dumps(list(attachments)) if attachments else None),
    )
    memory.db.commit()
    return cur.lastrowid


def conversation(memory: Memory, limit: int = 20) -> list[tuple]:
    """(id, time, sender, text, read_at), oldest first."""
    _table(memory)
    rows = memory.db.execute(
        "SELECT id, time, sender, text, read_at, attachments FROM messages ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [(i, t, s, with_files(x, a) if s == "ahab" else x, r) for i, t, s, x, r, a in reversed(rows)]


def describe_recent(memory: Memory, limit: int = RECENT_LIMIT,
                    char_limit: int = RECENT_CHAR_LIMIT) -> str:
    """The "Recent conversation" section of Ahab's page, oldest first.

    Shown apart from the event log so tool calls and reasoning can't push the
    conversation off the page. Owner messages count once Ahab has been given
    them (read_at set); a letter still waiting in the mailbox isn't shown.
    Newest messages win the space; the newest one is always shown in full.
    """
    _table(memory)
    rows = memory.db.execute(
        "SELECT time, sender, text, attachments FROM messages "
        "WHERE sender = 'ahab' OR read_at IS NOT NULL ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    lines, used = [], 0
    for sent, sender, text, files in rows:
        line = _line(sent, sender, text, files)
        if lines and used + len(line) > char_limit:
            break
        lines.append(line)
        used += len(line)
    waiting = memory.db.execute(
        "SELECT COUNT(*) FROM messages WHERE sender = 'owner' AND read_at IS NULL").fetchone()[0]
    if waiting:
        # Shown last: more from the owner is queued (see deliver).
        lines.insert(0, f"({waiting} more message(s) from your owner are waiting; you'll get them on "
                        "the wake-up right after this one, and they may change what's being asked.)")
    if not lines:
        return "(no messages yet)"
    return "\n\n".join(reversed(lines))


def waiting_note(memory: Memory) -> str | None:
    """A line for the page when more from the owner is queued than one wake-up takes (see deliver)."""
    _table(memory)
    waiting = memory.db.execute(
        "SELECT COUNT(*) FROM messages WHERE sender = 'owner' AND read_at IS NULL").fetchone()[0]
    if not waiting:
        return None
    return (f"{waiting} more message(s) from your owner are waiting; you'll get them on the wake-up right "
            "after this one, and they may change what's being asked.")


def _line(sent: str, sender: str, text: str, files: str | None) -> str:
    when = datetime.fromisoformat(sent).astimezone().strftime("%a %d %b %H:%M")
    shown = with_files(text, files) if sender == "ahab" else text
    return f"[{when}] {'Owner' if sender == 'owner' else 'You'}: {shown}"


def owner_reads(memory: Memory) -> None:
    _table(memory)
    memory.db.execute(
        "UPDATE messages SET read_at = ? WHERE sender = 'ahab' AND read_at IS NULL", (_now(),))
    memory.db.commit()


# --- Ahab side ---------------------------------------------------------------

def deliver(memory: Memory) -> int:
    """At wake-up: log unread owner messages as events and mark them read,
    oldest first, but only as many as "Recent conversation" can show
    (RECENT_LIMIT messages, RECENT_CHAR_LIMIT characters; the first always
    goes in, whatever its size). Marked read is what puts a message on the
    page, so the rest stay unread with a knock and the heartbeat wakes again
    for them right after this wake. Photos that came with them are shown to
    Ahab on this wake (senses.py)."""
    from ahab import senses, tools
    _table(memory)
    waiting = memory.db.execute(
        "SELECT id, time, text, attachments FROM messages WHERE sender = 'owner' AND read_at IS NULL "
        "ORDER BY id"
    ).fetchall()
    unread, used = [], 0
    for row in waiting[:RECENT_LIMIT]:
        size = len(_line(row[1], "owner", row[2], row[3]))
        if unread and used + size > RECENT_CHAR_LIMIT:
            break
        unread.append(row)
        used += size
    if len(unread) < len(waiting):
        memory.db.execute("UPDATE messages SET knock = 1 WHERE sender = 'owner' AND read_at IS NULL "
                          "AND id > ?", (unread[-1][0],))
        memory.db.commit()
    for msg_id, sent, text, files in unread:
        memory.record("message", f"From the owner (sent {sent}):\n{text}")
        memory.db.execute("UPDATE messages SET read_at = ? WHERE id = ?", (_now(), msg_id))
        memory.db.commit()
        for rel in files_of(files):
            path = tools.workspace_root(memory) / rel
            if senses.kind(path) == "image" and path.is_file():
                try:
                    senses.show(memory, path, rel)
                except senses.SenseError:
                    pass   # too big to send; the message still names the file
    return len(unread)


def knocked(memory: Memory) -> bool:
    """Is there an unread knock? Checked by the sleeping heartbeat."""
    _table(memory)
    return memory.db.execute(
        "SELECT 1 FROM messages WHERE sender = 'owner' AND knock = 1 AND read_at IS NULL"
    ).fetchone() is not None


def ahab_sends(memory: Memory, text: str, attachments: list[str] = ()) -> str:
    """attachments: workspace paths, already checked by the caller; the
    Telegram bridge sends each file after the text."""
    _table(memory)
    text = _check(text)
    files = json.dumps(list(attachments)) if attachments else None
    memory.db.execute(
        "INSERT INTO messages (time, sender, text, attachments) VALUES (?, 'ahab', ?, ?)",
        (_now(), text, files))
    memory.db.commit()
    memory.record("message", f"To the owner:\n{with_files(text, files)}")
    return "Sent" + (f", with {len(attachments)} file(s)" if attachments else "") + \
        ". The owner will see it whenever they next check."


# --- the owner's command line ------------------------------------------------

def main() -> None:
    sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description="Write to Ahab, or read the conversation.")
    parser.add_argument("text", nargs="*", help="your message (omit to read)")
    parser.add_argument("--now", action="store_true", help="knock: wake Ahab to read it")
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
            print("Knocked. Ahab wakes within seconds to read it." if args.now
                  else "Left in the mailbox. Ahab reads it at its next wake.")
            return
        rows = conversation(memory)
        if not rows:
            print("No messages yet.")
        for _, when, sender, text, read_at in rows:
            local = datetime.fromisoformat(when).astimezone()
            who = "You" if sender == "owner" else "Ahab"
            status = "" if sender == "ahab" else ("  (read)" if read_at else "  (not read yet)")
            new = "  [new]" if sender == "ahab" and not read_at else ""
            print(f"--- {who}, {local:%a %H:%M}{status}{new}\n{text}\n")
        owner_reads(memory)
    finally:
        memory.close()


if __name__ == "__main__":
    main()
