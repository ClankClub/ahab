"""Memory: what Perro keeps between wake-ups.

The model forgets everything the instant a call ends. Continuity has to live
on disk. Perro splits it into two layers, the same split Hermes and OpenClaw
use:

  notes.md     Curated, small, human-readable. Perro's own summary of what
               matters: who it is, what it's working on, what it learned.
               Shown to the model on every wake-up, so it has a size cap.

  memory.db    The raw log. Every event (woke up, thought something, did
               something) appended in order, forever. Too big to show the
               model in full; instead it gets the most recent few, and can
               search the rest.

Notes are the diary you reread each morning. The log is the box of receipts.
"""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_HOME = Path(__file__).resolve().parent.parent / "home"

# Roughly 1,000 tokens: room for who Perro is and what it knows about its
# owner, plus its open tasks. Still capped, because these get paid for on
# every wake-up and a cap forces Perro to decide what's worth keeping.
NOTES_CHAR_LIMIT = 4000

# Event kinds whose full text is too long for the page; snapshot() shows only
# their first line. Messages are shown in full in their own section instead
# (inbox.describe_recent), so here they'd only be a second copy.
HEADER_ONLY_KINDS = ("notes", "tool", "reasoning", "message", "dream")


@dataclass
class Event:
    id: int
    time: str   # ISO 8601, UTC
    kind: str   # free-form label: "wake", "thought", "action", "error", ...
    content: str


class Memory:
    def __init__(self, home: Path = DEFAULT_HOME):
        self.home = Path(home)
        self.home.mkdir(parents=True, exist_ok=True)
        self.notes_path = self.home / "notes.md"
        self.db = sqlite3.connect(self.home / "memory.db")
        # CREATE TABLE IF NOT EXISTS makes this safe to run on every start:
        # the first run creates the table, later runs find it already there.
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                time    TEXT NOT NULL,
                kind    TEXT NOT NULL,
                content TEXT NOT NULL
            )
        """)
        self.db.commit()

    # --- the raw log -------------------------------------------------------

    def record(self, kind: str, content: str) -> Event:
        """Append one event to the log. Events are never edited or deleted."""
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        # The ? placeholders let sqlite insert the values safely, so text
        # containing quotes or SQL can't break (or hijack) the query.
        cursor = self.db.execute(
            "INSERT INTO events (time, kind, content) VALUES (?, ?, ?)",
            (now, kind, content),
        )
        self.db.commit()
        return Event(cursor.lastrowid, now, kind, content)

    def recent(self, limit: int = 20) -> list[Event]:
        """The last `limit` events, oldest first (the order they happened)."""
        rows = self.db.execute(
            "SELECT id, time, kind, content FROM events ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [Event(*row) for row in reversed(rows)]

    def search(self, text: str, limit: int = 20,
               exclude_kinds: tuple[str, ...] = ()) -> list[Event]:
        """Events whose content contains `text` (case-insensitive), newest first."""
        skip = ",".join("?" * len(exclude_kinds)) or "''"
        rows = self.db.execute(
            "SELECT id, time, kind, content FROM events "
            f"WHERE content LIKE ? AND kind NOT IN ({skip}) ORDER BY id DESC LIMIT ?",
            (f"%{text}%", *exclude_kinds, limit),
        ).fetchall()
        return [Event(*row) for row in rows]

    # --- the curated notes -------------------------------------------------

    def read_notes(self) -> str:
        if not self.notes_path.exists():
            return ""
        return self.notes_path.read_text(encoding="utf-8")

    def write_notes(self, text: str) -> None:
        """Replace the notes entirely. Over the cap is refused, not trimmed:
        silently cutting Perro's notes would lose whatever was at the end."""
        if len(text) > NOTES_CHAR_LIMIT:
            raise ValueError(
                f"Notes are {len(text)} chars; the limit is {NOTES_CHAR_LIMIT}. "
                "Condense them first."
            )
        self.notes_path.write_text(text, encoding="utf-8")
        # Keep the full text in the log too, so every past version survives.
        self.record("notes", f"Rewrote notes ({len(text)} chars).\n{text}")

    # --- what the model gets to see ----------------------------------------

    def snapshot(self, recent_limit: int = 20) -> str:
        """Notes plus recent events, formatted as text for a prompt."""
        notes = self.read_notes().strip() or "(no notes yet)"
        events = self.recent(recent_limit)
        # Old notes versions, tool results, and private reasoning are kept in
        # full in the log, but the model only sees their one-line header here,
        # so the page stays small. (The full text is still searchable.)
        log = "\n".join(
            f"[{e.time}] {e.kind}: "
            + (e.content.splitlines()[0] if e.kind in HEADER_ONLY_KINDS else e.content)
            # A message's header alone ("From the owner (sent ...):") looked
            # like an empty message, and Perro once reported it as a bug.
            + (" (text under Recent conversation)" if e.kind == "message" else "")
            for e in events
        )
        return f"## Your notes\n{notes}\n\n## Recent events\n{log or '(nothing yet)'}"

    def close(self) -> None:
        self.db.close()


if __name__ == "__main__":
    # Peek at Perro's memory: python -m perro.memory   (or: ... search <text>)
    memory = Memory()
    if sys.argv[1:2] == ["search"]:
        for e in memory.search(" ".join(sys.argv[2:])):
            print(f"[{e.time}] {e.kind}: {e.content}")
    else:
        print(memory.snapshot())
    memory.close()
