"""A status screen for a machine that's Ahab's own.

  python3 -m ahab.console          full screen, redrawn every few seconds
  python3 -m ahab.console --once   print one frame and stop

On Linux the installer runs it on the machine's own screen (tty1) in place
of the login prompt, so a spare laptop shows what the agent is up to: asleep
or awake and until when, the last messages with the owner, the recent log,
whether the bridge, watchdog and backup are well, the model it runs on, the
tokens spent so far, any workers and crew agents at work, and the machine itself.
It only reads: the database is opened read-only, it spends no tokens, and it
takes no input, so there's no shell behind it for whoever is at the keyboard.
The owner can still log in on another console (Ctrl+Alt+F2).

Anyone in the room can read the screen, so home.json can hide the chat
(and with it what the log says, beyond sleeping and waking):

    "console": {"show_chat": false}
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import textwrap
import time
from datetime import datetime, timezone
from pathlib import Path

from ahab import sources
from ahab.memory import BACKGROUND_KINDS, DEFAULT_HOME, TOKEN_STATS
from ahab.model import BACKENDS

REFRESH = 5                      # seconds between frames
MARGIN = 2                       # columns left empty at the right edge: a line that fills the last
                                 # column gets its final character wiped by the clear-to-end-of-line
USAGE_KINDS = ("sleep", "dream", "digest", "web_search")   # events whose first line says what they cost
HELPER_LINES = 3                 # running helpers listed before "and N more"
BAD_ENDS = ("failed", "timed_out", "interrupted")
STALE = 3 * 60                   # an .alive file older than this means not running
SLEEP = re.compile(r"Sleeping (\d+) min")
PLAIN_KINDS = {"sleep", "wake", "start", "stopped", "watchdog", "dream"}   # shown even with the chat hidden
DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, YELLOW, RED, CYAN = "\033[32m", "\033[33m", "\033[31m", "\033[36m"


def _settings(home: Path) -> dict:
    try:
        return json.loads((home / "home.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _db(home: Path):
    path = home / "memory.db"
    if not path.is_file():
        return None
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=1)


def _when(iso: str) -> datetime | None:
    try:
        t = datetime.fromisoformat(iso)
    except ValueError:
        return None
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone()


def _rows(db, sql: str, n: int) -> list:
    try:
        return list(reversed(db.execute(sql, (n,)).fetchall()))
    except sqlite3.Error:
        return []


def state(home: Path, now: float) -> str:
    """'asleep until 07:00', 'awake', or what's wrong, from the last event."""
    db = _db(home)
    if db is None:
        return "no memory yet"
    try:
        skip = ", ".join("?" * len(BACKGROUND_KINDS))
        last = list(db.execute(f"SELECT time, kind, content FROM events WHERE kind NOT IN ({skip}) "
                               "ORDER BY id DESC LIMIT 1", BACKGROUND_KINDS).fetchall())
    finally:
        db.close()
    if not last:
        return "not woken yet"
    t, kind, content = last[0]
    when = _when(t)
    m = SLEEP.search(content) if kind == "sleep" else None
    if m and when:
        wake = when.timestamp() + int(m.group(1)) * 60
        if wake > now:
            at = datetime.fromtimestamp(wake)
            day = "" if at.date() == datetime.fromtimestamp(now).date() else f"{at:%a} "
            return f"asleep, next wake {day}{at:%H:%M}"
        return "asleep, waking soon"
    if kind == "error":
        return "last wake failed"
    return "awake"


def alive(home: Path, name: str, now: float) -> bool:
    try:
        return now - (home / f"{name}.alive").stat().st_mtime < STALE
    except OSError:
        return False


def last_backup(home: Path) -> str:
    if not (home / ".git").is_dir():
        return "off"
    try:
        out = subprocess.run(["git", "-C", str(home), "log", "-1", "--format=%ct"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        return f"{datetime.fromtimestamp(int(out)):%a %H:%M}" if out else "none yet"
    except (OSError, ValueError, subprocess.SubprocessError):
        return "?"


def source_ages(home: Path, c) -> list[str]:
    """One "hermes 3h ago" per listed source, red once a delivery is overdue."""
    out = []
    for name in sources.configured(home):
        received = sources.manifest(home, name).get("received", "")
        out.append(f"{name} " + c(RED if sources.stale(received) else GREEN, sources.age(received)))
    return out


def machine(home: Path) -> str:
    parts = []
    try:
        up = float(Path("/proc/uptime").read_text().split()[0])
        parts.append(f"up {int(up // 3600)}h{int(up % 3600 // 60):02d}")
    except (OSError, ValueError, IndexError):
        pass
    try:
        parts.append("load " + Path("/proc/loadavg").read_text().split()[0])
    except (OSError, IndexError):
        pass
    try:
        info = dict(l.split(":", 1) for l in Path("/proc/meminfo").read_text().splitlines() if ":" in l)
        total, free = (int(info[k].split()[0]) / 2**20 for k in ("MemTotal", "MemAvailable"))
        parts.append(f"RAM {total - free:.1f}/{total:.1f}G")
    except (OSError, KeyError, ValueError):
        pass
    try:
        d = shutil.disk_usage(home)
        parts.append(f"disk {d.used * 100 // d.total}%")
    except OSError:
        pass
    return " · ".join(parts)


def _clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[:max(0, width - 1)] + "…"


def compact(n: int) -> str:
    """14176736 -> '14.176 M': thousands, millions, billions with three decimals, cut not rounded."""
    for unit, size in (("B", 10**9), ("M", 10**6), ("k", 10**3)):
        if n >= size:
            thousandths = n * 1000 // size
            return f"{thousandths // 1000}.{thousandths % 1000:03d} {unit}"
    return str(n)


def backend_line(settings: dict) -> str:
    """'Codex → gpt-6-luna → Max': the model home.json picks, as tools._this_mind reads it."""
    name = settings.get("backend") or "deepseek"
    config = BACKENDS.get(name)
    if config is None:
        return f"{name} (unknown backend)"
    who = {"codex": "Codex", "claude": "Claude", "deepseek": "DeepSeek", "openrouter": "OpenRouter",
           "local": "Local"}.get(config.get("cli") or name, name)
    return " → ".join([who, config.get("model", "?")] + ([config["effort"].title()] if config.get("effort") else []))


_seen: dict[str, tuple[int, int, int]] = {}      # home -> (last event counted, tokens in, tokens out)


def spent(home: Path) -> tuple[int, int]:
    """Tokens used so far, in and out: the cost lines wakes, dreams, digests and searches log, plus what
    workers and crew agents report in their own tables. The log is read once and then only its new
    rows (it grows for years and the screen redraws every few seconds)."""
    db = _db(home)
    if db is None:
        return 0, 0
    key = str(home)
    last, t_in, t_out = _seen.get(key, (0, 0, 0))
    marks = ", ".join("?" * len(USAGE_KINDS))
    try:
        for ident, line in db.execute(
                f"SELECT id, substr(content, 1, instr(content || char(10), char(10))) FROM events "
                f"WHERE id > ? AND kind IN ({marks}) ORDER BY id", (last, *USAGE_KINDS)):
            last = ident
            if m := TOKEN_STATS.search(line):
                t_in, t_out = t_in + int(m[1]), t_out + int(m[2])
        _seen[key] = (last, t_in, t_out)
        for table in ("workers", "agent_tasks"):
            try:
                a, b = db.execute(f"SELECT COALESCE(SUM(tokens_in), 0), COALESCE(SUM(tokens_out), 0) "
                                  f"FROM {table}").fetchone()
                t_in, t_out = t_in + a, t_out + b
            except sqlite3.Error:
                pass                                  # the table appears with the first helper
    except sqlite3.Error:
        pass
    finally:
        db.close()
    return t_in, t_out


def helpers(home: Path, now: float, show_chat: bool, c, width: int) -> list[str]:
    """Workers and crew-agent tasks: a line for each one running (what it was asked, unless the chat
    is hidden), or, when none is, a count of those that finished in the last day."""
    db = _db(home)
    if db is None:
        return []
    cutoff = datetime.fromtimestamp(now - 24 * 3600, timezone.utc).isoformat(timespec="seconds")
    running, finished, bad = [], 0, 0
    try:
        for table, kind, column in (("workers", "worker", "model"), ("agent_tasks", "agent", "agent")):
            try:
                running += [(created, f"{kind} {who}", task) for created, who, task in db.execute(
                    f"SELECT created, {column}, task FROM {table} WHERE status IN ('running', 'cancelling')")]
                for (status,) in db.execute(f"SELECT status FROM {table} WHERE finished > ? "
                                            "AND status NOT IN ('running', 'cancelling', 'cancelled')", (cutoff,)):
                    finished, bad = finished + 1, bad + (status in BAD_ENDS)
            except sqlite3.Error:
                continue                              # the table appears with the first helper
    finally:
        db.close()
    running.sort()
    lines = []
    for created, label, task in running[:HELPER_LINES]:
        began = _when(created)
        mins = max(0, int((now - began.timestamp()) // 60)) if began else 0
        age = f"{mins}m" if mins < 60 else f"{mins // 60}h{mins % 60:02d}"
        prefix = f"helper {label:<14} {age:>5}  "
        lines.append(c(YELLOW, "helper") + prefix[6:] +
                     (_clip(" ".join(task.split()), width - len(prefix)) if show_chat else ""))
    if len(running) > HELPER_LINES:
        lines.append(c(DIM, f"…and {len(running) - HELPER_LINES} more running"))
    if not running and finished:
        text = f"helpers idle · {finished} finished in the last day" + (f", {bad} failed" if bad else "")
        lines.append(c(RED if bad else DIM, _clip(text, width)))
    return lines


def goals_line(home: Path, now: float, show_chat: bool, c, width: int) -> list[str]:
    """One line of the open goals: number, objective (unless the chat is hidden), share of the
    budget spent, and a word when one isn't simply active. Nothing when there are none."""
    db = _db(home)
    if db is None:
        return []
    try:
        rows = db.execute("SELECT id, objective, spent, budget, status, waiting_until FROM goals WHERE status IN "
                          "('active', 'paused', 'blocked', 'budget_limited') ORDER BY id").fetchall()
    except sqlite3.Error:
        return []                                     # the table appears with the first goal
    finally:
        db.close()
    if not rows:
        return []
    parts, bad = [], False
    for goal_id, objective, spent, budget, status, waiting_until in rows:
        until = _when(waiting_until) if waiting_until else None
        word = ("waiting" if status == "active" and until and until.timestamp() > now
                else {"active": "", "paused": "paused", "blocked": "blocked", "budget_limited": "out of budget"}[status])
        bad = bad or status in ("blocked", "budget_limited")
        title = " ".join(objective.split()[:5]) + " " if show_chat else ""
        parts.append(f"#{goal_id} {title}{spent * 100 // max(1, budget)}%" + (f" {word}" if word else ""))
    text = _clip(" · ".join(parts), width - 6)
    return [c(YELLOW, "goals") + " " + (c(RED, text) if bad else text)]


def frame(home: Path, width: int = 80, height: int = 24, now: float | None = None, color: bool = True) -> list[str]:
    """The screen as lines of text, at most `height` of them."""
    now = time.time() if now is None else now
    c = (lambda code, s: f"{code}{s}{RESET}") if color else (lambda code, s: s)
    settings = _settings(home)
    name = settings.get("name") or "Ahab"
    show_chat = (settings.get("console") or {}).get("show_chat", True) is not False
    rule = c(DIM, "─" * width)

    status = state(home, now)
    tone = RED if "failed" in status else GREEN if status.startswith("awake") else CYAN
    right = f"{datetime.fromtimestamp(now):%a %d %b %H:%M}"
    status = _clip(status, max(5, width - len(name) - len(right) - 5))
    gap = " " * max(1, width - len(name) - 3 - len(status) - len(right))
    head = c(BOLD, name.upper()) + " · " + c(tone, status) + gap + right

    t_in, t_out = spent(home)
    total = f"{compact(t_in + t_out)} tokens"
    mind = _clip(backend_line(settings), max(5, width - 3 - len(total)))
    split = f" ({compact(t_in)} in, {compact(t_out)} out)"
    fits = len(mind) + 3 + len(total) + len(split) <= width
    info = c(CYAN, mind) + " · " + c(BOLD, total) + (c(DIM, split) if fits else "")

    def ok(flag: bool, label: str) -> str:
        return f"{label} " + (c(GREEN, "ok") if flag else c(RED, "down"))

    foot = [rule,
            *helpers(home, now, show_chat, c, width),
            *goals_line(home, now, show_chat, c, width),
            "  ".join([ok(alive(home, "heartbeat", now), "heartbeat"), ok(alive(home, "telegram", now), "bridge"),
                       f"backup {last_backup(home)}", *source_ages(home, c)]),
            c(DIM, _clip(machine(home), width))]

    body_room = max(0, height - 3 - len(foot))
    chat_lines, log_lines = [], []
    db = _db(home)
    if db is not None:
        try:
            if show_chat:
                for t, sender, text in _rows(db, "SELECT time, sender, text FROM messages ORDER BY id DESC LIMIT ?", 6):
                    when = _when(t)
                    who = "You" if sender == "owner" else name
                    label = f"{who:<6}{when:%H:%M}  " if when else f"{who:<6}       "
                    wrapped = textwrap.wrap(" ".join(text.split()), max(10, width - len(label))) or [""]
                    chat_lines.append(c(BOLD if sender == "owner" else CYAN, label) + wrapped[0])
                    chat_lines += [" " * len(label) + w for w in wrapped[1:3]]
            for t, kind, content in _rows(db, "SELECT time, kind, content FROM events ORDER BY id DESC LIMIT ?", max(12, body_room)):
                when = _when(t)
                stamp = f"{when:%H:%M}" if when else "     "
                tone = RED if kind == "error" else YELLOW if kind in ("watchdog", "approval") else DIM
                if not show_chat and kind not in PLAIN_KINDS:
                    content = ""                     # messages, thoughts, mail and tool results stay private
                log_lines.append(f"{stamp}  " + c(tone, f"{kind[:10]:<10}") + " " +
                                 _clip(" ".join(content.split()), width - 18))
        finally:
            db.close()

    # Chat gets up to two thirds of the room, newest kept; the log takes the rest.
    chat_room = min(len(chat_lines), body_room * 2 // 3) if chat_lines else 0
    log_room = max(0, body_room - chat_room - (1 if chat_room else 0))
    body = chat_lines[len(chat_lines) - chat_room:] if chat_room else []
    if chat_room:
        body.append(rule)
    body += log_lines[max(0, len(log_lines) - log_room):]
    body += [""] * (body_room - len(body))
    return [head, info, rule, *body, *foot][:height]


def main() -> None:
    parser = argparse.ArgumentParser(description="Ahab's status screen.")
    parser.add_argument("--home", type=Path, default=DEFAULT_HOME)
    parser.add_argument("--once", action="store_true", help="print one frame and stop")
    args = parser.parse_args()
    if args.once:
        size = shutil.get_terminal_size((100, 30))
        print("\n".join(frame(args.home, size.columns, size.lines - 1, color=sys.stdout.isatty())))
        return
    out = sys.stdout
    out.write("\033[?25l")                              # hide the cursor
    try:
        while True:
            size = shutil.get_terminal_size((80, 25))
            lines = frame(args.home, max(20, size.columns - MARGIN), size.lines)
            out.write("\033[H" + "\n".join(l + "\033[K" for l in lines) + "\033[J")
            out.flush()
            time.sleep(REFRESH)
    except KeyboardInterrupt:
        pass
    finally:
        out.write("\033[?25h\033[0m\n")
        out.flush()


if __name__ == "__main__":
    main()
