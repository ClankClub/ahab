import sys, tempfile, json
from pathlib import Path
ROOT = str(Path(__file__).resolve().parent.parent)
from pathlib import Path
sys.path.insert(0, ROOT)
from ahab import heartbeat
from ahab.memory import Memory
from ahab.model import Reply

class Fake:
    def __init__(self): self.seen = []
    def complete(self, messages, temperature=0.7, json_mode=False):
        self.seen.append(messages)
        return Reply(json.dumps({"thought": "ok", "notes": None, "next_wake_minutes": 60}), "fake", 1, 1)

def page(home):
    m = Memory(home); f = Fake()
    heartbeat.beat(m, f, heartbeat.system_prompt("Ahab"))
    assert m.recent(1)[0].kind != "error", m.recent(3)
    m.close()
    return f.seen[0][0]["content"], f.seen[0][1]["content"]

with tempfile.TemporaryDirectory() as d:
    home = Path(d)
    sysp, p = page(home)
    assert "## Who you are (SOUL.md)\n# SOUL" in p, p[:400]  # seeded from templates/
    assert "wild wolf" not in sysp and "SOUL.md" in sysp and "8000" in sysp
    ws = home / "workspace"; ws.mkdir(exist_ok=True)
    (ws / "SOUL.md").write_text("# SOUL\nI am a test soul.", encoding="utf-8")
    _, p = page(home)
    assert p.index("## Who you are (SOUL.md)\n# SOUL\nI am a test soul.") < p.index("## Your notes"), p[:500]
    (ws / "SOUL.md").write_text("x" * 8500, encoding="utf-8")
    _, p = page(home)
    assert "[cut: SOUL.md is 8500 characters" in p and "x" * 8001 not in p
    print("ALL PASSED")
