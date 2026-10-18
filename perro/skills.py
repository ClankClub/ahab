"""Skills: know-how Perro keeps for itself, apart from its notes.

Notes are Perro's journal: what happened, what it concluded, capped at 2,400
characters and rewritten every wake. Know-how ("search ranks by word match,
so phrase queries carefully") is a different kind of thing. It doesn't
change from wake to wake, and it only matters when Perro is doing that
particular thing, yet kept in the notes it takes up room every time.

So a skill is just a text file in workspace/skills/, written and edited by
Perro with its ordinary file tools. Every wake-up its page lists each skill
by name and first line, which is all it costs until Perro actually needs
one; then it reads the whole file with read_file. Claude's skills and
OpenClaw's SKILL.md files work the same way ("progressive disclosure": a
short description always visible, the full text loaded only when needed).

This module builds that list. Perro writes the skills: awake, with
write_file, and in its dream, which is asked each day what it worked out
that it would otherwise have to work out again (Hermes's habit). review()
gives the dream each skill's last use from the log, so it can fix what went
wrong, merge what overlaps and retire what's dead. A retired skill moves to
skills/retired/, out of the list but not gone.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from perro.memory import Memory
from perro.tools import workspace_root

FOLDER = "skills"
RETIRED = "retired"
# Keep the list from costing as much as the notes it's meant to relieve.
MAX_LISTED = 30
DESCRIPTION_CHAR_LIMIT = 200
FIRST_LINE_BYTES = 4096       # how much of each file is read to find its first line


def _first_line(path) -> str:
    with open(path, "rb") as f:
        head = f.read(FIRST_LINE_BYTES).decode("utf-8", errors="replace")
    for line in head.splitlines():
        # Strip Markdown heading/list marks, so "# How I search" lists as "How I search".
        line = line.strip().lstrip("#-* ").strip()
        if line:
            if len(line) > DESCRIPTION_CHAR_LIMIT:
                line = line[:DESCRIPTION_CHAR_LIMIT] + "..."
            return line
    return "(empty)"


def _skills(root: Path) -> list[Path]:
    """The live skill files, retired ones left out."""
    folder = root / FOLDER
    if not folder.is_dir():
        return []
    files = []
    for path in sorted(folder.rglob("*"), key=lambda p: p.as_posix().lower()):
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.relative_to(folder).parts[0] == RETIRED:
            continue
        # A link planted inside the folder could point anywhere; only list
        # files that really live in the workspace.
        if not path.resolve().is_relative_to(root):
            continue
        files.append(path)
    return files


def _describe(path: Path) -> str:
    try:
        return _first_line(path)
    except OSError:
        return "(couldn't be read)"


def describe(memory: Memory) -> str:
    """The "Your skills" section of Perro's page."""
    root = workspace_root(memory)
    files = _skills(root)
    if not files:
        return "(none)"
    lines = [f"- {path.relative_to(root).as_posix()}: {_describe(path)}" for path in files[:MAX_LISTED]]
    if len(files) > MAX_LISTED:
        lines.append(f"- ...and {len(files) - MAX_LISTED} more (list_files {FOLDER} to see them all)")
    return "\n".join(lines)


def _clean(path: str) -> str:
    path = str(path).strip().replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path


def last_read(memory: Memory) -> dict[str, str]:
    """When each workspace file was last opened with read_file, from the whole log
    (path: ISO time)."""
    seen = {}
    for when, content in memory.db.execute(
            "SELECT time, content FROM events WHERE kind = 'tool' AND content LIKE 'read_file %' ORDER BY id"):
        lines = content.split("\n", 2)
        if len(lines) < 2 or not lines[1].startswith("args: "):
            continue
        try:
            path = json.loads(lines[1][len("args: "):]).get("path")
        except (ValueError, AttributeError):
            continue
        if path:
            seen[_clean(path)] = when
    return seen


def review(memory: Memory, day_start: str) -> str:
    """The skill list for the dream: each skill with when it was last used.
    day_start (ISO) is where the dream's day begins, to say "used today"."""
    root = workspace_root(memory)
    files = _skills(root)
    if not files:
        return "(none yet)"
    reads = last_read(memory)
    lines = [f"{len(files)} skill(s); your page lists the first {MAX_LISTED}."]
    for path in files:
        name = path.relative_to(root).as_posix()
        read = reads.get(name)
        if read and read >= day_start:
            used = f"read today, {datetime.fromisoformat(read).astimezone():%H:%M}: did it hold up?"
        elif read:
            used = f"last read {datetime.fromisoformat(read).astimezone():%d %b}"
        else:
            used = f"never read since it was written ({datetime.fromtimestamp(path.stat().st_mtime):%d %b})"
        lines.append(f"- {name}: {_describe(path)} [{used}]")
    return "\n".join(lines)


def is_skill(path: str) -> bool:
    parts = _clean(path).split("/")
    return len(parts) > 1 and parts[0] == FOLDER and parts[1] != RETIRED


def retire(memory: Memory, path: str) -> str:
    """Move a skill to skills/retired/, out of the list. Returns what happened."""
    from perro.tools import ToolError, _resolve
    try:
        source = _resolve(memory, _clean(path))
    except ToolError as e:
        return f"refused: {e}"
    if not source.is_file():
        return "no such skill"
    root = workspace_root(memory)
    target = root / FOLDER / RETIRED / source.relative_to(root / FOLDER)
    if target.exists():
        target = target.with_name(f"{target.stem}-{datetime.now():%Y%m%d%H%M}{target.suffix}")
    target.parent.mkdir(parents=True, exist_ok=True)
    source.rename(target)
    return f"retired to {target.relative_to(root).as_posix()}"
