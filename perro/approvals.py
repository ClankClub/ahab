"""Approvals: the owner says yes before Perro acts outside its own space.

Reading, researching and writing in Perro's own accounts run freely. An
action that reaches the world (making a site public, later perhaps sending
mail) instead goes through here, in four steps:

  1. A tool asks. It files a request with the exact payload of the action
     ("promote deployment dpl_123 of project rat-weights") and returns to
     Perro straight away. Nothing has happened yet.
  2. The card. Code writes it from the payload, so it states facts, not
     Perro's description of them: Perro reads web pages all day, and a page
     that steers it could otherwise get "fix a typo" written on a deploy.
     Perro may add a note, shown as Perro's.
  3. The owner decides, only through a channel that proves it's them: an
     Approve/Deny button on Telegram (telegram.py checks the paired account)
     or `python -m perro.approvals --yes N` at the PC. A "yes" typed in chat
     never counts, because Perro could phrase an innocent question so that
     the answer approves something.
  4. The code acts, not the model. The next wake runs the approved payload,
     exactly as filed, once, and logs the outcome for Perro to read. Perro
     has no tool that approves or runs anything.

A request nobody answers expires after EXPIRE_HOURS. Requests live in their
own table in memory.db, outside the workspace, so Perro's file tools can't
touch them.

Each kind of action registers two functions: one that writes its card from
the payload (and refuses a bad payload), one that carries it out.

  python -m perro.approvals               list what's waiting
  python -m perro.approvals --yes 3       approve request #3
  python -m perro.approvals --no 3        deny it
  python -m perro.approvals --test "hi"   file a harmless test request, to
                                          check the whole path end to end
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from perro.memory import DEFAULT_HOME, Memory

EXPIRE_HOURS = 48
MAX_PENDING = 10
NOTE_CHAR_LIMIT = 500
RESULT_CHAR_LIMIT = 1000


@dataclass(frozen=True)
class Action:
    card: Callable[[dict], str]          # payload -> what the owner reads; ValueError if bad
    run: Callable[[Memory, dict], str]   # carries it out; returns what happened


ACTIONS: dict[str, Action] = {}


def register(kind: str, card: Callable[[dict], str], run: Callable[[Memory, dict], str]) -> None:
    ACTIONS[kind] = Action(card, run)


# The test action: does nothing outside, so the whole path (request, card,
# button, decision, run, outcome) can be tried for real without risk.
def _test_card(payload: dict) -> str:
    text = str(payload.get("text", "")).strip()
    if not text or len(text) > 200:
        raise ValueError("A test request needs a short text.")
    return f"A test approval. Approving it does nothing outside; it only proves the path works.\nText: {text}"


register("test", _test_card, lambda memory, payload: f"The test went through: {payload['text']}")


# --- the table -----------------------------------------------------------
# status: pending -> approved -> running -> done | failed
#         pending -> denied | expired
# reported: when Perro's log was told the outcome (the heartbeat does that).

def _table(memory: Memory) -> None:
    memory.db.execute("""
        CREATE TABLE IF NOT EXISTS approvals (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            created          TEXT NOT NULL,
            kind             TEXT NOT NULL,
            payload          TEXT NOT NULL,
            card             TEXT NOT NULL,
            note             TEXT NOT NULL DEFAULT '',
            status           TEXT NOT NULL DEFAULT 'pending',
            decided          TEXT,
            decided_by       TEXT,
            result           TEXT,
            reported         TEXT,
            telegram_message INTEGER,
            telegram_closed  TEXT
        )
    """)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


@dataclass
class Request:
    id: int
    created: str
    kind: str
    payload: dict
    card: str
    note: str
    status: str
    decided: str | None
    result: str | None
    telegram_message: int | None

    def _body(self) -> list[str]:
        lines = [f"Perro asks your approval (#{self.id}):", "", self.card]
        if self.note:
            lines += ["", f"Perro's note: {self.note}"]
        return lines

    def text(self) -> str:
        """The whole card, as the owner sees it while it waits."""
        expires = datetime.fromisoformat(self.created) + timedelta(hours=EXPIRE_HOURS)
        return "\n".join(self._body() + ["", f"Expires {expires.astimezone():%a %d %b %H:%M}"
                                             " if you don't answer."])

    def closed_text(self) -> str:
        """The card once it's settled: what was decided and what came of it."""
        when = f"{datetime.fromisoformat(self.decided).astimezone():%a %d %b %H:%M}" if self.decided else ""
        outcome = {
            "approved": f"Approved {when}. Carrying it out.",
            "running": f"Approved {when}. Carrying it out.",
            "done": f"Approved {when}. Done: {self.result}",
            "failed": f"Approved {when}, but it failed: {self.result}",
            "denied": f"Denied {when}. Nothing was done.",
            "expired": "Expired unanswered. Nothing was done.",
        }.get(self.status, self.status)
        return "\n".join(self._body() + ["", outcome])


def _row(row) -> Request:
    return Request(row[0], row[1], row[2], json.loads(row[3]), *row[4:])


_COLUMNS = "id, created, kind, payload, card, note, status, decided, result, telegram_message"
FINAL = ("done", "failed", "denied", "expired")


def get(memory: Memory, request_id: int) -> Request | None:
    _table(memory)
    row = memory.db.execute(f"SELECT {_COLUMNS} FROM approvals WHERE id = ?", (int(request_id),)).fetchone()
    return _row(row) if row else None


def pending(memory: Memory) -> list[Request]:
    _table(memory)
    rows = memory.db.execute(f"SELECT {_COLUMNS} FROM approvals WHERE status = 'pending' ORDER BY id").fetchall()
    return [_row(r) for r in rows]


# --- 1. asking ------------------------------------------------------------

def request(memory: Memory, kind: str, payload: dict, note: str = "") -> int:
    """File a request. Raises ValueError (readable by Perro) if it's refused."""
    _table(memory)
    if kind not in ACTIONS:
        raise ValueError(f"No action called {kind!r} can be approved.")
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object.")
    note = " ".join(str(note or "").split())
    if len(note) > NOTE_CHAR_LIMIT:
        raise ValueError(f"The note is {len(note)} chars; the limit is {NOTE_CHAR_LIMIT}.")
    expire(memory)
    if len(pending(memory)) >= MAX_PENDING:
        raise ValueError(f"{MAX_PENDING} requests are already waiting on your owner.")
    card = ACTIONS[kind].card(payload)
    cur = memory.db.execute(
        "INSERT INTO approvals (created, kind, payload, card, note) VALUES (?, ?, ?, ?, ?)",
        (_now().isoformat(), kind, json.dumps(payload, sort_keys=True), card, note))
    memory.db.commit()
    return cur.lastrowid


# --- 3. deciding (the owner, through telegram.py or the command line) ------

def decide(memory: Memory, request_id: int, yes: bool, by: str) -> str:
    """Record the owner's answer. Returns a short line saying what happened."""
    _table(memory)
    expire(memory)
    req = get(memory, request_id)
    if req is None:
        return f"There's no request #{request_id}."
    if req.status != "pending":
        return f"#{req.id} was already {req.status}."
    cur = memory.db.execute(
        "UPDATE approvals SET status = ?, decided = ?, decided_by = ? WHERE id = ? AND status = 'pending'",
        ("approved" if yes else "denied", _now().isoformat(), by, req.id))
    memory.db.commit()
    if cur.rowcount == 0:
        return f"#{req.id} was decided a moment ago, some other way."
    return f"Approved #{req.id}. Perro will do it at once." if yes else f"Denied #{req.id}. Nothing happens."


def expire(memory: Memory) -> None:
    """Mark requests left unanswered too long as expired."""
    _table(memory)
    cutoff = (_now() - timedelta(hours=EXPIRE_HOURS)).isoformat()
    memory.db.execute("UPDATE approvals SET status = 'expired', decided = ? "
                      "WHERE status = 'pending' AND created < ?", (_now().isoformat(), cutoff))
    memory.db.commit()


def waiting(memory: Memory) -> bool:
    """Is there an approval Perro hasn't carried out? Checked by the sleeping
    heartbeat, so it happens within seconds. A denial doesn't wake Perro (a
    wake costs money and nothing needs doing); it hears at its next wake."""
    _table(memory)
    return memory.db.execute(
        "SELECT 1 FROM approvals WHERE status = 'approved' LIMIT 1").fetchone() is not None


# --- 4. acting (the heartbeat, at the start of a wake) ---------------------

def run_decided(memory: Memory) -> int:
    """Carry out what was approved, and log every outcome for Perro. Returns how many."""
    _table(memory)
    expire(memory)
    rows = memory.db.execute(
        f"SELECT {_COLUMNS} FROM approvals WHERE status IN ('approved', 'denied', 'expired') "
        "AND reported IS NULL ORDER BY id").fetchall()
    for req in map(_row, rows):
        headline = req.card.splitlines()[0]
        if req.status == "approved":
            # Claim it first, so it can never run twice.
            claimed = memory.db.execute(
                "UPDATE approvals SET status = 'running' WHERE id = ? AND status = 'approved'", (req.id,))
            memory.db.commit()
            if claimed.rowcount == 0:
                continue
            try:
                result, status = str(ACTIONS[req.kind].run(memory, req.payload)), "done"
            except Exception as e:  # a failed action is news for Perro, not a crash
                result, status = f"{type(e).__name__}: {e}", "failed"
            result = result[:RESULT_CHAR_LIMIT]
            text = (f"Your owner approved #{req.id} ({headline})."
                    + (f" Done: {result}" if status == "done" else f" It failed: {result}"))
        else:
            result, status = None, req.status
            text = (f"Your owner denied #{req.id} ({headline}). It wasn't done."
                    if status == "denied" else
                    f"#{req.id} ({headline}) expired after {EXPIRE_HOURS} hours unanswered. It wasn't done.")
        memory.db.execute("UPDATE approvals SET status = ?, result = ?, reported = ? WHERE id = ?",
                          (status, result, _now().isoformat(), req.id))
        memory.db.commit()
        memory.record("approval", text)
    return len(rows)


# --- Telegram's side: which cards to send, and which to close ----------------

def unsent(memory: Memory) -> list[Request]:
    """Waiting requests whose card hasn't gone to Telegram yet."""
    return [r for r in pending(memory) if r.telegram_message is None]


def mark_sent(memory: Memory, request_id: int, message_id: int) -> None:
    memory.db.execute("UPDATE approvals SET telegram_message = ? WHERE id = ?", (message_id, request_id))
    memory.db.commit()


def to_close(memory: Memory) -> list[Request]:
    """Settled requests whose Telegram card still shows the buttons."""
    _table(memory)
    marks = ", ".join("?" * len(FINAL))
    rows = memory.db.execute(
        f"SELECT {_COLUMNS} FROM approvals WHERE status IN ({marks}) AND telegram_message IS NOT NULL"
        " AND telegram_closed IS NULL ORDER BY id", FINAL).fetchall()
    return [_row(r) for r in rows]


def mark_closed(memory: Memory, request_id: int) -> None:
    memory.db.execute("UPDATE approvals SET telegram_closed = ? WHERE id = ?",
                      (_now().isoformat(), request_id))
    memory.db.commit()


def describe_pending(memory: Memory) -> str:
    """Requests still waiting on the owner, for Perro's page ('' if none)."""
    waiting_now = pending(memory)
    if not waiting_now:
        return ""
    lines = [f"#{r.id} (asked {datetime.fromisoformat(r.created).astimezone():%a %d %b %H:%M}): "
             f"{r.card.splitlines()[0]}" for r in waiting_now]
    return "## Waiting on your owner's approval\n" + "\n".join(lines)


# --- command line ----------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="See and answer Perro's approval requests.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--yes", type=int, metavar="N", help="approve request N")
    group.add_argument("--no", type=int, metavar="N", help="deny request N")
    group.add_argument("--test", metavar="TEXT", help="file a harmless test request")
    parser.add_argument("--home", type=Path, default=DEFAULT_HOME)
    args = parser.parse_args()
    sys.stdout.reconfigure(errors="replace")
    memory = Memory(args.home)
    try:
        if args.yes is not None or args.no is not None:
            print(decide(memory, args.yes if args.yes is not None else args.no,
                         args.yes is not None, "owner at the command line"))
        elif args.test is not None:
            try:
                print(f"Filed test request #{request(memory, 'test', {'text': args.test})}.")
            except ValueError as e:
                sys.exit(str(e))
        else:
            waiting_now = pending(memory)
            print("\n\n".join(r.text() for r in waiting_now) if waiting_now else "Nothing waiting.")
    finally:
        memory.close()


if __name__ == "__main__":
    main()
