"""Memory: what Ahab keeps between wake-ups.

The model forgets everything the instant a call ends. Continuity has to live
on disk. Ahab splits it into two layers, the same split Hermes and OpenClaw
use:

  notes.md     Curated, small, human-readable. Ahab's own summary of what
               matters: who it is, what it's working on, what it learned.
               Shown to the model on every wake-up, so it has a size cap.

  memory.db    The raw log. Every event (woke up, thought something, did
               something) appended in order, forever. Too big to show the
               model in full; instead it gets the most recent few, and can
               search the rest.

Notes are the diary you reread each morning. The log is the box of receipts.
What lasts beyond the week goes in workspace files Ahab keeps on purpose,
MEMORY.md and the memory/ folder (see identity.py).

Finding a receipt is find(): SQLite's built-in full-text index (FTS5), the
same kind of search engine a mail program uses. It matches words, not exact
strings, so "whales" finds "whale", and ranks what it finds by how well it
matches. A trigger adds every new event to the index as it's written. The
same index also covers Ahab's memory files (MEMORY.md, memory/, dreams/),
so one search reaches both what happened and what it wrote down about it.
"""

from __future__ import annotations

import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_HOME = Path(__file__).resolve().parent.parent / "home"

# Roughly 1,000 tokens: room for who Ahab is and what it knows about its
# owner, plus its open tasks. Still capped, because these get paid for on
# every wake-up and a cap forces Ahab to decide what's worth keeping.
NOTES_CHAR_LIMIT = 8000

# Event kinds whose full text is too long for the page; snapshot() shows only
# their first line. Messages are shown in full in their own section instead
# (inbox.describe_recent), so here they'd only be a second copy.
# Logged by code with Ahab asleep (a source delivery, a digest), so they say nothing about whether it's awake.
BACKGROUND_KINDS = ("source", "digest")

# The token counts a wake, dream, digest or search logs in its closing line:
# "[3 tool calls, 1200 in (800 cached), 90 out]", "1200 in, 90 out", "1200 tokens in, 90 out".
TOKEN_STATS = re.compile(r"(\d+) (?:tokens )?in(?: \(\d+ cached\))?, (\d+) out")
HEADER_ONLY_KINDS = ("notes", "tool", "reasoning", "message", "dream", "email_sent")

# Tool results find() leaves out: these only repeat things it searches
# anyway (old search results, files, the guide), so every search would
# echo the ones before it.
ECHO_TOOLS = ("recall", "read_conversation", "read_file", "list_files", "about_yourself",
              "message_owner")  # a sent message is its own "message" event too
# Workspace files find() searches: the memory Ahab keeps on purpose.
MEMORY_FILES = ("MEMORY.md", "memory/**/*.md", "memory/**/*.txt", "dreams/**/*.md")
SNIPPET_TOKENS = 30


@dataclass
class Event:
    id: int
    time: str   # ISO 8601, UTC
    kind: str   # free-form label: "wake", "thought", "action", "error", ...
    content: str


@dataclass
class Hit:
    """One find() result: an event (id set) or a memory file (path set)."""
    snippet: str
    id: int | None = None
    time: str = ""
    kind: str = ""
    path: str = ""


def fts_query(text: str, any_word: bool = False) -> str:
    """Turn what Ahab typed into a safe FTS5 query: every word and every
    "quoted phrase" becomes a quoted term, so stray punctuation or words
    like AND and NEAR can't break the search or change what it means."""
    phrases = [p.strip() for p in re.findall(r'"([^"]*)"', text) if p.strip()]
    words = re.findall(r"\w+", re.sub(r'"[^"]*"', " ", text))
    terms = [f'"{t}"' for t in phrases + words]
    return (" OR " if any_word else " ").join(terms)


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
        self.fts = self._index()
        self.db.commit()

    def _index(self) -> bool:
        """Set up the full-text index, building it from the whole log the
        first time. False if this SQLite has no FTS5 (find() then falls back
        to plain substring search)."""
        had = self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'events_fts'").fetchone()
        try:
            # content='events': the index points at the events table instead
            # of keeping a second copy of every event's text.
            self.db.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
                    content, content='events', content_rowid='id',
                    tokenize='porter unicode61')
            """)
            self.db.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(
                    path, body, mtime UNINDEXED, tokenize='porter unicode61')
            """)
        except sqlite3.OperationalError:
            return False
        self.db.execute("""
            CREATE TRIGGER IF NOT EXISTS events_fts_add AFTER INSERT ON events BEGIN
                INSERT INTO events_fts (rowid, content) VALUES (new.id, new.content);
            END
        """)
        if not had:
            self.db.execute("INSERT INTO events_fts (events_fts) VALUES ('rebuild')")
        return True

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

    def get(self, event_id: int) -> Event | None:
        row = self.db.execute(
            "SELECT id, time, kind, content FROM events WHERE id = ?", (event_id,)).fetchone()
        return Event(*row) if row else None

    def find(self, text: str, limit: int = 10, since: str = "", until: str = "",
             files_root: Path | None = None) -> list[Hit]:
        """Search the whole log, and the memory files under files_root, by
        words: best matches first, memory files ahead of events. since and
        until are UTC ISO times that bound the events searched. Every word
        has to match; if nothing has them all, any word will do."""
        if not self.fts:
            return [Hit(e.content[:300], e.id, e.time, e.kind)
                    for e in self.search(text, limit, exclude_kinds=("tool",))]
        if files_root is not None:
            self.index_files(files_root)
        for any_word in (False, True):
            query = fts_query(text, any_word)
            if not query:
                return []
            hits = self._find_files(query, limit) if files_root is not None else []
            hits += self._find_events(query, limit, since, until)
            if hits:
                return hits
        return []

    def _find_events(self, query: str, limit: int, since: str, until: str) -> list[Hit]:
        echo = " OR ".join("e.content LIKE ?" for _ in ECHO_TOOLS)
        rows = self.db.execute(
            "SELECT e.id, e.time, e.kind, "
            f"snippet(events_fts, 0, '«', '»', ' ... ', {SNIPPET_TOKENS}) "
            "FROM events_fts JOIN events e ON e.id = events_fts.rowid "
            "WHERE events_fts MATCH ? AND e.time >= ? AND e.time < ? "
            f"AND NOT (e.kind = 'tool' AND ({echo})) "
            # Notes are kept in full every time they're rewritten, so one
            # fact can sit in fifty versions; ask for extra to drop repeats.
            "ORDER BY rank LIMIT ?",
            (query, since or "", until or "9999", *(f"{t} %" for t in ECHO_TOOLS), limit * 3),
        ).fetchall()
        hits, seen = [], set()
        for id_, time_, kind, snippet in rows:
            key = (kind, " ".join(snippet.split()))
            if key in seen:
                continue
            seen.add(key)
            hits.append(Hit(" ".join(snippet.split()), id_, time_, kind))
            if len(hits) == limit:
                break
        return hits

    def _find_files(self, query: str, limit: int) -> list[Hit]:
        rows = self.db.execute(
            f"SELECT path, snippet(files_fts, 1, '«', '»', ' ... ', {SNIPPET_TOKENS}) "
            "FROM files_fts WHERE files_fts MATCH ? ORDER BY rank LIMIT ?",
            # Only the body: a match on the path alone isn't a match.
            (f"body : ({query})", max(3, limit // 3)),
        ).fetchall()
        return [Hit(" ".join(snippet.split()), path=path) for path, snippet in rows]

    def index_files(self, root: Path) -> None:
        """Bring the files index up to date with the memory files under
        root: changed files re-read, deleted ones dropped. Cheap when
        nothing changed (one stat per file)."""
        root = Path(root)
        found = {}
        for pattern in MEMORY_FILES:
            for path in root.glob(pattern):
                # A link planted in the folder could point anywhere.
                if path.is_file() and path.resolve().is_relative_to(root.resolve()):
                    found[path.relative_to(root).as_posix()] = path
        known = dict(self.db.execute("SELECT path, mtime FROM files_fts").fetchall())
        for rel in set(known) - set(found):
            self.db.execute("DELETE FROM files_fts WHERE path = ?", (rel,))
        for rel, path in found.items():
            mtime = str(path.stat().st_mtime)
            if known.get(rel) == mtime:
                continue
            self.db.execute("DELETE FROM files_fts WHERE path = ?", (rel,))
            self.db.execute("INSERT INTO files_fts (path, body, mtime) VALUES (?, ?, ?)",
                            (rel, path.read_text(encoding="utf-8", errors="replace"), mtime))
        self.db.commit()

    # --- the curated notes -------------------------------------------------

    def read_notes(self) -> str:
        if not self.notes_path.exists():
            return ""
        return self.notes_path.read_text(encoding="utf-8")

    def write_notes(self, text: str) -> None:
        """Replace the notes entirely. Over the cap is refused, not trimmed:
        silently cutting Ahab's notes would lose whatever was at the end."""
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
            # like an empty message, and Ahab once reported it as a bug.
            + (" (text under Recent conversation)" if e.kind == "message" else "")
            for e in events
        )
        return f"## Your notes\n{notes}\n\n## Recent events\n{log or '(nothing yet)'}"

    def close(self) -> None:
        self.db.close()


if __name__ == "__main__":
    # Peek at Ahab's memory: python -m ahab.memory   (or: ... search <text>)
    memory = Memory()
    if sys.argv[1:2] == ["search"]:
        for h in memory.find(" ".join(sys.argv[2:]), 20, files_root=memory.home / "workspace"):
            print(f"{h.path}: {h.snippet}" if h.path else f"#{h.id} [{h.time}] {h.kind}: {h.snippet}")
    else:
        print(memory.snapshot())
    memory.close()
