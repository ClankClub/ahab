import sys, tempfile, json
from pathlib import Path
ROOT = str(Path(__file__).resolve().parent.parent)
from pathlib import Path
sys.path.insert(0, ROOT)
from perro import heartbeat, inbox
from perro.memory import Memory
from perro.model import Reply

class Fake:
    def __init__(self, replies): self.replies, self.seen = list(replies), []
    def complete(self, messages, temperature=0.7, json_mode=False):
        self.seen.append([dict(m) for m in messages])
        return Reply(json.dumps(self.replies.pop(0)), "fake", 1, 1)

done = {"thought": "ok", "notes": None, "next_wake_minutes": 60}
with tempfile.TemporaryDirectory() as d:
    m = Memory(Path(d))
    assert inbox.describe_recent(m) == "(no messages yet)"
    inbox.owner_sends(m, "waiting letter")
    assert inbox.describe_recent(m) == "(no messages yet)"   # not delivered yet
    f = Fake([{"thought": "reply", "tool": "message_owner", "args": {"text": "hi back"}}, done])
    heartbeat.beat(m, f, heartbeat.system_prompt("Perro"))
    page = f.seen[0][1]["content"]
    convo = page.split("## Recent conversation")[1].split("## Your notes")[0]
    assert "Owner: waiting letter" in convo, convo
    events = page.split("## Recent events")[1]
    assert "waiting letter" not in events and "From the owner (sent" in events, events
    after = inbox.describe_recent(m)
    assert after.index("Owner: waiting letter") < after.index("You: hi back"), after
    # budget: many long messages, newest kept, total bounded
    for i in range(30):
        inbox.perro_sends(m, f"msg{i:02d} " + "y" * 900)
    r = inbox.describe_recent(m)
    assert "msg29" in r and "msg00" not in r and len(r) < inbox.RECENT_CHAR_LIMIT + 200, len(r)
    # newest always shown even if alone it's over budget
    inbox.perro_sends(m, "z" * 3990)
    assert inbox.describe_recent(m, char_limit=100).endswith("z" * 50)
    m.close()
print("ALL PASSED")
