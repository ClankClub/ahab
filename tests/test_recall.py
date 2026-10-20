"""Test full-text recall, read_conversation, edit_file and the memory folder on the page."""
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path
ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, ROOT)
from ahab import identity, inbox, tools
from ahab.memory import Memory, fts_query

home = Path(tempfile.mkdtemp())

# An old log, from before the index existed, gets indexed when it's opened.
db = sqlite3.connect(home / "memory.db")
db.execute("CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, time TEXT NOT NULL, "
           "kind TEXT NOT NULL, content TEXT NOT NULL)")
db.execute("INSERT INTO events (time, kind, content) VALUES ('2026-08-01T15:00:00+00:00', 'thought', "
           "'Sam mentioned the lighthouse keepers on the island.')")
db.commit(); db.close()
m = Memory(home)
assert m.fts
run = lambda name, args: tools.run(m, name, args)

out = run("recall", {"query": "lighthouse"})
assert "#1 [" in out and "«lighthouse»" in out, out

# Words, not strings: "whales" finds "whale", and the order of words doesn't matter.
m.record("thought", "The whale in chapter one is mostly rumour.")
m.record("notes", "Reading Moby-Dick with Sam, chapter one done.")
m.record("notes", "Reading Moby-Dick with Sam, chapter one done.")   # a repeat version
assert "«whale»" in run("recall", {"query": "whales"})
out = run("recall", {"query": "Sam reading"})
assert out.count("Moby-Dick with") == 1, "repeated notes versions show once: " + out
# No event has every word, so any word will do.
assert "lighthouse" in run("recall", {"query": "lighthouse submarine"})
# Punctuation and query words can't break it.
assert fts_query('AND "chapter one" NEAR(x') == '"chapter one" "AND" "NEAR" "x"'
assert "Nothing in your log" in run("recall", {"query": "zeppelin"})
assert "needs a query" in run("recall", {})

# Old search results, files read and sent messages don't echo back in.
m.record("tool", 'recall {"query": "zebra"} -> 1 hit\nargs: {}\n... zebra ...')
m.record("tool", 'read_file {"path": "x"} -> x\nargs: {}\nzebra zebra')
m.record("tool", 'fetch_page {"url": "https://zoo.test"} -> Zoo\nargs: {}\nthe zebra house')
out = run("recall", {"query": "zebra"})
assert "fetch_page" in out and "read_file" not in out and 'recall {"query"' not in out, out

# Dates narrow it, in the owner's local time.
assert "Nothing in your log" in run("recall", {"query": "lighthouse", "since": "2026-09-01"})
assert "#1 [" in run("recall", {"query": "lighthouse", "until": "2026-08-02"})
assert "isn't a date" in run("recall", {"query": "x", "since": "last week"})

# One event whole, a page at a time.
big = m.record("thought", "start " + "y" * (tools.READ_CHAR_LIMIT + 50) + " end")
out = run("recall", {"id": big.id})
assert out.startswith(f"#{big.id} [") and " end" not in out and f'"id": {big.id}, "start": {tools.READ_CHAR_LIMIT}' in out
assert run("recall", {"id": big.id, "start": tools.READ_CHAR_LIMIT}).endswith(" end")
assert "No event #9999" in run("recall", {"id": 9999})

# Memory files are searched too, kept current as they change and go.
ws = tools.workspace_root(m)
(ws / "memory" / "people").mkdir(parents=True)
(ws / "memory" / "people" / "ann.md").write_text("Ann, Sam's sister. Keeps bees in Vermont.", encoding="utf-8")
(ws / "MobyDick.txt").write_text("bees " * 100, encoding="utf-8")         # not memory: not searched
out = run("recall", {"query": "bees"})
assert "In your memory files:\n- memory/people/ann.md:" in out and "MobyDick" not in out, out
(ws / "memory" / "people" / "ann.md").write_text("Ann, Sam's sister. Moved to Maine.", encoding="utf-8")
import os; os.utime(ws / "memory" / "people" / "ann.md", (1, 1))      # a changed mtime, whatever the clock
assert "ann.md" not in run("recall", {"query": "bees"}) and "ann.md" in run("recall", {"query": "Maine"})
(ws / "memory" / "people" / "ann.md").unlink()
assert "ann.md" not in run("recall", {"query": "Maine"})

# The conversation, word for word.
inbox.owner_sends(m, "Tell me about the lighthouse again.")
inbox.deliver(m)
inbox.ahab_sends(m, "It was on the island Sam mentioned.")
inbox.owner_sends(m, "Not seen yet.")                                    # still in the mailbox
today = datetime.now().strftime("%Y-%m-%d")
out = run("read_conversation", {"date": today})
assert "word for word (2 messages)" in out and "Owner: Tell me about the lighthouse again." in out, out
assert "You: It was on the island" in out and "Not seen yet" not in out
asked = [e for e in m.recent(50) if e.kind == "message"][0]
assert "Tell me about the lighthouse" in run("read_conversation", {"around": asked.id})
assert "No messages on 2026-01-01. Nearest days with some, after:" in run("read_conversation", {"date": "2026-01-01"})
assert "give a" in run("read_conversation", {})

# edit_file: one passage, or the end.
assert run("edit_file", {"path": "MEMORY.md", "old": "", "new": "# MEMORY\n## Facts"}).startswith("Created MEMORY.md")
assert run("edit_file", {"path": "MEMORY.md", "old": "", "new": "- Sam reads Melville."}).startswith("Added to the end")
assert run("edit_file", {"path": "MEMORY.md", "old": "Melville", "new": "Melville (Oct 2026)"}).startswith("Edited")
assert (ws / "MEMORY.md").read_text(encoding="utf-8") == "# MEMORY\n## Facts\n- Sam reads Melville (Oct 2026)."
(ws / "twice.md").write_text("a a", encoding="utf-8")
assert "appears 2 times" in run("edit_file", {"path": "twice.md", "old": "a", "new": "b"})
assert "doesn't exist" in run("edit_file", {"path": "none.md", "old": "a", "new": "b"})
assert "outside your workspace" in run("edit_file", {"path": "../x.md", "old": "", "new": "b"})

# The memory folder on the page: each file by its first line, the latest day in full.
assert "(nothing yet. A page for each person" in identity.memory_folder(m)
(ws / "memory" / "opinions.md").write_text("# What I think about books\n...", encoding="utf-8")
(ws / "memory" / "daily").mkdir()
(ws / "memory" / "daily" / "2026-10-01.md").write_text("# Thursday\nOld day.", encoding="utf-8")
(ws / "memory" / "daily" / "2026-10-02.md").write_text("# Friday\nWe read chapter two.", encoding="utf-8")
section = identity.memory_folder(m)
assert "- opinions.md: What I think about books" in section, section
assert "- daily/: 2 daily note(s), 2026-10-01 to 2026-10-02" in section
assert section.endswith("## Your last day, in short (memory/daily/2026-10-02.md)\n# Friday\nWe read chapter two.")
assert "Old day" not in section
m.close()
print("recall ok")
