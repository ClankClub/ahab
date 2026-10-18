"""Alarms: messages Perro leaves for a future version of itself.

Each wake-up Perro already picks when it next exists. An alarm adds what that
future self should think about: "in 6 hours: re-read draft 2 cold." Perro
sets one with a tool. The heartbeat then sleeps until whichever comes first,
the wake Perro chose or its earliest alarm. When an alarm comes due it "rings":
it's logged as an `alarm` event, so it lands in the recent events Perro reads
on waking, and it's marked fired so it never rings twice.

Alarms live in their own table in memory.db, next to the event log, so they
survive restarts and get backed up with everything else. A restart or a
sleeping PC can't lose one: anything overdue rings at the next wake.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from perro.memory import Memory

MIN_MINUTES = 10              # same floor as a normal wake
MAX_MINUTES = 30 * 24 * 60    # 30 days
MAX_PENDING = 20
MESSAGE_CHAR_LIMIT = 500


def _table(memory: Memory) -> None:
    memory.db.execute("""
        CREATE TABLE IF NOT EXISTS alarms (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            created  TEXT NOT NULL,
            due      TEXT NOT NULL,
            message  TEXT NOT NULL,
            fired    TEXT,
            canceled TEXT
        )
    """)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def add(memory: Memory, minutes: int, message: str) -> str:
    """Set an alarm. Raises ValueError (with a reason Perro can read) if refused."""
    _table(memory)
    if isinstance(minutes, bool) or not isinstance(minutes, (int, float)):
        raise ValueError("minutes_from_now must be a number.")
    minutes = int(minutes)
    if not MIN_MINUTES <= minutes <= MAX_MINUTES:
        raise ValueError(f"minutes_from_now must be between {MIN_MINUTES} and {MAX_MINUTES}.")
    message = str(message).strip()
    if not message:
        raise ValueError("An alarm needs a message.")
    if len(message) > MESSAGE_CHAR_LIMIT:
        raise ValueError(f"Message is {len(message)} chars; the limit is {MESSAGE_CHAR_LIMIT}.")
    if len(pending(memory)) >= MAX_PENDING:
        raise ValueError(f"You already have {MAX_PENDING} alarms set; cancel one first.")
    now = _now()
    due = now + timedelta(minutes=minutes)
    cur = memory.db.execute(
        "INSERT INTO alarms (created, due, message) VALUES (?, ?, ?)",
        (now.isoformat(), due.isoformat(), message),
    )
    memory.db.commit()
    return f"Alarm #{cur.lastrowid} set for {due.isoformat()} ({minutes} min from now)."


def cancel(memory: Memory, alarm_id: int) -> str:
    _table(memory)
    cur = memory.db.execute(
        "UPDATE alarms SET canceled = ? WHERE id = ? AND fired IS NULL AND canceled IS NULL",
        (_now().isoformat(), int(alarm_id)),
    )
    memory.db.commit()
    if cur.rowcount == 0:
        raise ValueError(f"No pending alarm #{alarm_id}.")
    return f"Canceled alarm #{alarm_id}."


def pending(memory: Memory) -> list[tuple[int, str, str]]:
    """(id, due, message) for every alarm that hasn't rung or been canceled."""
    _table(memory)
    return memory.db.execute(
        "SELECT id, due, message FROM alarms "
        "WHERE fired IS NULL AND canceled IS NULL ORDER BY due"
    ).fetchall()


def ring_due(memory: Memory) -> int:
    """Log every alarm that's due (or overdue) and mark it fired. Returns how many."""
    now = _now()
    rung = 0
    for alarm_id, due, message in pending(memory):
        if datetime.fromisoformat(due) > now:
            break   # sorted by due time, so the rest are later
        created = memory.db.execute(
            "SELECT created FROM alarms WHERE id = ?", (alarm_id,)).fetchone()[0]
        memory.record("alarm", f"Alarm #{alarm_id} (set at {created}, due {due}): {message}")
        memory.db.execute("UPDATE alarms SET fired = ? WHERE id = ?", (now.isoformat(), alarm_id))
        memory.db.commit()
        rung += 1
    return rung


def next_due(memory: Memory) -> float | None:
    """When the earliest pending alarm is due, as a Unix timestamp, or None."""
    alarms = pending(memory)
    return datetime.fromisoformat(alarms[0][1]).timestamp() if alarms else None


def describe_pending(memory: Memory) -> str:
    """Pending alarms, formatted for Perro's page."""
    alarms = pending(memory)
    if not alarms:
        return "(none set)"
    return "\n".join(f"#{i} due {due}: {msg}" for i, due, msg in alarms)
