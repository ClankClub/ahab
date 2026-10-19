"""Identity files: who Perro is (SOUL.md), who its owner is (USER.md), what
the owner wants brought up unasked (PROACTIVE_PREFERENCES.md), how Perro
gets work done here (AGENTS.md: its own conventions, tool quirks and lessons,
written by Perro, like Muse's), and what it remembers (MEMORY.md: facts,
decisions, projects and what happened, dated, the way Muse and OpenClaw keep
one).

All five live in Perro's workspace, so it can edit them with its ordinary
file tools, and all five are shown at the top of every wake-up page.

MEMORY.md is the summary; the detail lives in workspace/memory/: a page per
person, Perro's opinions, what it learned about the world, and a short note
for each day, written by the dream. The page lists that folder by each
file's first line (as with skills) and shows the latest day in full; the
rest is a read_file or a recall away. Under all of it is the log, which
keeps every word verbatim (see memory.py).
PROACTIVE_PREFERENCES.md records the owner's wishes, so Perro writes there
only what the owner has told it. That's the
difference from the notes, which are working memory rewritten every wake:
these change slowly, and the owner can read and correct them.

A new home has neither, so each wake-up copies in a starter from templates/
for any that are missing. An existing file is never overwritten; deleting
one gets a fresh starter at the next wake-up.

The rules Perro works by stay in the heartbeat's prompt, not here, so no
file in the workspace can talk it out of them.
"""

from __future__ import annotations

from pathlib import Path

from perro import skills
from perro.memory import Memory
from perro.tools import workspace_root

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
# (file, heading on the page). Shown in this order.
FILES = (("SOUL.md", "Who you are"), ("USER.md", "Your owner"),
         ("PROACTIVE_PREFERENCES.md", "What to bring up on your own"),
         ("AGENTS.md", "How you work here"), ("MEMORY.md", "What you remember"))
CHAR_LIMIT = 8000
# AGENTS.md holds the lessons that pile up from doing the work, plus the
# owner's own guide to how Perro should sound, so it gets more room;
# MEMORY.md is everything worth keeping, so it gets the most. All of it is
# paid for on every wake-up, which is what a cap is for.
LIMITS = {"AGENTS.md": 10_000, "MEMORY.md": 20_000}

MEMORY_FOLDER = "memory"
DAYS = "daily"                 # memory/daily/<YYYY-MM-DD>.md, one per day, by the dream
DAY_NOTE_CHAR_LIMIT = 8000
MAX_LISTED = 80


def limit(filename: str) -> int:
    """How long an identity file may be."""
    return LIMITS.get(filename, CHAR_LIMIT)


def limits_text() -> str:
    """The caps in words, for the prompts."""
    extra = ", ".join(f"{name} under {cap}" for name, cap in LIMITS.items())
    return f"{CHAR_LIMIT} characters ({extra})"


def seed(memory: Memory, name: str | None) -> list[str]:
    """Copy in the starter for each identity file that's missing. Returns what was added."""
    root = workspace_root(memory)
    added = []
    for filename, _ in FILES:
        target, template = root / filename, TEMPLATES / filename
        if target.exists() or not template.is_file():
            continue
        text = template.read_text(encoding="utf-8").replace("{name}", name or "the assistant")
        target.write_text(text, encoding="utf-8")
        added.append(filename)
    return added


def _read(path: Path) -> str:
    if not path.is_file():
        return f"(no {path.name} yet; you can write one)"
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    cap = limit(path.name)
    if len(text) > cap:
        text = (text[:cap] + f"\n[cut: {path.name} is {len(text)} characters,"
                f" over the {cap} limit. Trim it.]")
    return text or "(empty)"


def describe(memory: Memory) -> str:
    """The identity sections at the top of Perro's page."""
    root = workspace_root(memory)
    return "\n\n".join(f"## {heading} ({filename})\n{_read(root / filename)}"
                       for filename, heading in FILES)


def _memory_files(root: Path) -> list[Path]:
    folder = root / MEMORY_FOLDER
    if not folder.is_dir():
        return []
    # A link planted in the folder could point anywhere; only what really
    # lives in the workspace counts.
    return sorted((p for p in folder.rglob("*") if p.is_file() and not p.name.startswith(".")
                   and p.resolve().is_relative_to(root)), key=lambda p: p.as_posix().lower())


def memory_folder(memory: Memory) -> str:
    """The memory folder's section of the page: each file by its first line,
    and the latest day's note in full."""
    root = workspace_root(memory)
    files = _memory_files(root)
    days = [p for p in files if p.parent == root / MEMORY_FOLDER / DAYS and p.suffix == ".md"]
    pages = [p for p in files if p not in days]
    lines = [f"- {p.relative_to(root / MEMORY_FOLDER).as_posix()}: {skills._describe(p)}"
             for p in pages[:MAX_LISTED]]
    if len(pages) > MAX_LISTED:
        lines.append(f"- ...and {len(pages) - MAX_LISTED} more (list_files {MEMORY_FOLDER} to see them all)")
    if days:
        lines.append(f"- {DAYS}/: {len(days)} daily note(s), {days[0].stem} to {days[-1].stem}; "
                     "the latest is below, the rest are there to read")
    section = f"## Your memory folder ({MEMORY_FOLDER}/)\n" + ("\n".join(lines) or (
        "(nothing yet. A page for each person in people/<name>.md, your own views in "
        "opinions.md, what you learn about the world in world.md, and any other file you "
        f"want to keep there; your dream writes {DAYS}/.)"))
    if days:
        latest = days[-1]
        text = latest.read_text(encoding="utf-8", errors="replace").strip()
        if len(text) > DAY_NOTE_CHAR_LIMIT:
            text = text[:DAY_NOTE_CHAR_LIMIT] + f"\n[cut: the rest is in {MEMORY_FOLDER}/{DAYS}/{latest.name}]"
        section += f"\n\n## Your last day, in short ({MEMORY_FOLDER}/{DAYS}/{latest.name})\n{text or '(empty)'}"
    return section
