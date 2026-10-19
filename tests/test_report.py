"""Test the weekly memory report: what it counts, and when the watchdog sends it."""
import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, ROOT)
from perro import inbox, report
from perro.memory import Memory

home = Path(tempfile.mkdtemp())
(home / "home.json").write_text(json.dumps({"name": "Pip"}), encoding="utf-8")
m = Memory(home)
ws = home / "workspace"; (ws / "memory" / "people").mkdir(parents=True); (ws / "memory" / "daily").mkdir()
(ws / "MEMORY.md").write_text("m" * 1200, encoding="utf-8")
(ws / "memory" / "people" / "ann.md").write_text("# Ann", encoding="utf-8")

tool = lambda name, args, result: m.record("tool", f"{name} {json.dumps(args)} -> {result}\nargs: {json.dumps(args)}\n{result}")
tool("edit_file", {"path": "MEMORY.md", "old": "", "new": "x"}, "Added to the end of MEMORY.md (now 9 chars).")
tool("write_file", {"path": "memory/people/ann.md", "content": "# Ann"}, "Created memory/people/ann.md (5 chars).")
tool("edit_file", {"path": "notes-of-mine.md", "old": "", "new": "x"}, "Created notes-of-mine.md (now 1 chars).")
tool("edit_file", {"path": "MEMORY.md", "old": "zz", "new": "x"}, "edit_file refused: old appears 0 times")
tool("recall", {"query": "Ann"}, "Best matches...")
tool("recall", {"query": "bees"}, "Best matches...")
tool("read_conversation", {"date": "2026-10-01"}, "Your conversation...")
m.record("dream", "Dreamed over the day since Sat; wrote dreams/x.md. Wrote memory/daily/x.md. Rewrote the notes. "
                  "Changed USER.md, MEMORY.md, memory/people/ann.md. Tell your owner what changed. [0 tool calls]")
inbox.owner_sends(m, "Remember Ann?"); inbox.deliver(m)
inbox.perro_sends(m, "Sorry, I don't remember an Ann.")
yesterday = datetime.now().astimezone() - timedelta(days=1)
m.db.execute("INSERT INTO events (time, kind, content) VALUES (?, 'message', 'From the owner (sent x):\nhi')",
             (yesterday.astimezone().isoformat(),))
m.db.commit(); m.close()

text, n = report.build(home, datetime.now().astimezone())
print(text)
assert text.startswith("[memory report] Pip, the last 7 days:")
assert "Wrote to memory while awake 2 time(s): MEMORY.md, memory/people/ann.md." in text
assert "The dream ran 1 time(s) and changed 2 memory file(s)." in text
assert "MEMORY.md is 1,200 characters (cap 20,000)." in text and "since the last report" not in text
assert "People pages: 1 (ann)." in text
assert "Looked things up: recall 2, read_conversation 1." in text
assert "Said it didn't remember 1 time(s), latest: \"Sorry, I don't remember an Ann.\"" in text
assert f"Talked on 2 day(s); no day note for {yesterday:%Y-%m-%d}." in text, "today's note isn't due yet"
(ws / "memory" / "daily" / f"{yesterday:%Y-%m-%d}.md").write_text("# day", encoding="utf-8")
assert "each with its day note" in report.build(home, datetime.now().astimezone())[0]

# Sending: a new home starts counting; then once per Sunday-7pm slot, retried if the send fails.
sunday = datetime(2026, 10, 25, 19, 30).astimezone()
assert report._slot(sunday) == datetime(2026, 10, 25, 19, 0).astimezone()
assert report._slot(sunday - timedelta(hours=1)) == datetime(2026, 10, 18, 19, 0).astimezone()
sent = []
send = lambda t: sent.append(t) or True
assert report.send_if_due(home, sunday - timedelta(days=2), send) is None and not sent   # first look: start counting
assert report.send_if_due(home, sunday - timedelta(hours=1), send) is None               # not Sunday 7pm yet
assert report.send_if_due(home, sunday, lambda t: False) is None                         # failed: try again
assert report.send_if_due(home, sunday, send) and len(sent) == 1
assert report.send_if_due(home, sunday + timedelta(minutes=10), send) is None and len(sent) == 1
(ws / "MEMORY.md").write_text("m" * 1500, encoding="utf-8")
monday = sunday + timedelta(days=8)                                                      # PC was off on Sunday
assert report.send_if_due(home, monday, send) and "+300 since the last report" in sent[-1], sent[-1]
print("report ok")
