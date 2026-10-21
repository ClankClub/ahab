"""The session simulator (scripts/simulate_sessions.py) on a short, hard run: a small
session and a summarizer that keeps failing, so folds, retries and hard folds all
happen, and none of the session's rules break."""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import simulate_sessions as sim

# A small session and a flaky summarizer: folds, failures and folds without a summary all happen.
run = sim.simulate(seed=7, days=6, owner="busy", limit=8_000, fail=0.4, outages=0.5, cache_minutes=10)
assert run["violations"] == [], run["violations"][:5]
assert run["wakes"] > 50 and run["owner_messages"] > 30, (run["wakes"], run["owner_messages"])
assert run["folds"] >= 5, run["folds"]
assert run["summary_failures"] >= 1 and run["hard_folds"] >= 1, (run["summary_failures"], run["hard_folds"])
assert run["fold_errors"] == 0
assert max(run["summary_sizes"]) <= 12_000 + 40
assert run["session_peak"] < 8_000 * 2.5, run["session_peak"]
assert 0 < run["tokens_cached"] < run["tokens_in"], "a burst of chat reuses the page's opening"

# The same seed gives the same run.
again = sim.simulate(seed=7, days=1, owner="quiet", limit=8_000, fail=0, outages=0, cache_minutes=10)
twice = sim.simulate(seed=7, days=1, owner="quiet", limit=8_000, fail=0, outages=0, cache_minutes=10)
assert (again["wakes"], again["tokens_in"]) == (twice["wakes"], twice["tokens_in"])
from ahab import tools
assert tools.run.__name__ == "run" and tools._real_run.__module__ == "ahab.tools", "later runs send for real"

# The profile can be measured from a log: sizes and counts only.
import tempfile
from ahab.memory import Memory
home = Path(tempfile.mkdtemp())
m = Memory(home)
m.record("wake", "Woke.")
m.record("message", "From the owner (sent now):\nhello there")
m.record("tool", 'read_file {"path": "a"} -> x\nargs: {"path": "a"}\nxxxxxxxxxx')
m.record("thought", "a thought")
m.close()
profile = sim.measure(home / "memory.db")
assert profile["chat_tools"] == [[1, 1]] and profile["owner"][0] == len("hello there"), profile

print("simulate_sessions ok")

# The owner: same seed, same messages; facts told once, no repeats of the whole script, probes scored by code.
import sim_owner
a, b = sim_owner.Owner(5), sim_owner.Owner(5)
texts = []
folds = 0
for i in range(200):
    if i and i % 40 == 0:
        folds += 1
        a.on_fold(folds), b.on_fold(folds)
    ta, ma = a.next(folds)
    tb, _ = b.next(folds)
    assert ta == tb
    texts.append((ta, ma))
kinds = Counter(m["kind"] for _, m in texts)
assert kinds["tell"] >= 20 and kinds["probe"] >= 6 and kinds["update"] >= 1, kinds
tells = [t for t, m in texts if m["kind"] == "tell"]
assert len(set(tells)) > len(tells) * 0.85, "facts come from pools, so they rarely repeat"
assert all(not any(w in c.lower() for w in ("lisbon", "dana")) for c, m in texts if m["kind"] == "chat")
assert sim_owner.score([["lisbon"], ["12"]], "Trip to Lisbon on the 12th") and not sim_owner.score([["lisbon"], ["13"]], "Lisbon, the 12th")

# A real-model run with a stand-in that has a perfect summarizer (it keeps every line the owner said and
# repeats the latest ones back). Checks the plumbing, not any model's memory: probes get asked after folds
# and scored, the home's own tools run for real, and the real dream runs.
from ahab.model import Reply

def owner_lines(text):
    return [l.split("] Owner: ", 1)[1] for l in text.splitlines() if "] Owner: " in l]


class Oracle:
    """A model with a perfect summarizer: it keeps every owner line, and repeats them back."""
    backend, model = "oracle", "oracle"
    def complete(self, messages, temperature=0.7, json_mode=False):
        everything = "\n".join(m["content"] for m in messages if isinstance(m["content"], str))
        if not json_mode:
            before = (everything.split("## The summary so far\n", 1)[1].split("## The older part", 1)[0]
                      if "## The summary so far" in everything else "")
            text = (before.replace("(none yet)", "") + " " + " ".join(owner_lines(everything)))[-11000:]
            return Reply(text, "o", len(everything) // 4, 800)
        if "this is your dream" in messages[0]["content"]:
            t = json.dumps({"thought": "x", "dream": "A short dream.", "day": "A day.", "notes": None,
                            "skill_check": "none", "files": {}})
        else:
            t = json.dumps({"thought": "x", "message": " ".join(owner_lines(everything))[-3000:],
                            "next_wake_minutes": 120})
        return Reply(t, "o", len(everything) // 4, 120)


real = sim.simulate(seed=2, days=4, owner="busy", limit=2_500, fail=0, outages=0, cache_minutes=10,
                    client=Oracle())
assert real["violations"] == [], real["violations"][:3]
assert real["folds"] >= 5 and len(real["probes"]) >= 10, (real["folds"], len(real["probes"]))
assert all("_text" not in p and "_after" not in p for p in real["probes"])
assert any(p["passed"] for p in real["probes"]) and not all(p["passed"] for p in real["probes"])
assert real["dreams"] >= 1 and real["dream_calls"] >= 1, (real["dreams"], real["dream_failures"])
assert real["dream_failures"] == 0
report = sim.summarize_runs([real], 2_500)
assert "Questions asked after a fold" in report and "Dreams done" in report, report
print("simulate_sessions owner and probes ok")


# DeepSeek's balance, read as the spend meter
assert sim.parse_balance('{"is_available": true, "balance_infos": [{"currency": "USD", "total_balance": "3.03"}]}') == 3.03
assert sim.parse_balance('{"balance_infos": [{"currency": "CNY", "total_balance": "9.00"}]}') is None
assert sim.parse_balance("not json") is None and sim.parse_balance("{}") is None


class Falling:
    """A balance that drops two cents every time it is read."""
    def __init__(self):
        self.value = 3.00
    def __call__(self):
        self.value -= 0.02
        return round(self.value, 2)


meter = sim.BalanceMeter(read=Falling())
meter.EVERY = 3
braked = sim.simulate(seed=2, days=4, owner="busy", limit=2_500, fail=0, outages=0, cache_minutes=10,
                      client=Oracle(), max_cost=0.10, meter=meter)
assert braked["stopped"] and "cap" in braked["stopped"], braked["stopped"]
assert 0.10 < braked["cost"] <= 0.20, braked["cost"]
unmetered = sim.BalanceMeter(read=lambda: None)
assert unmetered.start is None
free = {"cost": 0.0}
unmetered.tick(free, force=True)
assert free["cost"] == 0.0
print("simulate_sessions balance meter ok")
