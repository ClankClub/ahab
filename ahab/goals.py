"""Goals: objectives Ahab works toward across wakes, on a budget kept by code.

An alarm is a time, an intent is a phrase, a watch is a page, a worker is
one job. A goal is an objective: what's wanted, what done looks like, how
many tokens it may cost, by when, and how long it may sit untouched (its
pace) before it wakes Ahab. It lives here, in memory.db, outside the
workspace, so Ahab moves it forward but can't rewrite it.

Two sizes, decided by the numbers rather than by Ahab's description:
  - small: Ahab opens it itself (up to OWN_BUDGET tokens, OWN_DAYS days,
    MAX_OWN_OPEN at once). It shows in the record (the status screen,
    /goals on Telegram, the day pages); nobody is pinged.
  - large: anything bigger goes through the approval step (`goal`), with a
    card written here from the payload. The owner can also open one
    directly with /goal on Telegram or `python -m ahab.goals --start`.

Ahab moves goals forward; the owner moves the target. Ahab can log
progress, set a goal waiting, finish it as done (with evidence) or blocked,
and drop its own small ones. Only the owner edits, pauses, resumes or drops
a goal they set or approved, and more budget or time goes through
`goal_budget`. That way a page that steers Ahab can't also rewrite what
it's working toward.

Budget is charged here, so it can't be dodged:
  - a wake is charged to the goals Ahab touched during it (progress, a
    wait, finishing, opening), split evenly, and to any goal that came due
    and woke it, touched or not;
  - workers and crew tasks started for a goal charge their tokens to it
    when they report (goal_helpers).
At CHECKINS percent of the budget a goal event asks whether it's on track;
at 100% the goal is budget_limited and stops waking Ahab. Past its deadline
it's expired. Nothing is deleted.

  python -m ahab.goals                    the open goals and the last few ended
  python -m ahab.goals 3                  one goal and its whole log
  python -m ahab.goals --start "<text>"   open an owner goal
  python -m ahab.goals --pause 3          also --resume, --drop, --done
  python -m ahab.goals --budget 3 2M      set its budget; --days 3 30 its time left
  python -m ahab.goals --note 3 "<text>"  add a note of yours to its log
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ahab import approvals
from ahab.memory import DEFAULT_HOME, Memory

OWN_BUDGET = 200_000            # tokens: about 15 to 20 wakes
OWN_DAYS = 14
MAX_OWN_OPEN = 3
MAX_OPEN = 8
MAX_BUDGET = 5_000_000
MAX_DAYS = 90
OWNER_DEFAULT_BUDGET = 1_000_000
OWNER_DEFAULT_DAYS = 30
DEFAULT_PACE_HOURS = 24
OWN_MIN_PACE = 60               # minutes; Ahab's own goals can't wake it more often than hourly
MIN_PACE = 10                   # the heartbeat's own floor, for approved and owner goals
MAX_PACE = 7 * 24 * 60
MAX_WAIT_HOURS = 30 * 24
CHECKINS = (50, 90)             # percent of the budget
OBJECTIVE_CHAR_LIMIT = 300
DONE_CHAR_LIMIT = 300
NOTE_CHAR_LIMIT = 500
PAGE_NOTES = 3
SHOW_ENDED = 3
FOLDER = "goals"

OPEN = ("active", "paused", "blocked", "budget_limited")
ENDED = ("done", "dropped", "expired")
WHO = {"own": "you", "approved": "you, approved by your owner", "owner": "your owner"}


class GoalError(Exception):
    """A refused goal action; the text is for Ahab (or the owner) to read."""


def _table(memory: Memory) -> None:
    memory.db.execute("""
        CREATE TABLE IF NOT EXISTS goals (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            created       TEXT NOT NULL,
            who           TEXT NOT NULL,        -- own | approved | owner
            objective     TEXT NOT NULL,
            done_when     TEXT NOT NULL DEFAULT '',
            stop_when     TEXT NOT NULL DEFAULT '',
            budget        INTEGER NOT NULL,     -- tokens, in plus out
            spent         INTEGER NOT NULL DEFAULT 0,
            deadline      TEXT NOT NULL,
            pace          INTEGER NOT NULL,     -- minutes it may sit untouched
            due           TEXT NOT NULL,        -- when it next wakes Ahab
            waiting_until TEXT,
            waiting_why   TEXT,
            status        TEXT NOT NULL DEFAULT 'active',
            status_note   TEXT,
            checked       INTEGER NOT NULL DEFAULT 0,   -- highest check-in percent announced
            ended         TEXT,
            evidence      TEXT,
            approval      INTEGER
        )
    """)
    memory.db.execute("""
        CREATE TABLE IF NOT EXISTS goal_notes (
            id    INTEGER PRIMARY KEY AUTOINCREMENT,
            goal  INTEGER NOT NULL,
            time  TEXT NOT NULL,
            by    TEXT NOT NULL,                -- ahab | code | owner
            kind  TEXT NOT NULL,                -- progress, start, wait, finish, due, charge, ...
            text  TEXT NOT NULL
        )
    """)
    memory.db.execute("""
        CREATE TABLE IF NOT EXISTS goal_helpers (
            goal    INTEGER NOT NULL,
            kind    TEXT NOT NULL,              -- worker | agent
            helper  INTEGER NOT NULL,
            charged TEXT,
            PRIMARY KEY (kind, helper)
        )
    """)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(when: datetime) -> str:
    return when.isoformat()


def _local(iso: str | None, fmt: str = "%a %d %b %H:%M") -> str:
    return f"{datetime.fromisoformat(iso).astimezone():{fmt}}" if iso else ""


def _clean(text, name: str, limit: int, required: bool = True) -> str:
    text = " ".join(str(text or "").split())
    if required and not text:
        raise GoalError(f"{name} is needed.")
    if len(text) > limit:
        raise GoalError(f"{name} is {len(text)} chars; the limit is {limit}.")
    return text


def _whole(value, name: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GoalError(f"{name} must be a number.")
    if not low <= value <= high:
        raise GoalError(f"{name} must be from {low:g} to {high:g}.")
    return value


def tokens(text) -> int:
    """'2M', '500k', '1.5m', '200000', '2,000,000' -> a whole number of tokens."""
    if isinstance(text, bool):
        raise GoalError("A budget is a number of tokens.")
    if isinstance(text, (int, float)):
        return int(text)
    s = str(text).strip().lower().replace(",", "").replace("_", "").replace(" ", "")
    s = s.removesuffix("tokens").removesuffix("tok")
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([km]?)", s)
    if not match:
        raise GoalError(f"{text!r} isn't a number of tokens (like 200k or 2M).")
    return int(float(match.group(1)) * {"": 1, "k": 1_000, "m": 1_000_000}[match.group(2)])


def compact(n: int) -> str:
    for unit, size in (("M", 10**6), ("k", 10**3)):
        if n >= size:
            value = n / size
            return f"{value:.1f}".rstrip("0").rstrip(".") + unit
    return str(n)


def _slug(text: str) -> str:
    return "-".join(re.findall(r"[a-z0-9]+", text.lower()))[:40].strip("-") or "goal"


def folder(goal_id: int, objective: str) -> str:
    return f"{FOLDER}/{goal_id}-{_slug(objective)}"


def _note(memory: Memory, goal_id: int, by: str, kind: str, text: str, when: datetime | None = None) -> None:
    memory.db.execute("INSERT INTO goal_notes (goal, time, by, kind, text) VALUES (?, ?, ?, ?, ?)",
                      (goal_id, _iso(when or _now()), by, kind, text))


_COLUMNS = ("id, created, who, objective, done_when, stop_when, budget, spent, deadline, pace, due, "
            "waiting_until, waiting_why, status, status_note, checked, ended, evidence, approval")


class Goal:
    def __init__(self, row):
        (self.id, self.created, self.who, self.objective, self.done_when, self.stop_when, self.budget,
         self.spent, self.deadline, self.pace, self.due, self.waiting_until, self.waiting_why, self.status,
         self.status_note, self.checked, self.ended, self.evidence, self.approval) = row

    @property
    def percent(self) -> int:
        return int(self.spent * 100 // self.budget) if self.budget else 100

    @property
    def waiting(self) -> bool:
        return (self.status == "active" and bool(self.waiting_until)
                and datetime.fromisoformat(self.waiting_until) > _now())

    @property
    def state(self) -> str:
        """Its status as a word for people, 'waiting' included."""
        return "waiting" if self.waiting else self.status.replace("_", " ")


def get(memory: Memory, goal_id) -> Goal:
    _table(memory)
    try:
        goal_id = int(goal_id)
    except (TypeError, ValueError):
        raise GoalError("id must be a goal's number.") from None
    row = memory.db.execute(f"SELECT {_COLUMNS} FROM goals WHERE id = ?", (goal_id,)).fetchone()
    if not row:
        raise GoalError(f"There's no goal #{goal_id}.")
    return Goal(row)


def open_goals(memory: Memory) -> list[Goal]:
    _table(memory)
    marks = ", ".join("?" * len(OPEN))
    return [Goal(r) for r in memory.db.execute(
        f"SELECT {_COLUMNS} FROM goals WHERE status IN ({marks}) ORDER BY id", OPEN)]


def _recent_ended(memory: Memory, n: int = SHOW_ENDED) -> list[Goal]:
    marks = ", ".join("?" * len(ENDED))
    return [Goal(r) for r in memory.db.execute(
        f"SELECT {_COLUMNS} FROM goals WHERE status IN ({marks}) ORDER BY ended DESC LIMIT ?", (*ENDED, n))]


def notes(memory: Memory, goal_id: int, limit: int | None = None, kinds: tuple = ()) -> list[tuple]:
    sql = "SELECT time, by, kind, text FROM goal_notes WHERE goal = ?"
    args: list = [goal_id]
    if kinds:
        sql += f" AND kind IN ({', '.join('?' * len(kinds))})"
        args += list(kinds)
    rows = memory.db.execute(sql + " ORDER BY id DESC" + (" LIMIT ?" if limit else ""),
                             (*args, limit) if limit else args).fetchall()
    return rows[::-1]


# --- opening ----------------------------------------------------------------------

def _pace_minutes(pace_hours, who: str) -> int:
    if pace_hours in (None, ""):
        return DEFAULT_PACE_HOURS * 60
    low = (OWN_MIN_PACE if who == "own" else MIN_PACE) / 60
    hours = _whole(pace_hours, "pace_hours", low, MAX_PACE / 60)
    return int(round(hours * 60))


def _create(memory: Memory, who: str, objective: str, done_when: str, stop_when: str, budget: int,
            days: float, pace: int, approval: int | None = None, note: str = "") -> int:
    now = _now()
    # An owner goal wakes Ahab now. Ahab's own opens mid-wake, while it's already at it, and an
    # approved one is announced by the approval itself, so both are next due a pace from now.
    due = now if who == "owner" else now + timedelta(minutes=pace)
    cur = memory.db.execute(
        "INSERT INTO goals (created, who, objective, done_when, stop_when, budget, deadline, pace, due, approval)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (_iso(now), who, objective, done_when, stop_when, budget, _iso(now + timedelta(days=days)), pace,
         _iso(due), approval))
    goal_id = cur.lastrowid
    opened_by = {"own": "Opened by Ahab.", "approved": f"Opened by Ahab with the owner's approval (#{approval}).",
                 "owner": "Opened by the owner."}[who]
    _note(memory, goal_id, "ahab" if who != "owner" else "owner", "start",
          opened_by + (f" {note}" if note else ""))
    memory.db.commit()
    from ahab import tools
    try:
        (tools.workspace_root(memory) / folder(goal_id, objective)).mkdir(parents=True, exist_ok=True)
    except OSError:
        pass   # the folder is a convenience; the record is in the tables
    return goal_id


def _counts(memory: Memory) -> tuple[int, int]:
    goals = open_goals(memory)
    return len(goals), sum(g.who == "own" for g in goals)


def start(memory: Memory, objective, done_when, stop_when=None, budget=None, days=None, pace_hours=None,
          note=None) -> str:
    """Ahab opens a goal: small ones at once, bigger ones as a request for the owner's approval."""
    _table(memory)
    objective = _clean(objective, "objective", OBJECTIVE_CHAR_LIMIT)
    done_when = _clean(done_when, "done_when (what done looks like, concretely)", DONE_CHAR_LIMIT)
    stop_when = _clean(stop_when, "stop_when", DONE_CHAR_LIMIT, required=False)
    note = _clean(note, "note", NOTE_CHAR_LIMIT, required=False)
    budget = OWN_BUDGET if budget in (None, "") else tokens(budget)
    if not 1_000 <= budget <= MAX_BUDGET:
        raise GoalError(f"budget must be from 1k to {compact(MAX_BUDGET)} tokens.")
    days = OWN_DAYS if days in (None, "") else _whole(days, "days", 1, MAX_DAYS)
    small = budget <= OWN_BUDGET and days <= OWN_DAYS
    pace = _pace_minutes(pace_hours, "own" if small else "approved")
    total, own = _counts(memory)
    if total >= MAX_OPEN:
        raise GoalError(f"{total} goals are open; {MAX_OPEN} is the limit. Finish one first.")
    if small:
        if own >= MAX_OWN_OPEN:
            raise GoalError(f"You have {own} goals of your own open; {MAX_OWN_OPEN} is the limit. Finish or "
                            "drop one, or make this one big enough to ask your owner for.")
        goal_id = _create(memory, "own", objective, done_when, stop_when, budget, days, pace, note=note)
        return (f"Opened goal #{goal_id}: {compact(budget)} tokens, {days:g} days, due again every "
                f"{_pace_words(pace)} you leave it. Its folder is {folder(goal_id, objective)}/. Log "
                "progress with goal_progress whenever you work on it.")
    payload = {"objective": objective, "done_when": done_when, "stop_when": stop_when, "budget": budget,
               "days": days, "pace": pace}
    try:
        request_id = approvals.request(memory, "goal", payload, note)
    except ValueError as e:
        raise GoalError(str(e)) from None
    return (f"That's bigger than a goal you can open yourself (up to {compact(OWN_BUDGET)} tokens and "
            f"{OWN_DAYS} days), so it's filed as #{request_id} for your owner's approval. Nothing is open yet; "
            "you'll hear either way.")


def _pace_words(minutes: int) -> str:
    if minutes % 1440 == 0:
        return f"{minutes // 1440} day(s)"
    if minutes % 60 == 0:
        return f"{minutes // 60} hour(s)"
    return f"{minutes} min"


# --- Ahab moving goals forward --------------------------------------------------------

def _open(memory: Memory, goal_id) -> Goal:
    goal = get(memory, goal_id)
    if goal.status in ENDED:
        raise GoalError(f"Goal #{goal.id} is {goal.status}; it's closed.")
    return goal


def progress(memory: Memory, id, note, done_when=None) -> str:
    goal = _open(memory, id)
    note = _clean(note, "note", NOTE_CHAR_LIMIT)
    extra = ""
    if done_when not in (None, ""):
        if goal.done_when:
            raise GoalError(f"Goal #{goal.id} already says what done means; only your owner can change it.")
        done_when = _clean(done_when, "done_when", DONE_CHAR_LIMIT)
        memory.db.execute("UPDATE goals SET done_when = ? WHERE id = ?", (done_when, goal.id))
        _note(memory, goal.id, "ahab", "done_when", f"Done means: {done_when}")
        extra = " and wrote down what done means"
    now = _now()
    sets, args = ["due = ?"], [_iso(now + timedelta(minutes=goal.pace))]
    if goal.status == "blocked" and goal.who == "own":
        sets.append("status = 'active'")
        sets.append("status_note = NULL")
        extra += "; it's no longer blocked"
    memory.db.execute(f"UPDATE goals SET {', '.join(sets)} WHERE id = ?", (*args, goal.id))
    _note(memory, goal.id, "ahab", "progress", note)
    memory.db.commit()
    still = {"paused": " It's paused by your owner, so it won't wake you.",
             "blocked": " It's still blocked until your owner resumes it.",
             "budget_limited": " Its budget is used up, so it won't wake you; request_goal_budget asks for more."
             }.get(goal.status if not (goal.status == "blocked" and goal.who == "own") else "", "")
    return f"Logged on goal #{goal.id}{extra}. Spent {compact(goal.spent)} of {compact(goal.budget)}.{still}"


def wait(memory: Memory, id, hours, why) -> str:
    goal = _open(memory, id)
    if goal.status != "active":
        raise GoalError(f"Goal #{goal.id} is {goal.state}; only an active goal can wait.")
    hours = _whole(hours, "hours", 0.25, MAX_WAIT_HOURS)
    why = _clean(why, "why", NOTE_CHAR_LIMIT)
    until = min(_now() + timedelta(hours=hours), datetime.fromisoformat(goal.deadline))
    memory.db.execute("UPDATE goals SET waiting_until = ?, waiting_why = ?, due = ? WHERE id = ?",
                      (_iso(until), why, _iso(until), goal.id))
    _note(memory, goal.id, "ahab", "wait", f"Waiting until {_local(_iso(until))}: {why}")
    memory.db.commit()
    return f"Goal #{goal.id} waits until {_local(_iso(until))} and wakes you then."


def finish(memory: Memory, id, how, evidence=None, reason=None) -> str:
    goal = _open(memory, id)
    how = str(how or "").strip().lower()
    now = _iso(_now())
    if how == "done":
        evidence = _clean(evidence, "evidence (where the result is: a file, a link, a page, a commit)",
                          NOTE_CHAR_LIMIT)
        memory.db.execute("UPDATE goals SET status = 'done', ended = ?, evidence = ?, waiting_until = NULL "
                          "WHERE id = ?", (now, evidence, goal.id))
        _note(memory, goal.id, "ahab", "finish", f"Done. Evidence: {evidence}")
        memory.db.commit()
        return (f"Goal #{goal.id} is done, at {compact(goal.spent)} of {compact(goal.budget)} tokens. "
                "Tell your owner, if this is news to them.")
    if how == "blocked":
        reason = _clean(reason, "reason (what's in the way, and what you need)", NOTE_CHAR_LIMIT)
        memory.db.execute("UPDATE goals SET status = 'blocked', status_note = ?, waiting_until = NULL "
                          "WHERE id = ?", (reason, goal.id))
        _note(memory, goal.id, "ahab", "finish", f"Blocked: {reason}")
        memory.db.commit()
        who = (" Log progress on it when you can go on." if goal.who == "own"
               else " Only your owner can resume it.")
        return (f"Goal #{goal.id} is blocked and won't wake you.{who} Tell your owner what you need: "
                "they can't help with what they don't know.")
    if how == "dropped":
        if goal.who != "own":
            raise GoalError(f"Goal #{goal.id} is your owner's{' (approved)' if goal.who == 'approved' else ''}; "
                            "only they can drop it. Mark it blocked and say why.")
        reason = _clean(reason, "reason", NOTE_CHAR_LIMIT)
        memory.db.execute("UPDATE goals SET status = 'dropped', ended = ?, status_note = ?, waiting_until = NULL "
                          "WHERE id = ?", (now, reason, goal.id))
        _note(memory, goal.id, "ahab", "finish", f"Dropped: {reason}")
        memory.db.commit()
        return f"Dropped goal #{goal.id}."
    raise GoalError('how must be "done", "blocked" or "dropped".')


def request_budget(memory: Memory, id, more=None, days=None, why=None) -> str:
    goal = _open(memory, id)
    why = _clean(why, "why", NOTE_CHAR_LIMIT)
    more = 0 if more in (None, "") else tokens(more)
    days = 0 if days in (None, "") else _whole(days, "days", 1, MAX_DAYS)
    if more < 0 or (not more and not days):
        raise GoalError("Ask for more tokens (more), more days (days), or both.")
    if goal.budget + more > MAX_BUDGET:
        raise GoalError(f"That would make the budget {compact(goal.budget + more)}; {compact(MAX_BUDGET)} is the most.")
    # The card's facts come from the record, written here by code, never from Ahab's words.
    lately = "; ".join(t for _, _, _, t in notes(memory, goal.id, PAGE_NOTES, ("progress",))) or "none logged"
    payload = {"goal": goal.id, "more": more, "days": days, "objective": goal.objective,
               "done_when": goal.done_when, "spent": goal.spent, "budget": goal.budget,
               "deadline": goal.deadline, "lately": lately}
    try:
        request_id = approvals.request(memory, "goal_budget", payload, why)
    except ValueError as e:
        raise GoalError(str(e)) from None
    return f"Filed #{request_id} for your owner's approval. You'll hear either way."


# --- helpers and charging ----------------------------------------------------------------

def require_active(memory: Memory, goal_id) -> Goal:
    """For a worker or crew task started for a goal: the goal has to be one being worked on."""
    goal = _open(memory, goal_id)
    if goal.status != "active":
        raise GoalError(f"Goal #{goal.id} is {goal.state}, so nothing can be started for it.")
    return goal


def link(memory: Memory, goal_id: int, kind: str, helper: int) -> None:
    _table(memory)
    memory.db.execute("INSERT OR IGNORE INTO goal_helpers (goal, kind, helper) VALUES (?, ?, ?)",
                      (int(goal_id), kind, int(helper)))
    _note(memory, int(goal_id), "code", "helper", f"Started {kind} {'#' if kind == 'worker' else 'task #'}{helper} for it.")
    memory.db.commit()


def charge(memory: Memory, goal_id: int, amount: int, what: str) -> None:
    """Add spend to a goal; announce check-ins and the end of its budget."""
    if amount <= 0:
        return
    goal = get(memory, goal_id)
    spent = goal.spent + amount
    memory.db.execute("UPDATE goals SET spent = ? WHERE id = ?", (spent, goal.id))
    _note(memory, goal.id, "code", "charge", f"{compact(amount)} tokens for {what}.")
    memory.db.commit()
    if goal.status not in OPEN:
        return
    percent = spent * 100 // goal.budget
    if percent >= 100 and goal.status != "budget_limited":
        memory.db.execute("UPDATE goals SET status = 'budget_limited', checked = 100 WHERE id = ?", (goal.id,))
        _note(memory, goal.id, "code", "status", f"Budget used up ({compact(spent)} of {compact(goal.budget)}).")
        memory.db.commit()
        memory.record("goal", f"Goal #{goal.id} ({goal.objective}) has used its whole budget, {compact(spent)} "
                              f"of {compact(goal.budget)} tokens, so it won't wake you again. If it's worth more, "
                              "request_goal_budget asks your owner; if it's done, finish_goal with the evidence. "
                              "A budget running out doesn't make unfinished work done.")
        return
    crossed = [c for c in CHECKINS if goal.checked < c <= percent]
    if crossed and percent < 100:
        memory.db.execute("UPDATE goals SET checked = ? WHERE id = ?", (crossed[-1], goal.id))
        memory.db.commit()
        lately = "; ".join(t for _, _, _, t in notes(memory, goal.id, PAGE_NOTES, ("progress",))) or "none logged"
        memory.record("goal", f"Goal #{goal.id} ({goal.objective}) has used {percent}% of its budget "
                              f"({compact(spent)} of {compact(goal.budget)} tokens). Progress lately: {lately}. "
                              f"Done means: {goal.done_when or '(not written yet)'}. Is it on track? If not, "
                              "change how you're going about it, mark it blocked, or drop it if it's yours.")


def charge_wake(memory: Memory, since: str, spent: int) -> None:
    """Charge one wake's tokens to the goals it touched, and to any goal that woke it."""
    _table(memory)
    rows = memory.db.execute(
        "SELECT DISTINCT goal FROM goal_notes WHERE time >= ? AND (by = 'ahab' OR kind = 'due')", (since,)).fetchall()
    goal_ids = [r[0] for r in rows]
    if not goal_ids or spent <= 0:
        return
    share = spent // len(goal_ids)
    for goal_id in goal_ids:
        charge(memory, goal_id, share, "a wake" + (f" shared by {len(goal_ids)} goals" if len(goal_ids) > 1 else ""))


def charge_helpers(memory: Memory) -> None:
    """Charge finished workers' and crew tasks' tokens to the goals they were started for."""
    _table(memory)
    for kind, table in (("worker", "workers"), ("agent", "agent_tasks")):
        try:
            rows = memory.db.execute(
                f"SELECT h.goal, h.helper, t.tokens_in + t.tokens_out FROM goal_helpers h JOIN {table} t "
                "ON t.id = h.helper WHERE h.kind = ? AND h.charged IS NULL "
                "AND t.status NOT IN ('running', 'cancelling')", (kind,)).fetchall()
        except Exception:   # the helper's table appears with its first run
            continue
        for goal_id, helper, amount in rows:
            memory.db.execute("UPDATE goal_helpers SET charged = ? WHERE kind = ? AND helper = ?",
                              (_iso(_now()), kind, helper))
            memory.db.commit()
            charge(memory, goal_id, int(amount or 0), f"{kind} {'#' if kind == 'worker' else 'task #'}{helper}")


# --- waking Ahab ------------------------------------------------------------------------

def ring_due(memory: Memory) -> int:
    """At the start of a wake: expire what's past its deadline, and log a goal event for each one due.
    Each due goal moves on by one pace, so one left alone rings once a pace, not every wake."""
    _table(memory)
    now = _now()
    rang = 0
    for goal in open_goals(memory):
        if datetime.fromisoformat(goal.deadline) <= now:
            memory.db.execute("UPDATE goals SET status = 'expired', ended = ?, waiting_until = NULL WHERE id = ?",
                              (_iso(now), goal.id))
            _note(memory, goal.id, "code", "status", "Expired: its deadline passed.")
            memory.db.commit()
            memory.record("goal", f"Goal #{goal.id} ({goal.objective}) expired: its deadline passed, at "
                                  f"{compact(goal.spent)} of {compact(goal.budget)} tokens. Tell your owner where it "
                                  "got to, if they're waiting on it.")
            continue
        if goal.status != "active" or datetime.fromisoformat(goal.due) > now:
            continue
        waited = goal.waiting_why if goal.waiting_until else None
        memory.db.execute("UPDATE goals SET due = ?, waiting_until = NULL, waiting_why = NULL WHERE id = ?",
                          (_iso(now + timedelta(minutes=goal.pace)), goal.id))
        _note(memory, goal.id, "code", "due", "The wait was over." if waited else "Came due.")
        memory.db.commit()
        last = notes(memory, goal.id, 1, ("progress",))
        if waited:
            why = f"the wait you set is over ({waited})"
        elif goal.who == "owner" and not last:
            why = "your owner just set it"
        else:
            why = f"it's gone {_pace_words(goal.pace)} untouched"
        memory.record("goal", f"Goal #{goal.id} is due: {why}. {goal.objective}. "
                              f"Done means: {goal.done_when or '(not written yet: write it with goal_progress)'}. "
                              f"Spent {compact(goal.spent)} of {compact(goal.budget)}. "
                              + (f"Last progress: {last[0][3]}" if last else "No progress logged yet."))
        rang += 1
    return rang


def next_due(memory: Memory) -> float | None:
    """When the soonest active goal wants Ahab, as a timestamp; None if none does."""
    _table(memory)
    row = memory.db.execute("SELECT MIN(due) FROM goals WHERE status = 'active'").fetchone()
    return datetime.fromisoformat(row[0]).timestamp() if row and row[0] else None


def due_now(memory: Memory) -> bool:
    """Checked by the sleeping heartbeat, so a goal the owner just set wakes Ahab within seconds."""
    _table(memory)
    return memory.db.execute("SELECT 1 FROM goals WHERE status = 'active' AND due <= ? LIMIT 1",
                             (_iso(_now()),)).fetchone() is not None


# --- what Ahab and the owner see ---------------------------------------------------------

def _line(goal: Goal) -> str:
    return f"#{goal.id} {goal.objective} [{goal.state}, {goal.percent}% of {compact(goal.budget)}]"


def describe(memory: Memory) -> str:
    """The open goals, for Ahab's page."""
    goals = open_goals(memory)
    if not goals:
        return ("(none open) A goal is for work that spans wake-ups; start_goal opens a small one, and a "
                "bigger one goes to your owner.")
    blocks = []
    for g in goals:
        if g.status == "active":
            when = (f"waiting until {_local(g.waiting_until)}: {g.waiting_why}" if g.waiting
                    else f"due {_local(g.due)} unless you log progress first")
        else:
            when = {"paused": "paused by your owner", "blocked": f"blocked: {g.status_note}",
                    "budget_limited": "budget used up"}[g.status]
        lines = [f"#{g.id} ({'yours' if g.who == 'own' else WHO[g.who]}) {g.objective}",
                 f"  done means: {g.done_when or '(not written yet: write it with goal_progress)'}"
                 + (f"; stop when: {g.stop_when}" if g.stop_when else ""),
                 f"  spent {compact(g.spent)} of {compact(g.budget)}, ends {_local(g.deadline, '%a %d %b')}, {when}"]
        lines += [f"  {_local(t, '%d %b')}: {text}" for t, _, _, text in notes(memory, g.id, PAGE_NOTES, ("progress",))]
        lines.append(f"  folder: {folder(g.id, g.objective)}/")
        blocks.append("\n".join(lines))
    return "\n".join(blocks)


def summary(memory: Memory) -> str:
    """Open goals and the last few ended, for /goals and the command line."""
    goals, ended = open_goals(memory), _recent_ended(memory)
    if not goals and not ended:
        return "No goals yet. /goal <objective> opens one."
    lines = ["Open goals:" if goals else "No goals open."]
    for g in goals:
        lines.append(f"{_line(g)}, ends {_local(g.deadline, '%d %b')}")
        last = notes(memory, g.id, 1, ("progress",))
        if last:
            lines.append(f"   latest: {last[0][3]}")
    if ended:
        lines += ["", "Lately ended:"]
        lines += [f"#{g.id} {g.objective} [{g.status} {_local(g.ended, '%d %b')}]" for g in ended]
    lines += ["", "/goal N shows one; /goal pause|resume|drop|done N, /goal budget N 2M, /goal days N 30, "
                  "/goal note N <text>."]
    return "\n".join(lines)


def detail(memory: Memory, goal_id) -> str:
    g = get(memory, goal_id)
    lines = [f"Goal #{g.id}: {g.objective}",
             f"Set by {'Ahab' if g.who == 'own' else 'Ahab, approved' if g.who == 'approved' else 'the owner'}"
             f" {_local(g.created)}. Status: {g.state}" + (f" ({g.status_note})" if g.status_note else ""),
             f"Done means: {g.done_when or '(not written yet)'}" + (f". Stop when: {g.stop_when}" if g.stop_when else ""),
             f"Spent {compact(g.spent)} of {compact(g.budget)} ({g.percent}%). Ends {_local(g.deadline)}. "
             f"Pace {_pace_words(g.pace)}.", ""]
    if g.evidence:
        lines.insert(4, f"Evidence: {g.evidence}")
    lines += [f"{_local(t, '%d %b %H:%M')} {by}: {text}" for t, by, _, text in notes(memory, g.id)
              if not (by == "code" and _ == "charge")][-40:]
    return "\n".join(lines)


def check(memory: Memory, id=None) -> str:
    """Ahab's check_goal: one goal's whole log, or every open goal and the last few ended."""
    if id in (None, ""):
        return summary(memory)
    return detail(memory, id)


# --- the owner's controls (Telegram's /goal and the command line) ------------------------------

def owner_start(memory: Memory, objective: str) -> str:
    _table(memory)
    objective = _clean(objective, "The objective", OBJECTIVE_CHAR_LIMIT)
    total, _ = _counts(memory)
    if total >= MAX_OPEN:
        raise GoalError(f"{total} goals are open already; {MAX_OPEN} is the limit. Drop or finish one first.")
    goal_id = _create(memory, "owner", objective, "", "", OWNER_DEFAULT_BUDGET, OWNER_DEFAULT_DAYS,
                      DEFAULT_PACE_HOURS * 60)
    return (f"Opened goal #{goal_id}: {compact(OWNER_DEFAULT_BUDGET)} tokens, {OWNER_DEFAULT_DAYS} days. Ahab "
            f"wakes for it now and writes down what done means. /goal budget {goal_id} 2M or /goal days {goal_id} 60 "
            "changes the limits.")


def owner_action(memory: Memory, action: str, goal_id, value=None) -> str:
    goal = get(memory, goal_id)
    now = _now()
    said = f"Your owner {{}} goal #{goal.id} ({goal.objective})"
    if action == "note":
        text = _clean(value, "The note", NOTE_CHAR_LIMIT)
        _note(memory, goal.id, "owner", "note", text)
        memory.db.commit()
        memory.record("goal", said.format("added a note to") + f": {text}")
        return f"Noted on #{goal.id}."
    if goal.status in ENDED and action not in ("resume",):
        raise GoalError(f"#{goal.id} is {goal.status}. /goal resume {goal.id} reopens it.")
    if action == "pause":
        if goal.status == "paused":
            return f"#{goal.id} is already paused."
        memory.db.execute("UPDATE goals SET status = 'paused', status_note = ? WHERE id = ?",
                          (_clean(value, "note", NOTE_CHAR_LIMIT, required=False) or None, goal.id))
        text, event = f"Paused #{goal.id}.", said.format("paused")
    elif action == "resume":
        deadline = max(datetime.fromisoformat(goal.deadline), now + timedelta(days=1))
        budget = max(goal.budget, goal.spent + max(goal.budget // 4, 1_000))
        memory.db.execute("UPDATE goals SET status = 'active', status_note = NULL, ended = NULL, due = ?, "
                          "deadline = ?, budget = ? WHERE id = ?", (_iso(now), _iso(deadline), budget, goal.id))
        text = f"Resumed #{goal.id}; Ahab wakes for it now."
        if budget != goal.budget:
            text += f" Its budget was used up, so it's now {compact(budget)}."
        event = said.format("resumed")
    elif action == "drop":
        memory.db.execute("UPDATE goals SET status = 'dropped', ended = ?, waiting_until = NULL WHERE id = ?",
                          (_iso(now), goal.id))
        text, event = f"Dropped #{goal.id}.", said.format("dropped")
    elif action == "done":
        memory.db.execute("UPDATE goals SET status = 'done', ended = ?, evidence = COALESCE(evidence, ?), "
                          "waiting_until = NULL WHERE id = ?", (_iso(now), "Closed by the owner.", goal.id))
        text, event = f"Marked #{goal.id} done.", said.format("marked done")
    elif action == "budget":
        budget = tokens(value)
        if not 1_000 <= budget <= MAX_BUDGET:
            raise GoalError(f"A budget is from 1k to {compact(MAX_BUDGET)} tokens.")
        _set_budget(memory, goal, budget)
        text, event = f"#{goal.id}'s budget is now {compact(budget)}.", said.format(f"set the budget to {compact(budget)} on")
    elif action == "days":
        try:
            days = float(value)
        except (TypeError, ValueError):
            raise GoalError("Days left is a number, like 30.") from None
        days = _whole(days, "Days left", 1, MAX_DAYS)
        memory.db.execute("UPDATE goals SET deadline = ? WHERE id = ?", (_iso(now + timedelta(days=days)), goal.id))
        text, event = f"#{goal.id} now ends in {days:g} days.", said.format(f"gave {days:g} more days to")
    else:
        raise GoalError(f"No goal action {action!r}.")
    _note(memory, goal.id, "owner", "status", text)
    memory.db.commit()
    memory.record("goal", event + ".")
    return text


def _set_budget(memory: Memory, goal: Goal, budget: int) -> None:
    percent = goal.spent * 100 // budget
    checked = max([c for c in CHECKINS if c <= percent], default=0)
    status = goal.status
    if status == "budget_limited" and goal.spent < budget:
        status = "active"
    who = "approved" if goal.who == "own" and budget > OWN_BUDGET else goal.who
    memory.db.execute("UPDATE goals SET budget = ?, checked = ?, status = ?, who = ?, due = "
                      "CASE WHEN ? = 'active' AND status = 'budget_limited' THEN ? ELSE due END WHERE id = ?",
                      (budget, checked, status, who, status, _iso(_now()), goal.id))


ACTIONS = ("pause", "resume", "drop", "done", "budget", "days", "note")


def telegram_command(memory: Memory, text: str) -> str | None:
    """The owner's /goal and /goals, from the paired chat. None if the text isn't one."""
    words = text.strip().split(None, 1)
    if not words or words[0].lower().split("@")[0] not in ("/goal", "/goals"):
        return None
    rest = words[1].strip() if len(words) > 1 else ""
    try:
        if not rest:
            return summary(memory)
        parts = rest.split(None, 2)
        if parts[0].isdigit():
            return detail(memory, parts[0])
        if parts[0].lower() in ACTIONS and len(parts) >= 2 and parts[1].lstrip("#").isdigit():
            return owner_action(memory, parts[0].lower(), parts[1].lstrip("#"), parts[2] if len(parts) > 2 else None)
        return owner_start(memory, rest)
    except GoalError as e:
        return str(e)


# --- approvals: a big goal, and more budget or time --------------------------------------

def _goal_card(payload: dict) -> str:
    objective = _clean(payload.get("objective"), "objective", OBJECTIVE_CHAR_LIMIT)
    done_when = _clean(payload.get("done_when"), "done_when", DONE_CHAR_LIMIT)
    budget = tokens(payload.get("budget"))
    days = payload.get("days")
    pace = payload.get("pace")
    if not 1_000 <= budget <= MAX_BUDGET or not isinstance(days, (int, float)) or not 1 <= days <= MAX_DAYS \
            or not isinstance(pace, int) or not MIN_PACE <= pace <= MAX_PACE:
        raise ValueError("That goal's budget, days or pace is out of range.")
    stop = f"\nStop when: {payload['stop_when']}" if payload.get("stop_when") else ""
    return (f"A goal: {objective}\nDone means: {done_when}{stop}\n"
            f"Budget: {compact(budget)} tokens. Time: {days:g} days. It wakes Ahab after {_pace_words(pace)} untouched.")


def _goal_run(memory: Memory, payload: dict) -> str:
    _table(memory)
    total, _ = _counts(memory)
    if total >= MAX_OPEN:
        raise GoalError(f"{MAX_OPEN} goals are open, the limit, so it couldn't be opened. Finish one, then ask again.")
    row = memory.db.execute("SELECT id FROM approvals WHERE kind = 'goal' AND status = 'running' "
                            "ORDER BY id DESC LIMIT 1").fetchone()
    goal_id = _create(memory, "approved", payload["objective"], payload["done_when"], payload.get("stop_when") or "",
                      tokens(payload["budget"]), payload["days"], payload["pace"], approval=row[0] if row else None)
    return f"opened goal #{goal_id}; its folder is {folder(goal_id, payload['objective'])}/."


def _budget_card(payload: dict) -> str:
    more, days = int(payload.get("more") or 0), payload.get("days") or 0
    if more < 0 or not isinstance(days, (int, float)) or (not more and not days):
        raise ValueError("A budget request needs more tokens or days.")
    asks = " and ".join(x for x in (f"{compact(more)} more tokens" if more else "",
                                    f"{days:g} more days" if days else "") if x)
    return (f"More for goal #{int(payload['goal'])}: {asks}.\n"
            f"The goal: {payload.get('objective', '?')}\n"
            f"Done means: {payload.get('done_when') or '(not written)'}\n"
            f"So far: {compact(int(payload.get('spent') or 0))} of {compact(int(payload.get('budget') or 0))} "
            f"tokens, ends {_local(payload.get('deadline'))}.\nProgress lately: {payload.get('lately', '')}")


def _budget_run(memory: Memory, payload: dict) -> str:
    goal = get(memory, payload["goal"])
    if goal.status in ENDED:
        return f"goal #{goal.id} was already {goal.status}, so nothing changed."
    more, days = int(payload.get("more") or 0), payload.get("days") or 0
    if more:
        _set_budget(memory, goal, min(MAX_BUDGET, goal.budget + more))
    if days:
        deadline = max(datetime.fromisoformat(goal.deadline), _now()) + timedelta(days=days)
        memory.db.execute("UPDATE goals SET deadline = ? WHERE id = ?", (_iso(deadline), goal.id))
    goal = get(memory, goal.id)
    _note(memory, goal.id, "owner", "status", f"Approved more: budget {compact(goal.budget)}, ends {_local(goal.deadline)}.")
    memory.db.commit()
    return f"goal #{goal.id} now has {compact(goal.budget)} tokens and ends {_local(goal.deadline)}."


approvals.register("goal", _goal_card, _goal_run)
approvals.register("goal_budget", _budget_card, _budget_run)


# --- command line ------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="See and steer Ahab's goals.")
    parser.add_argument("id", nargs="?", type=int, help="show one goal and its log")
    parser.add_argument("--start", metavar="OBJECTIVE", help="open an owner goal")
    for action in ("pause", "resume", "drop", "done"):
        parser.add_argument(f"--{action}", type=int, metavar="N")
    parser.add_argument("--budget", nargs=2, metavar=("N", "TOKENS"))
    parser.add_argument("--days", nargs=2, metavar=("N", "DAYS"))
    parser.add_argument("--note", nargs=2, metavar=("N", "TEXT"))
    parser.add_argument("--home", type=Path, default=DEFAULT_HOME)
    args = parser.parse_args()
    sys.stdout.reconfigure(errors="replace")
    memory = Memory(args.home)
    try:
        if args.start:
            print(owner_start(memory, args.start))
        elif any(getattr(args, a) is not None for a in ("pause", "resume", "drop", "done")):
            action = next(a for a in ("pause", "resume", "drop", "done") if getattr(args, a) is not None)
            print(owner_action(memory, action, getattr(args, action)))
        elif args.budget or args.days or args.note:
            action, (goal_id, value) = next((a, getattr(args, a)) for a in ("budget", "days", "note") if getattr(args, a))
            print(owner_action(memory, action, goal_id, value))
        elif args.id is not None:
            print(detail(memory, args.id))
        else:
            print(summary(memory))
    except GoalError as e:
        sys.exit(str(e))
    finally:
        memory.close()


if __name__ == "__main__":
    main()
