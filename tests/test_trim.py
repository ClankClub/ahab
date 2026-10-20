import sys, tempfile, json
from pathlib import Path
ROOT = str(Path(__file__).resolve().parent.parent)
from pathlib import Path
sys.path.insert(0, ROOT)
from ahab import heartbeat
from ahab.memory import Memory
from ahab.model import Reply

class Fake:
    def __init__(self, replies): self.replies, self.seen = list(replies), []
    def complete(self, messages, temperature=0.7, json_mode=False):
        self.seen.append([dict(m) for m in messages])
        return Reply(json.dumps(self.replies.pop(0)), "fake", 100, 10, cached_tokens=40)

def run(trim):
    with tempfile.TemporaryDirectory() as d:
        m = Memory(Path(d)); ws = Path(d) / "workspace"; ws.mkdir()
        (ws / "big.txt").write_text("B" * 7900, encoding="utf-8")
        reads = [{"thought": f"read {i}", "tool": "read_file", "args": {"path": "big.txt"}} for i in range(10)]
        f = Fake(reads + [{"thought": "done", "notes": None, "next_wake_minutes": 60}])
        if not trim:
            saved = heartbeat.trim_last_result; heartbeat.trim_last_result = lambda msgs: None
        heartbeat.beat(m, f, heartbeat.system_prompt("Ahab"))
        if not trim: heartbeat.trim_last_result = saved
        sleep = m.recent(1)[0].content
        full_logged = all(len(e.content) > 7900 for e in m.search("big.txt", 50) if e.kind == "tool")
        m.close()
        return f.seen, sleep, full_logged

seen, sleep, logged = run(True)
last = seen[-1]
results = [x["content"] for x in last if x["content"].startswith("Tool result:")]
assert len(results) == 10
assert all("[trimmed:" in r and len(r) < 800 for r in results[:-1]), [len(r) for r in results]
assert "[trimmed:" not in results[-1] and results[-1].count("B") == 7900
assert "1100 in (440 cached), 110 out" in sleep, sleep
assert logged, "log lost full results"
assert "Only your latest tool" in last[0]["content"]
total_trim = sum(len(x["content"]) for call in seen for x in call)
total_full = sum(len(x["content"]) for call in run(False)[0] for x in call)
print(f"chars sent over the wake-up: {total_full:,} untrimmed -> {total_trim:,} trimmed ({total_trim/total_full:.0%})")
print("ALL PASSED")
