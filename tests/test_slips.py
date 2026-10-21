"""Model slips the wake loop forgives: args sent as a quoted string, and a reply that does nothing."""
import json
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ahab import heartbeat, tools
from ahab.memory import Memory
from ahab.model import Reply

home = Path(tempfile.mkdtemp())
(home / "workspace").mkdir()
(home / "workspace" / "a.md").write_text("hello bees", encoding="utf-8")
m = Memory(home)


class Fake:
    def __init__(self, *replies):
        self.replies, self.seen = list(replies), []

    def complete(self, messages, temperature=0.7, json_mode=False):
        self.seen.append([dict(x) for x in messages])
        return Reply(json.dumps(self.replies.pop(0)), "fake", 100, 10)


# Args as a quoted JSON object are read as the object they are; other strings still refuse.
assert "hello bees" in tools.run(m, "read_file", json.dumps({"path": "a.md"}))
assert tools.run(m, "read_file", "not json") == "args for read_file must be a JSON object."
assert tools.run(m, "read_file", json.dumps(["a.md"])) == "args for read_file must be a JSON object."

# The wake of 2026-10-21: a tool slip, then a reply with every field null. It is asked again once
# (the owner's request would otherwise sleep for 30 minutes), and the answer then goes out.
nothing = {"thought": "Waiting for the output.", "tool": None, "args": None, "message": None, "notes": None,
           "next_wake_minutes": None}
f = Fake({"thought": "Reading.", "tool": "read_file", "args": json.dumps({"path": "a.md"})},
         nothing,
         {"thought": "Done.", "message": None, "notes": None, "next_wake_minutes": 60})
minutes = heartbeat.beat(m, f, heartbeat.system_prompt("Pip"))
assert minutes == 60 and len(f.seen) == 3, (minutes, len(f.seen))
assert "hello bees" in f.seen[1][-1]["content"]
assert f.seen[2][-1]["content"] == heartbeat.EMPTY_REPLY
assert any(e.kind == "error" and "nothing happened" in e.content for e in m.recent(50))

# A second empty reply in the same wake is not chased again; a plain final reply is never questioned.
f = Fake(nothing, nothing)
heartbeat.beat(m, f, heartbeat.system_prompt("Pip"))
assert len(f.seen) == 2
f = Fake({"thought": "Quiet.", "message": None, "notes": None, "next_wake_minutes": 90})
assert heartbeat.beat(m, f, heartbeat.system_prompt("Pip")) == 90 and len(f.seen) == 1
f = Fake({"thought": "Answering.", "message": "Here you go.", "notes": None})
heartbeat.beat(m, f, heartbeat.system_prompt("Pip"))
assert len(f.seen) == 1

m.close()
print("slips ok")
