"""Session: one running conversation instead of a fresh page every wake-up.

Before sessions, each wake-up rebuilt Ahab's page from scratch: the last
messages with the owner, one-line headers of the last few events, the notes.
What a tool returned, why Ahab chose what it chose, what it was halfway
through: gone from view unless it wrote it down. Now the page keeps growing.
Each wake adds its turns to a `session` table in memory.db, and the next wake
starts by reading them back:

  opening    when Ahab woke and what happened since the last wake-up (the
             owner's messages in full, alarms, reports, the dream, restarts)
  replies    Ahab's own JSON replies, with a rewritten notes field reduced to
             its length and long tool arguments cut (the log has them whole)
  results    tool results, cut the way a wake already cuts an older result

The heartbeat puts them after the system prompt and the identity files, and
ends the page with "Right now" (notes, alarms, goals and the rest as they
stand), which is rebuilt every wake and never stored. So the opening of the
page is the same from one wake to the next, and a model provider that caches
a repeated prompt opening (Codex, DeepSeek) charges little for it during a
burst of chat.

When the session grows past its size (`session_tokens` in home.json, default
DEFAULT_TOKENS, estimated at four characters a token), the oldest wake-ups
are folded into a summary the model writes, until about KEEP_SHARE of it is
left word for word. A wake-up is never split, so a tool call stays with its
result. Folded turns stay in the table, marked folded; nothing is deleted,
and the event log keeps everything as before. The design, and why there's
no memory-flush turn before folding, is in docs/DESIGN_SESSIONS.md.

  python -m ahab.session              how big the session is, and its summary
  python -m ahab.session --compact    fold it now (asks the home's model)
  python -m ahab.session --new        start a fresh one (the old stays in the log)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from ahab.memory import DEFAULT_HOME, HEADER_ONLY_KINDS, Memory

DEFAULT_TOKENS = 100_000      # the owner's choice, 2026-10-21; a small model's backend sets its own
CHARS_PER_TOKEN = 4           # a rough estimate, good enough to decide when to fold
KEEP_SHARE = 0.4              # how much of the size is left word for word after folding
HARD_SHARE = 1.5              # past this, fold even without a summary (the summarizer keeps failing)
RETRY_AFTER = 60 * 60         # seconds to wait after a failed summary before trying again
SUMMARY_CHAR_LIMIT = 12_000
NEWS_CHAR_LIMIT = 12_000      # the "since your last wake-up" block
NEWS_EVENT_CHAR_LIMIT = 2_000 # one event in it
THOUGHT_KEEP = 3_000          # a stored reply's thought
ARG_KEEP = 600                # a stored tool argument (a write_file's content, say)
RAW_KEEP = 2_000              # a stored reply that wasn't JSON

SUMMARY_PROMPT = """You keep the memory of a long conversation for the assistant who had it. The
assistant wakes up now and then; each wake-up opens with what came in (its
owner's messages, alarms, reports), followed by its replies (JSON objects with
its thought, any tool it used, and any message it sent its owner) and the
tool results.

You'll be given the summary so far and the older part of the conversation
that's about to leave the assistant's page. Write the summary that replaces
both. Keep what the assistant will need to carry on: what its owner asked for
and whether it got done; promises made and to whom; decisions and why; what
the owner said about themselves, their plans and their preferences; threads
still open, each with its next step; and the names, dates, file paths, ids
and links that go with them. Leave out routine wake-ups, tool results already
acted on, and chatter that changed nothing.

Write to the assistant in the second person ("you", "your owner"), in plain
text, oldest first, with dates where they matter. Stay under {limit}
characters. Reply with the summary only."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _local(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone().strftime("%a %d %b %H:%M")


def _table(memory: Memory) -> None:
    memory.db.execute("""
        CREATE TABLE IF NOT EXISTS session (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            time    TEXT NOT NULL,
            wake    INTEGER NOT NULL,     -- the id of the wake event this turn belongs to
            role    TEXT NOT NULL,        -- 'user', 'assistant' or 'summary'
            content TEXT NOT NULL,
            folded  INTEGER NOT NULL DEFAULT 0
        )
    """)
    memory.db.execute("CREATE TABLE IF NOT EXISTS session_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    memory.db.commit()


def _get(memory: Memory, key: str) -> str | None:
    row = memory.db.execute("SELECT value FROM session_meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _set(memory: Memory, key: str, value) -> None:
    memory.db.execute("INSERT OR REPLACE INTO session_meta (key, value) VALUES (?, ?)", (key, str(value)))
    memory.db.commit()


def _cut(text: str, keep: int) -> str:
    return text if len(text) <= keep else f"{text[:keep]} [... {len(text) - keep} more chars]"


# --- a wake-up's part in it ----------------------------------------------------

def begin(memory: Memory, wake_id: int) -> None:
    """At the start of a wake-up. A home that has never had a session gets one,
    seeded with the conversation the old page would have shown, so the switch
    doesn't leave Ahab with a blank page."""
    _table(memory)
    if _get(memory, "started") is None:
        from ahab import inbox
        _set(memory, "started", _now())
        convo = inbox.describe_recent(memory)
        if convo != "(no messages yet)":
            add(memory, wake_id, "summary",
                f"Your session began on {datetime.now().astimezone():%A %d %B %Y}. Until then every wake-up "
                "started from a fresh page; since then your page is one running conversation. Your "
                f"conversation with your owner just before it (their local time):\n{convo}")
    if _get(memory, "seen") is None:
        # What happened since the last finished wake-up goes in this one's opening.
        row = memory.db.execute("SELECT id FROM events WHERE kind = 'sleep' AND id < ? ORDER BY id DESC "
                                "LIMIT 1", (wake_id,)).fetchone()
        _set(memory, "seen", row[0] if row else wake_id - 1)


def add(memory: Memory, wake_id: int, role: str, content: str) -> None:
    _table(memory)
    memory.db.execute("INSERT INTO session (time, wake, role, content) VALUES (?, ?, ?, ?)",
                      (_now(), wake_id, role, content))
    memory.db.commit()


def finish(memory: Memory) -> None:
    """At the end of a wake-up: everything logged so far is in the session now,
    so the next opening starts after it."""
    _table(memory)
    row = memory.db.execute("SELECT MAX(id) FROM events").fetchone()
    _set(memory, "seen", row[0] or 0)


def news(memory: Memory) -> str:
    """What was logged since the last wake-up finished: the owner's messages in
    full, other events as the old page showed them (long kinds by their first
    line). Oldest first. Over NEWS_CHAR_LIMIT, the oldest events that aren't
    the owner's words are left out first."""
    seen = int(_get(memory, "seen") or 0)
    rows = memory.db.execute("SELECT time, kind, content FROM events WHERE id > ? AND kind != 'wake' "
                             "ORDER BY id", (seen,)).fetchall()
    lines = []
    for when, kind, content in rows:
        first, _, rest = content.partition("\n")
        if kind == "message" and first.startswith("From the owner"):
            lines.append((True, f"[{_local(when)}] Owner: {rest.strip()}"))
        elif kind == "message":
            lines.append((False, f"[{_local(when)}] You, to your owner: {_cut(rest.strip(), NEWS_EVENT_CHAR_LIMIT)}"))
        elif kind in HEADER_ONLY_KINDS:
            lines.append((False, f"[{_local(when)}] {kind}: {first}"))
        else:
            lines.append((False, f"[{_local(when)}] {kind}: {_cut(content, NEWS_EVENT_CHAR_LIMIT)}"))
    size, dropped, i = sum(len(text) + 1 for _, text in lines), 0, 0
    while size > NEWS_CHAR_LIMIT and i < len(lines):
        if lines[i][0]:
            i += 1
            continue
        size -= len(lines.pop(i)[1]) + 1
        dropped += 1
    text = "\n".join(t for _, t in lines)
    if dropped:
        text = f"({dropped} earlier event(s) left out for room; recall finds them.)\n{text}"
    return text


def opening(memory: Memory) -> str:
    """The first turn of a wake-up: when, and what's new. Stored in the session."""
    local = datetime.now().astimezone()
    what = news(memory)
    return (f"Woke at {local:%A %d %B %Y, %H:%M %Z} (your owner's local time; {_now()} UTC).\n"
            + (f"Since your last wake-up:\n{what}" if what else "Nothing new since your last wake-up."))


def shrink_reply(text: str) -> str:
    """One of Ahab's replies as the session keeps it."""
    from ahab.heartbeat import parse_reply   # here: the heartbeat imports this module
    try:
        decision, _ = parse_reply(text)
    except ValueError:
        return _cut(text.strip(), RAW_KEEP)
    kept = {}
    for key, value in decision.items():
        if key == "notes" and isinstance(value, str):
            value = f"(you rewrote your notes here: {len(value)} chars)"
        elif key == "thought" and isinstance(value, str):
            value = _cut(value, THOUGHT_KEEP)
        elif key == "args" and isinstance(value, dict):
            value = {k: _cut(v, ARG_KEEP) if isinstance(v, str) else v for k, v in value.items()}
        kept[key] = value
    return json.dumps(kept, ensure_ascii=False)


def shrink_result(result: str) -> str:
    """A tool result as the session keeps it: cut like an older result in a wake."""
    from ahab.heartbeat import TRIM_RESULTS_OVER, TRIMMED_KEEP
    if len(result) <= TRIM_RESULTS_OVER:
        return result
    return (f"{result[:TRIMMED_KEEP]}\n[trimmed: {len(result) - TRIMMED_KEEP} more chars. Run the tool "
            "again if you need the rest.]")


# --- what the page shows ---------------------------------------------------------

def summary(memory: Memory) -> str | None:
    _table(memory)
    row = memory.db.execute("SELECT content FROM session WHERE role = 'summary' AND folded = 0 "
                            "ORDER BY id DESC LIMIT 1").fetchone()
    return row[0] if row else None


def turns(memory: Memory) -> list[dict]:
    """The session's unfolded turns, oldest first, as chat messages."""
    _table(memory)
    return [{"role": role, "content": content} for role, content in memory.db.execute(
        "SELECT role, content FROM session WHERE folded = 0 AND role != 'summary' ORDER BY id")]


def merge(messages: list[dict]) -> list[dict]:
    """Join back-to-back messages of the same role (a failed wake-up leaves a
    user turn that the next opening follows). Only plain-text messages are
    joined; one carrying images stays as it is."""
    out = []
    for m in messages:
        if (out and out[-1]["role"] == m["role"] and m["role"] != "system"
                and isinstance(out[-1]["content"], str) and isinstance(m["content"], str)
                and set(out[-1]) == {"role", "content"} and set(m) == {"role", "content"}):
            out[-1] = {"role": m["role"], "content": f"{out[-1]['content']}\n\n{m['content']}"}
        else:
            out.append(dict(m))
    return out


def size(memory: Memory) -> int:
    """The session's estimated size on the page, in tokens."""
    _table(memory)
    # Folding marks every older summary folded, so at most one summary counts here.
    chars = memory.db.execute("SELECT COALESCE(SUM(LENGTH(content)), 0) FROM session WHERE folded = 0").fetchone()[0]
    return chars // CHARS_PER_TOKEN


def limit(config: dict) -> int:
    """How big the session may grow: home.json's session_tokens, else the backend's, else the default."""
    from ahab.model import BACKENDS
    value = config.get("session_tokens") or BACKENDS.get(config.get("backend") or "", {}).get("session_tokens")
    return int(value or DEFAULT_TOKENS)


# --- folding -----------------------------------------------------------------------

def _wakes(memory: Memory) -> list[tuple[int, int, str]]:
    """(wake id, chars, first turn's time) for each wake-up with unfolded turns, oldest first."""
    return memory.db.execute(
        "SELECT wake, SUM(LENGTH(content)), MIN(time) FROM session WHERE folded = 0 AND role != 'summary' "
        "GROUP BY wake ORDER BY MIN(id)").fetchall()


def _to_fold(memory: Memory, limit_tokens: int) -> list[int]:
    """The oldest wake-ups to fold so that about KEEP_SHARE of the size is left.
    The newest wake-up is always kept, however big it is."""
    wakes = _wakes(memory)
    keep_chars, kept = limit_tokens * KEEP_SHARE * CHARS_PER_TOKEN, 0
    keep_from = len(wakes) - 1
    while keep_from > 0 and kept + wakes[keep_from][1] <= keep_chars:
        kept += wakes[keep_from][1]
        keep_from -= 1
    if keep_from == len(wakes) - 1:
        keep_from -= 1                     # the newest alone is over the size: fold the rest
    return [w for w, _, _ in wakes[:keep_from + 1]]


def _fold(memory: Memory, wakes: list[int], text: str) -> None:
    marks = ",".join("?" * len(wakes))
    last = max(wakes)
    memory.db.execute(f"UPDATE session SET folded = 1 WHERE (wake IN ({marks}) AND role != 'summary') "
                      "OR role = 'summary'", wakes)
    memory.db.execute("INSERT INTO session (time, wake, role, content) VALUES (?, ?, 'summary', ?)",
                      (_now(), last, text))
    memory.db.commit()


def _transcript(memory: Memory, wakes: list[int]) -> str:
    marks = ",".join("?" * len(wakes))
    parts = []
    for role, content in memory.db.execute(
            f"SELECT role, content FROM session WHERE folded = 0 AND role != 'summary' AND wake IN ({marks}) "
            "ORDER BY id", wakes):
        parts.append(f"--- {'Its reply' if role == 'assistant' else 'Then'}:\n{content}")
    return "\n\n".join(parts)


def compact(memory: Memory, client, limit_tokens: int, force: bool = False, clock=None) -> str:
    """Fold the oldest wake-ups into a new summary. Returns what happened, in a line.
    Over the size, or with force. A failed summary leaves everything as it was and
    waits RETRY_AFTER, unless the session is past HARD_SHARE of its size: then the
    oldest wake-ups are folded under a short note instead, so the page can't grow
    without end while the model is down."""
    from ahab.model import ModelError
    _table(memory)
    now = time.time() if clock is None else clock()
    current = size(memory)
    if not force:
        if current <= limit_tokens:
            return "under its size"
        if now < float(_get(memory, "retry_after") or 0) and current <= limit_tokens * HARD_SHARE:
            return "waiting to retry"
    wakes = _to_fold(memory, limit_tokens if not force else min(limit_tokens, current))
    if not wakes:
        return "nothing to fold"
    spans = {w: t for w, _, t in _wakes(memory)}
    first, last = _local(spans[wakes[0]]), _local(spans[wakes[-1]])
    folded_chars = sum(c for w, c, _ in _wakes(memory) if w in wakes)
    before = summary(memory) or ""
    page = (f"## The summary so far\n{before or '(none yet)'}\n\n"
            f"## The older part of the conversation, oldest first ({first} to {last})\n"
            f"{_transcript(memory, wakes)}")
    try:
        reply = client.complete([{"role": "system", "content": SUMMARY_PROMPT.format(limit=SUMMARY_CHAR_LIMIT)},
                                 {"role": "user", "content": page}])
        text = reply.text.strip()
        if not text:
            raise ModelError("the summary came back empty")
    except Exception as e:   # a model error, or anything odd in its reply: never retried every wake
        if current > limit_tokens * HARD_SHARE:
            note = (f"[{len(wakes)} wake-up(s) from {first} to {last} were folded on "
                    f"{datetime.now().astimezone():%a %d %b} without a summary, because writing one failed "
                    f"({e}). recall and read_conversation still reach them.]")
            kept = before[-(SUMMARY_CHAR_LIMIT - len(note) - 2):] if before else ""
            _fold(memory, wakes, f"{kept}\n\n{note}".strip())
            memory.record("session", f"Folded {len(wakes)} wake-up(s) of your session without a summary "
                                     f"(summarizing failed: {e}).")
            return "folded without a summary"
        _set(memory, "retry_after", now + RETRY_AFTER)
        memory.record("error", f"Summarizing the older part of your session failed ({e}). Nothing was lost; "
                               "it's tried again in an hour.")
        return "failed"
    if len(text) > SUMMARY_CHAR_LIMIT:
        text = text[:SUMMARY_CHAR_LIMIT] + " [cut at the limit]"
    _fold(memory, wakes, text)
    memory.record("session",
                  f"Folded {len(wakes)} wake-up(s) of your session ({first} to {last}, about "
                  f"{folded_chars // CHARS_PER_TOKEN} tokens) into its summary. "
                  f"[{reply.prompt_tokens} in ({reply.cached_tokens} cached), {reply.completion_tokens} out]\n{text}")
    return f"folded {len(wakes)} wake-up(s)"


def start_fresh(memory: Memory) -> None:
    """The owner's fresh start: everything folds, under a one-line summary."""
    _table(memory)
    memory.db.execute("UPDATE session SET folded = 1 WHERE folded = 0")
    memory.db.execute("INSERT INTO session (time, wake, role, content) VALUES (?, 0, 'summary', ?)",
                      (_now(), f"Your owner started this session fresh on {datetime.now().astimezone():%A %d %B %Y}. "
                               "What came before is in your log (recall and read_conversation reach it), and "
                               "your notes and memory files are as you left them."))
    memory.db.commit()
    _set(memory, "started", _now())
    memory.record("session", "Your owner started a fresh session.")


# --- the owner's command line -------------------------------------------------------

def main() -> None:
    sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description="See Ahab's running session, fold it, or start a fresh one.")
    parser.add_argument("--home", type=Path, default=DEFAULT_HOME)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--compact", action="store_true", help="fold the older part into the summary now")
    group.add_argument("--new", action="store_true", help="start a fresh session (the old one stays in the log)")
    args = parser.parse_args()
    from ahab.heartbeat import home_config
    config = home_config(args.home)
    memory = Memory(args.home)
    try:
        _table(memory)
        cap = limit(config)
        if args.new:
            start_fresh(memory)
            print("Started a fresh session. Ahab's next wake-up begins from its identity files and notes.")
            return
        if args.compact:
            from ahab.model import ModelClient, with_fallback
            client = with_fallback(ModelClient.from_backend(config["backend"]), config.get("fallback"))
            print(f"Folding: {compact(memory, client, cap, force=True)}.")
        started = _get(memory, "started")
        print(f"Session: about {size(memory):,} of {cap:,} tokens, {len(_wakes(memory))} wake-up(s) on the page"
              + (f", begun {_local(started)}." if started else "."))
        text = summary(memory)
        print(f"\nSummary:\n{text}" if text else "\nNo summary yet.")
    finally:
        memory.close()


if __name__ == "__main__":
    main()
