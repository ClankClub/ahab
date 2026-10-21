import sys, tempfile, json
from pathlib import Path
ROOT = str(Path(__file__).resolve().parent.parent)
from pathlib import Path
sys.path.insert(0, ROOT)
from ahab import heartbeat, identity
from ahab.memory import Memory
from ahab.model import Reply

class Fake:
    def __init__(self): self.seen = []
    def complete(self, messages, temperature=0.7, json_mode=False):
        self.seen.append([dict(m) for m in messages])
        return Reply(json.dumps({"thought": "ok", "notes": None, "next_wake_minutes": 60}), "fake", 1, 1)

def page(home, name="Ahab"):
    m = Memory(home); f = Fake()
    heartbeat.beat(m, f, heartbeat.system_prompt(name), name)
    starts = [e.content for e in m.recent(30) if e.kind == "start"]
    assert m.recent(1)[0].kind == "sleep", m.recent(3)
    m.close()
    return f.seen[0][0]["content"], "\n\n".join(x["content"] for x in f.seen[0][1:]), starts

with tempfile.TemporaryDirectory() as d:
    home = Path(d); ws = home / "workspace"
    sysp, p, starts = page(home, "Luna")
    soul, user = (ws / "SOUL.md").read_text(encoding="utf-8"), (ws / "USER.md").read_text(encoding="utf-8")
    assert "You're Luna," in soul and "{name}" not in soul and "Lobo" not in soul
    assert user.startswith("# USER")
    assert len(starts) == 6, starts
    assert (ws / "AGENTS.md").read_text(encoding="utf-8").startswith("# AGENTS")
    assert (ws / "PROACTIVE_PREFERENCES.md").read_text(encoding="utf-8").startswith("# PROACTIVE PREFERENCES")
    assert p.index("## Who you are (SOUL.md)") < p.index("## Your owner (USER.md)") < p.index("## What to bring up on your own (PROACTIVE_PREFERENCES.md)") < p.index("## How you work here (AGENTS.md)") < p.index("## What you remember (MEMORY.md)") < p.index("Woke at") < p.index("## Right now") < p.index("## Your notes") < p.index("## Your memory folder (memory/)")
    assert "USER.md" in sysp and "PROACTIVE_PREFERENCES.md is your owner's" in sysp and "Five files" in sysp and "AGENTS.md is how you get work done" in sysp and "8000" in sysp and "{identity" not in sysp
    # existing files are never overwritten; no new start events
    (ws / "USER.md").write_text("# USER\nGoes by Sam.", encoding="utf-8")
    _, p, starts = page(home, "Luna")
    assert "Goes by Sam." in p and len(starts) == 6 and (ws / "USER.md").read_text(encoding="utf-8") == "# USER\nGoes by Sam."
    # deleted file comes back as a starter; cap still works
    (ws / "SOUL.md").unlink(); (ws / "USER.md").write_text("u" * 8500, encoding="utf-8")
    _, p, starts = page(home, None)
    assert "You're the assistant," in (ws / "SOUL.md").read_text(encoding="utf-8") and len(starts) == 7
    assert "[cut: USER.md is 8500 characters" in p
print("ALL PASSED")
