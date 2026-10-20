"""The weekly memory report: is Ahab actually using its memory?

Ahab has a MEMORY.md, a memory folder and a search over its whole past
(see memory.py and identity.py). Having them doesn't mean using them. This
counts, from the log and the workspace, the things worth watching:

  - writing as it goes: edits to MEMORY.md and memory/ during ordinary
    wake-ups, apart from what the dream wrote
  - tidiness: MEMORY.md's size, against the last report
  - looking things up: recall and read_conversation, and how often its
    messages to the owner say it doesn't remember
  - day notes: one for each day it talked with the owner

No model is involved, so it costs nothing. The watchdog, which runs every
10 minutes anyway, sends it on Telegram once a week, on Sunday evening (or
as soon after as the PC is on). `py -m ahab.report` prints it now.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ahab import identity
from ahab.memory import DEFAULT_HOME, Memory

STATE_FILE = "report.json"
WEEKDAY, HOUR = 6, 19          # Sunday, 7pm local time
DAYS = 7
MEMORY_PATHS = ("MEMORY.md", "memory/")
WRITE_TOOLS = ("edit_file", "write_file")
WROTE = ("Created", "Overwrote", "Edited", "Added")
FORGOT = re.compile(r"\b(?:don't|do not|can't|cannot|no longer) (?:remember|recall)\b|\bno (?:memory|record) of\b"
                    r"|\bI(?:'ve| have) forgotten\b", re.I)


def _clean(path: str) -> str:
    return str(path).strip().replace("\\", "/").removeprefix("./")


def _is_memory(path: str) -> bool:
    path = _clean(path)
    return path == MEMORY_PATHS[0] or path.startswith(MEMORY_PATHS[1])


def _args(content: str) -> dict:
    """A tool event's args, from its "args: {...}" line."""
    lines = content.split("\n", 2)
    if len(lines) < 2 or not lines[1].startswith("args: "):
        return {}
    try:
        args = json.loads(lines[1][len("args: "):])
    except ValueError:
        return {}
    return args if isinstance(args, dict) else {}


def _load(home: Path) -> dict:
    try:
        return json.loads((home / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(home: Path, state: dict) -> None:
    (home / STATE_FILE).write_text(json.dumps(state, indent=2), encoding="utf-8")


def gather(memory: Memory, now: datetime, days: int = DAYS) -> dict:
    """The week's numbers (now is aware, local or UTC)."""
    since = (now - timedelta(days=days)).astimezone(timezone.utc).isoformat(timespec="seconds")
    rows = memory.db.execute("SELECT time, kind, content FROM events WHERE time >= ? ORDER BY id",
                             (since,)).fetchall()
    awake, dream_files, lookups, forgot, talked, dreams = [], [], {}, [], set(), 0
    feedback = 0
    for when, kind, content in rows:
        if kind == "tool":
            name, _, rest = content.partition(" ")
            if name in WRITE_TOOLS:
                path = _args(content).get("path", "")
                result = rest.split(" -> ", 1)[-1].split("\n", 1)[0]
                if _is_memory(path) and result.startswith(WROTE):
                    awake.append(str(path))
                elif _clean(path) == identity.FEEDBACK and result.startswith(WROTE):
                    feedback += 1
            elif name in ("recall", "read_conversation"):
                lookups[name] = lookups.get(name, 0) + 1
        elif kind == "dream":
            dreams += 1
            m = re.search(r" Changed (.+?)\. (?:Not saved|Tell your|\[|$)", content.split("\n", 1)[0])
            if m:
                dream_files += [f for f in m.group(1).split(", ") if _is_memory(f)]
                feedback += identity.FEEDBACK in m.group(1).split(", ")
        elif kind == "message":
            first, _, text = content.partition("\n")
            if first.startswith("From the owner"):
                talked.add(datetime.fromisoformat(when).astimezone().strftime("%Y-%m-%d"))
            elif FORGOT.search(text):
                forgot.append(" ".join(text.split())[:120])
    root = memory.home / "workspace"
    daily = root / identity.MEMORY_FOLDER / identity.DAYS
    today = now.astimezone().strftime("%Y-%m-%d")
    # Today's note isn't due until tomorrow's dream.
    missing = sorted(d for d in talked if d != today and not (daily / f"{d}.md").exists())
    memory_md = root / "MEMORY.md"
    people = root / identity.MEMORY_FOLDER / "people"
    return {
        "awake": awake, "dream_files": dream_files, "dreams": dreams, "lookups": lookups,
        "forgot": forgot, "talked": sorted(talked), "missing_days": missing, "feedback": feedback,
        "memory_chars": len(memory_md.read_text(encoding="utf-8", errors="replace")) if memory_md.is_file() else 0,
        "people": sorted(p.stem for p in people.glob("*.md")) if people.is_dir() else [],
    }


def describe(name: str, n: dict, last_chars: int | None) -> str:
    """The report as a Telegram message."""
    files = sorted(set(n["awake"]))
    lines = [f"[memory report] {name}, the last {DAYS} days:"]
    if n["awake"]:
        lines.append(f"Wrote to memory while awake {len(n['awake'])} time(s): {', '.join(files)}.")
    else:
        lines.append("Wrote to memory while awake: never. Everything it kept came from the dream." if n["dream_files"]
                     else "Wrote to memory while awake: never.")
    lines.append(f"The dream ran {n['dreams']} time(s) and changed {len(n['dream_files'])} memory file(s).")
    size = f"MEMORY.md is {n['memory_chars']:,} characters (cap {identity.limit('MEMORY.md'):,})"
    if last_chars is not None:
        change = n["memory_chars"] - last_chars
        size += f", {'+' if change >= 0 else ''}{change:,} since the last report"
    lines.append(size + ".")
    if n["people"]:
        lines.append(f"People pages: {len(n['people'])} ({', '.join(n['people'][:12])}"
                     + (", ..." if len(n["people"]) > 12 else "") + ").")
    recall, conv = n["lookups"].get("recall", 0), n["lookups"].get("read_conversation", 0)
    lines.append(f"Looked things up: recall {recall}, read_conversation {conv}.")
    if n["forgot"]:
        lines.append(f"Said it didn't remember {len(n['forgot'])} time(s), latest: \"{n['forgot'][-1]}\"")
    else:
        lines.append("Never told you it didn't remember.")
    if n.get("feedback"):
        lines.append(f"Added to {identity.FEEDBACK} {n['feedback']} time(s): problems or ideas for you "
                     f"(home/workspace/{identity.FEEDBACK}).")
    lines.append(f"Talked on {len(n['talked'])} day(s)" + (
        f"; no day note for {', '.join(n['missing_days'])}." if n["missing_days"] else ", each with its day note."))
    return "\n".join(lines)


def _slot(now: datetime) -> datetime:
    """The most recent Sunday 7pm at or before now (local)."""
    local = now.astimezone()
    slot = (local - timedelta(days=(local.weekday() - WEEKDAY) % 7)).replace(hour=HOUR, minute=0, second=0, microsecond=0)
    return slot if slot <= local else slot - timedelta(days=7)


def build(home: Path, now: datetime) -> tuple[str, dict]:
    memory = Memory(home)
    try:
        numbers = gather(memory, now)
    finally:
        memory.close()
    try:
        name = json.loads((home / "home.json").read_text(encoding="utf-8")).get("name") or "Ahab"
    except (OSError, ValueError, AttributeError):
        name = "Ahab"
    return describe(name, numbers, _load(home).get("memory_chars")), numbers


def send_if_due(home: Path, now: datetime, send) -> str | None:
    """Called by the watchdog each round: once a week, build and send the
    report. A new home starts counting from now, rather than sending at once.
    Returns what was sent."""
    state, slot = _load(home), _slot(now).timestamp()
    if "last_slot" not in state:
        _save(home, {**state, "last_slot": slot})
        return None
    if state["last_slot"] >= slot:
        return None
    text, numbers = build(home, now)
    if not send(text):
        return None                                   # tried again next round
    _save(home, {"last_slot": slot, "memory_chars": numbers["memory_chars"]})
    return text


def main() -> None:
    parser = argparse.ArgumentParser(description="Print Ahab's weekly memory report.")
    parser.add_argument("--home", type=Path, default=DEFAULT_HOME)
    parser.add_argument("--send", action="store_true", help="also send it to the owner on Telegram now")
    args = parser.parse_args()
    sys.stdout.reconfigure(errors="replace")
    text, numbers = build(args.home, datetime.now().astimezone())
    print(text)
    if args.send:
        from ahab.watchdog import telegram_sender
        send = telegram_sender(args.home)
        if send is None or not send(text):
            sys.exit("Couldn't send it (is the Telegram bot set up?).")
        _save(args.home, {**_load(args.home), "memory_chars": numbers["memory_chars"]})
        print("Sent.")


if __name__ == "__main__":
    main()
