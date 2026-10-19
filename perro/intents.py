"""Standing intents: "when X comes up, do Y", remembered by code, not by Perro.

Alarms are for a time ("in 6 hours: re-read the draft"). An intent is for an
event: "when the owner mentions the move, ask whether the deposit came back",
"when an email from the clinic arrives, remind them to book the follow-up".
Writing that in notes and hoping to notice later is the weak way: a model
reading a long page is bad at acting on a plan stored as prose, and it can't
be trusted to work out on its own that the plan has lapsed. So the plan is
compiled into a row here, with words to listen for, and matching is done in
code at the start of each wake, against what just arrived: the owner's new
messages, and (if the intent asks) the sender and subject of new email.

A match "fires": it's logged as an `intent` event, so it lands in the recent
events Perro reads that wake, the same way an alarm rings. To keep it from
nagging, the limits are structural rather than left to judgment:
  - each intent fires at most `times` times (default 3), then it's done;
  - after firing it rests for `cooldown_hours` (default 24) before it can fire
    again, however often the words come up;
  - it expires after `days` (default 90) if it never comes up;
  - at most MAX_FIRES_PER_WAKE fire in one wake.
Perro can cancel one any time, and its armed intents are listed on its page.

Its own messages, its log and anything recalled never trigger one: only text
that came in from outside this wake.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from perro.memory import Memory

MAX_ARMED = 20
MAX_FIRES_PER_WAKE = 3
MESSAGE_CHAR_LIMIT = 500
MAX_WORDS = 8                  # phrases to listen for, per intent
WORD_CHAR_LIMIT = 60
DEFAULT_TIMES, MAX_TIMES = 3, 20
DEFAULT_COOLDOWN_HOURS, MAX_COOLDOWN_HOURS = 24, 24 * 30
DEFAULT_DAYS, MAX_DAYS = 90, 365
WATCHES = ("owner", "email", "both")
SNIPPET = 160


def _table(memory: Memory) -> None:
    memory.db.execute("""
        CREATE TABLE IF NOT EXISTS intents (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            created    TEXT NOT NULL,
            words      TEXT NOT NULL,      -- JSON list of phrases
            message    TEXT NOT NULL,
            watch      TEXT NOT NULL,
            times_left INTEGER NOT NULL,
            cooldown   INTEGER NOT NULL,   -- hours
            expires    TEXT NOT NULL,
            last_fired TEXT,
            ended      TEXT,               -- when it was used up, expired or canceled
            how_ended  TEXT
        )
    """)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _number(value, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number.")
    value = int(value)
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}.")
    return value


def _clean_words(words) -> list[str]:
    if isinstance(words, str):
        words = [words]
    if not isinstance(words, list) or not words:
        raise ValueError("when needs at least one word or phrase to listen for.")
    clean = []
    for w in words:
        w = " ".join(str(w).split()).lower()
        if not w:
            continue
        if len(w) < 3:
            raise ValueError(f"{w!r} is too short to listen for; use a word of 3 letters or more.")
        if len(w) > WORD_CHAR_LIMIT:
            raise ValueError(f"Phrases are limited to {WORD_CHAR_LIMIT} chars; {w[:20]!r}... is longer.")
        if w not in clean:
            clean.append(w)
    if not clean:
        raise ValueError("when needs at least one word or phrase to listen for.")
    if len(clean) > MAX_WORDS:
        raise ValueError(f"At most {MAX_WORDS} words or phrases per intent.")
    return clean


def _pattern(word: str) -> re.Pattern:
    # Anchored at the start of a word only, so endings still match: "move"
    # matches "moves" and "moved" but not "remove". Spaces match any run of spaces.
    body = r"\s+".join(re.escape(part) for part in word.split())
    return re.compile(r"(?<![\w])" + body, re.I)


def add(memory: Memory, when, then: str, watch: str = "owner", times=None,
        cooldown_hours=None, days=None) -> str:
    """Arm an intent. Raises ValueError (with a reason Perro can read) if refused."""
    _table(memory)
    words = _clean_words(when)
    message = " ".join(str(then).split())
    if not message:
        raise ValueError("then needs what to do or say when it comes up.")
    if len(message) > MESSAGE_CHAR_LIMIT:
        raise ValueError(f"then is {len(message)} chars; the limit is {MESSAGE_CHAR_LIMIT}.")
    if watch not in WATCHES:
        raise ValueError(f"watch must be one of {', '.join(WATCHES)}.")
    times = DEFAULT_TIMES if times is None else _number(times, "times", 1, MAX_TIMES)
    cooldown = (DEFAULT_COOLDOWN_HOURS if cooldown_hours is None
                else _number(cooldown_hours, "cooldown_hours", 0, MAX_COOLDOWN_HOURS))
    days = DEFAULT_DAYS if days is None else _number(days, "days", 1, MAX_DAYS)
    if len(armed(memory)) >= MAX_ARMED:
        raise ValueError(f"You already have {MAX_ARMED} standing intents; cancel one first.")
    now = _now()
    expires = now + timedelta(days=days)
    cur = memory.db.execute(
        "INSERT INTO intents (created, words, message, watch, times_left, cooldown, expires) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (now.isoformat(), json.dumps(words), message, watch, times, cooldown, expires.isoformat()))
    memory.db.commit()
    return (f"Standing intent #{cur.lastrowid} set: when {_listing(words)} comes up "
            f"({_watching(watch)}), you'll be shown it. Fires up to {times} time(s), "
            f"at most once every {cooldown} h, until {expires.date().isoformat()}.")


def _listing(words: list[str]) -> str:
    return " or ".join(f'"{w}"' for w in words)


def _watching(watch: str) -> str:
    return {"owner": "in your owner's messages", "email": "in new email's sender or subject",
            "both": "in your owner's messages or new email"}[watch]


def cancel(memory: Memory, intent_id: int) -> str:
    _table(memory)
    cur = memory.db.execute("UPDATE intents SET ended = ?, how_ended = 'canceled' "
                            "WHERE id = ? AND ended IS NULL", (_now().isoformat(), int(intent_id)))
    memory.db.commit()
    if cur.rowcount == 0:
        raise ValueError(f"No armed standing intent #{intent_id}.")
    return f"Canceled standing intent #{intent_id}."


def armed(memory: Memory) -> list[tuple]:
    """(id, words, message, watch, times_left, cooldown, expires, last_fired), oldest first."""
    _table(memory)
    rows = memory.db.execute(
        "SELECT id, words, message, watch, times_left, cooldown, expires, last_fired FROM intents "
        "WHERE ended IS NULL ORDER BY id").fetchall()
    return [(r[0], json.loads(r[1]), *r[2:]) for r in rows]


def _incoming(memory: Memory, since_id: int) -> list[tuple[str, str]]:
    """(source, text) for what arrived from outside since since_id."""
    out = []
    for kind, content in memory.db.execute(
            "SELECT kind, content FROM events WHERE id > ? AND kind IN ('message', 'email') "
            "ORDER BY id", (since_id,)):
        if kind == "message":
            first, _, text = content.partition("\n")
            if first.startswith("From the owner"):
                out.append(("owner", text))
        else:
            # Only "New email from <sender>...: '<subject>'", not the guidance
            # mail.py adds after "(id N)", which would match its own words.
            out.append(("email", content.split(" (id", 1)[0]))
    return out


def check(memory: Memory, since_id: int) -> int:
    """At the start of a wake: expire what's lapsed, then fire the intents that
    what came in since since_id (an event id) matches. Returns how many fired."""
    now = _now()
    incoming = _incoming(memory, since_id)
    fired = 0
    for iid, words, message, watch, left, cooldown, expires, last in armed(memory):
        if datetime.fromisoformat(expires) <= now:
            memory.db.execute("UPDATE intents SET ended = ?, how_ended = 'expired' WHERE id = ?",
                              (now.isoformat(), iid))
            memory.record("intent", f"Standing intent #{iid} (when {_listing(words)} comes up) expired "
                                    "without being used up. Set it again if it still matters.")
            continue
        if fired >= MAX_FIRES_PER_WAKE:
            continue
        if last and datetime.fromisoformat(last) + timedelta(hours=cooldown) > now:
            continue
        hit = _match(words, watch, incoming)
        if not hit:
            continue
        left -= 1
        memory.db.execute("UPDATE intents SET times_left = ?, last_fired = ? WHERE id = ?",
                          (left, now.isoformat(), iid))
        if left <= 0:
            memory.db.execute("UPDATE intents SET ended = ?, how_ended = 'used up' WHERE id = ?",
                              (now.isoformat(), iid))
        word, source, snippet = hit
        where = "your owner wrote" if source == "owner" else "an email came in"
        after = (f"{left} more time(s), not before {cooldown} h from now" if left > 0
                 else "that was its last time; it's done now")
        memory.record("intent", f"Standing intent #{iid} fired: {where} with \"{word}\" (\"{snippet}\"). "
                                f"What you set it for: {message} (It can fire {after}.)")
        fired += 1
    memory.db.commit()
    return fired


def _match(words: list[str], watch: str, incoming: list[tuple[str, str]]):
    for source, text in incoming:
        if watch != "both" and watch != source:
            continue
        for word in words:
            m = _pattern(word).search(text)
            if m:
                start = max(0, m.start() - SNIPPET // 2)
                snippet = " ".join(text[start:start + SNIPPET].split())
                return word, source, snippet
    return None


def describe(memory: Memory) -> str:
    """Armed intents, formatted for Perro's page."""
    rows = armed(memory)
    if not rows:
        return "(none set)"
    lines = []
    for iid, words, message, watch, left, cooldown, expires, last in rows:
        lines.append(f"#{iid} when {_listing(words)} ({_watching(watch)}; {left} fire(s) left, "
                     f"until {expires[:10]}): {message}")
    return "\n".join(lines)
