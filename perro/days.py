"""Days: Perro's event log rewritten as one readable Markdown file per day.

This is for you, not for Perro. memory.db is the real memory; these files are
a view of it, regenerated from scratch every time (so they can never drift
from the log), written to <home>/days/, outside the workspace. Perro can't see
them. The backup pushes them to GitHub, where they render nicely on a phone.

  days/README.md         index: one line per day, with wake and token counts
  days/2026-10-17.md     every wake that day, in your local time

Thoughts and sleeps are shown in full. Long things (private reasoning, tool
results, notes rewrites) are folded into click-to-open sections.
"""

from __future__ import annotations

import html
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from perro.memory import DEFAULT_HOME

TOKENS = re.compile(r"(\d+) in, (\d+) out")


def load_events(home: Path) -> list[tuple[int, datetime, str, str]]:
    """Every event, oldest first, with its time converted to local time."""
    db = home / "memory.db"
    if not db.exists():
        return []
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=5)
    try:
        rows = con.execute("SELECT id, time, kind, content FROM events ORDER BY id").fetchall()
    finally:
        con.close()
    return [(i, datetime.fromisoformat(t).astimezone(), k, c) for i, t, k, c in rows]


def esc(text: str) -> str:
    """Perro's text is shown as text: a stray <tag> or leading > shouldn't
    turn into HTML or a quote block."""
    return html.escape(text, quote=False)


def fold(summary: str, body: str) -> str:
    if not body.strip():
        # Early events (before the log kept full text) have only a header.
        return "*(full text wasn't kept for this one)*\n"
    return f"<details><summary>{esc(summary)}</summary>\n\n{esc(body)}\n\n</details>\n"


def render_event(kind: str, content: str) -> str:
    first, _, rest = content.partition("\n")
    if kind == "thought":
        return f"**Thought:** {esc(content)}\n"
    if kind == "reasoning":
        return fold(f"Private reasoning ({len(rest)} chars)", rest)
    if kind == "tool":
        # A backtick in the args would end the code span early.
        return f"**Tool:** `{first.replace('`', chr(39))}`\n\n" + fold("Full call and result", rest)
    if kind == "notes":
        return f"**{esc(first)}**\n\n" + fold("The new notes", rest)
    if kind == "sleep":
        return f"**{esc(content)}**\n"
    if kind == "message":
        quoted = "\n".join(f"> {esc(line)}" if line else ">" for line in rest.splitlines())
        return f"**{esc(first.rstrip(':'))}:**\n\n{quoted}\n"
    if kind == "dream":
        return f"**{esc(first)}**\n\n" + fold("The whole dream", rest)
    if kind == "alarm":
        return f"**Alarm rang:** {esc(content)}\n"
    if kind == "error":
        return f"> **Error:** {esc(content)}\n"
    # start, stopped, anything new: a plain italic fact
    return f"*{esc(content)}*\n"


def render_day(day: str, events: list, first_wake: int) -> tuple[str, int, int, int]:
    """One day's Markdown. Returns (text, wakes, tokens_in, tokens_out)."""
    body, wakes, t_in, t_out = [], 0, 0, 0
    wake_no = first_wake
    for _, when, kind, content in events:
        if kind == "wake":
            wake_no += 1
            wakes += 1
            body.append(f"\n---\n\n### {when:%H:%M} — wake {wake_no}\n")
            continue
        if kind == "sleep" and (m := TOKENS.search(content)):
            t_in, t_out = t_in + int(m[1]), t_out + int(m[2])
        if kind in ("start", "stopped"):
            body.append(f"\n{when:%H:%M} · {render_event(kind, content)}")
        else:
            body.append(render_event(kind, content))
    date = datetime.strptime(day, "%Y-%m-%d")
    head = (f"# {date:%A %Y-%m-%d}\n\n"
            f"{wakes} wake{'s' * (wakes != 1)} · {t_in:,} tokens in · {t_out:,} out · "
            "times are local\n")
    return head + "\n".join(body) + "\n", wakes, t_in, t_out


def write_days(home: Path) -> int:
    """Regenerate every day file. Only touches files whose text changed.
    Returns how many files were written."""
    by_day: dict[str, list] = defaultdict(list)
    for e in load_events(home):
        by_day[f"{e[1]:%Y-%m-%d}"].append(e)
    out = home / "days"
    out.mkdir(exist_ok=True)

    written, index, wakes_so_far = 0, [], 0
    for day in sorted(by_day):
        text, wakes, t_in, t_out = render_day(day, by_day[day], wakes_so_far)
        wakes_so_far += wakes
        index.append(f"- [{day}]({day}.md) — {wakes} wakes, {t_in:,} in / {t_out:,} out")
        written += _write_if_changed(out / f"{day}.md", text)
    readme = ("# Perro's days\n\nGenerated from memory.db; Perro never sees these. "
              "Newest first.\n\n" + "\n".join(reversed(index)) + "\n")
    written += _write_if_changed(out / "README.md", readme)
    return written


def _write_if_changed(path: Path, text: str) -> int:
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return 0
    path.write_text(text, encoding="utf-8")
    return 1


if __name__ == "__main__":
    # python -m perro.days [home]
    home = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_HOME
    print(f"Wrote {write_days(home)} file(s) to {home / 'days'}.")
