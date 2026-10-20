"""Skills and the dream: what the dream is shown about skills and busy wake-ups, and how it
writes, refuses and retires them."""
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, ROOT)
from ahab import dream, inbox, skills
from ahab.memory import Memory
from ahab.model import Reply

home = Path(tempfile.mkdtemp())
m = Memory(home)
ws = home / "workspace"
(ws / "skills" / "retired").mkdir(parents=True)
(ws / "skills" / "books.md").write_text("# Finding a short book on Gutenberg\n1. Search.", encoding="utf-8")
(ws / "skills" / "old.md").write_text("How I used to do sheets\nStale.", encoding="utf-8")
(ws / "skills" / "fresh.md").write_text("Writing a cover letter\nSteps.", encoding="utf-8")
(ws / "skills" / "retired" / "gone.md").write_text("Long gone", encoding="utf-8")


def read(path, when=None):
    content = f'read_file {{"path": "{path}"}} -> ok\nargs: {json.dumps({"path": path})}\nok'
    if when is None:
        m.record("tool", content)
    else:
        m.db.execute("INSERT INTO events (time, kind, content) VALUES (?, 'tool', ?)", (when, content))
        m.db.commit()


# Retired skills are out of the waking list.
listed = skills.describe(m)
assert "skills/books.md: Finding a short book on Gutenberg" in listed and "gone" not in listed.lower(), listed
assert skills.is_skill("skills/old.md") and skills.is_skill("./skills\\old.md")
assert not skills.is_skill("skills/retired/gone.md") and not skills.is_skill("notes.md") and not skills.is_skill("skills")

# A day: a quiet wake, then a busy one that read a skill, then a reply.
long_ago = (datetime.now(timezone.utc) - timedelta(days=12)).isoformat(timespec="seconds")
read("skills/old.md", long_ago)
m.record("wake", "Woke.")
m.record("tool", 'list_files {"path": "."} -> Contents')
m.record("wake", "Woke.")
inbox.owner_sends(m, "Make me a reading list sheet."); inbox.deliver(m)
read("./skills/books.md")
for name in ("drive_search", "create_sheet", "update_sheet", "update_sheet", "update_sheet"):
    m.record("tool", f'{name} {{}} -> ok\nargs: {{}}\nok')
m.record("tool", 'message_owner (final reply) -> Sent\nargs: {"text": "Done."}\nSent')

busy, start = dream._busy_wakes(m)
assert len(busy) == 1, busy
assert "6 tool calls: read_file, drive_search, create_sheet, update_sheet x3" in busy[0], busy
review = skills.review(m, start)
assert "3 skill(s)" in review
assert "skills/books.md: Finding a short book on Gutenberg [read today" in review and "did it hold up?" in review, review
assert "skills/old.md: How I used to do sheets [last read" in review, review
assert "skills/fresh.md: Writing a cover letter [never read since it was written" in review, review
assert "gone" not in review.lower()


class Fake:
    def __init__(self, replies):
        self.replies, self.seen = list(replies), []

    def complete(self, messages, temperature=0.7, json_mode=False):
        self.seen.append([dict(x) for x in messages])
        return Reply(json.dumps(self.replies.pop(0)), "fake", 100, 10)


# The dream sees all that, and is asked about skills in so many words.
final = {"thought": "Sheets go in a skill; the old one's dead.", "notes": None, "dream": "A sheet day.",
         "skill_check": "The sheet run was worth one; wrote skills/sheets.md.",
         "files": {"skills/sheets.md": "# Making a sheet from a list\n1. create_sheet, then update_sheet per block.",
                   "skills/old.md": "",
                   "skills/huge.md": "# Too much\n" + "x" * dream.SKILL_CHAR_LIMIT,
                   "skills/retired/gone.md": "Back from the dead?"}}
fake = Fake([final])
dream._mark(home, dream._slot(datetime.now()) - timedelta(days=1))
assert dream.dream(m, fake, "Ahab") is True
system, page = fake.seen[0][0]["content"], fake.seen[0][1]["content"]
assert "2. Keep your skills." in system and "work out again" in system and "never ran isn't a skill" in system
assert "## Wake-ups that took work (4 or more tool calls)" in page and "update_sheet x3" in page, page
assert "did it hold up?" in page
said = m.recent(1)[0].content.splitlines()[0]
assert "Skill check: The sheet run was worth one" in m.recent(1)[0].content
assert '"skill_check"' in system
assert "skills/sheets.md" in said and "skills/old.md (retired to skills/retired/old.md)" in said, said
assert f"skills/huge.md (over {dream.SKILL_CHAR_LIMIT} chars)" in said, said
assert not (ws / "skills" / "old.md").exists() and (ws / "skills" / "retired" / "old.md").read_text(encoding="utf-8").startswith("How I")
assert not (ws / "skills" / "huge.md").exists()
# A path inside retired/ isn't a live skill: written as an ordinary file, not retired again.
assert (ws / "skills" / "retired" / "gone.md").read_text(encoding="utf-8") == "Back from the dead?"
assert "Making a sheet from a list" in skills.describe(m) and "old.md" not in skills.describe(m)

# Retiring the same name twice keeps both; a skill that isn't there, or a path out of the
# workspace, is refused.
(ws / "skills" / "old.md").write_text("Old again", encoding="utf-8")
assert skills.retire(m, "skills/old.md").startswith("retired to skills/retired/old-")
assert skills.retire(m, "skills/nothing.md") == "no such skill"
assert skills.retire(m, "../outside.md").startswith("refused")
print("skills dream ok")
