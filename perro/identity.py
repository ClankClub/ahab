"""Identity files: who Perro is (SOUL.md), who its owner is (USER.md), and
what the owner wants brought up unasked (PROACTIVE_PREFERENCES.md).

All three live in Perro's workspace, so it can edit them with its ordinary
file tools, and all three are shown at the top of every wake-up page. The
last one records the owner's wishes, so Perro writes there only what the
owner has told it. That's the
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

from perro.memory import Memory
from perro.tools import workspace_root

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
# (file, heading on the page). Shown in this order.
FILES = (("SOUL.md", "Who you are"), ("USER.md", "Your owner"),
         ("PROACTIVE_PREFERENCES.md", "What to bring up on your own"))
CHAR_LIMIT = 3000


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
    if len(text) > CHAR_LIMIT:
        text = (text[:CHAR_LIMIT] + f"\n[cut: {path.name} is {len(text)} characters,"
                f" over the {CHAR_LIMIT} limit. Trim it.]")
    return text or "(empty)"


def describe(memory: Memory) -> str:
    """The identity sections at the top of Perro's page."""
    root = workspace_root(memory)
    return "\n\n".join(f"## {heading} ({filename})\n{_read(root / filename)}"
                       for filename, heading in FILES)
