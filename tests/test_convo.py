import sys, tempfile, json
from pathlib import Path
ROOT = str(Path(__file__).resolve().parent.parent)
from pathlib import Path
sys.path.insert(0, ROOT)
from ahab import heartbeat, inbox
from ahab.memory import Memory
from ahab.model import Reply

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
    recent = inbox.describe_recent(m)                         # not delivered yet: only counted
    assert "waiting letter" not in recent and "1 more message(s)" in recent, recent
    f = Fake([{"thought": "reply", "tool": "message_owner", "args": {"text": "hi back"}}, done])
    heartbeat.beat(m, f, heartbeat.system_prompt("Ahab"))
    page = "\n\n".join(x["content"] for x in f.seen[0][1:])
    opening = page.split("Woke at")[1].split("## Right now")[0]
    assert "Owner: waiting letter" in opening, opening
    assert "## Recent conversation" not in page and "## Recent events" not in page
    after = inbox.describe_recent(m)
    assert after.index("Owner: waiting letter") < after.index("You: hi back"), after
    # budget: many long messages, newest kept, total bounded
    for i in range(30):
        inbox.ahab_sends(m, f"msg{i:02d} " + "y" * 900)
    r = inbox.describe_recent(m)
    assert "msg29" in r and "msg00" not in r and len(r) < inbox.RECENT_CHAR_LIMIT + 200, len(r)
    # newest always shown even if alone it's over budget
    inbox.ahab_sends(m, "z" * 3990)
    assert inbox.describe_recent(m, char_limit=100).endswith("z" * 50)
    m.close()
print("ALL PASSED")
