"""Test workers: background helpers with read-only tools, reporting to Ahab, never the owner."""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, ROOT)
from ahab import heartbeat, tools, workers
from ahab.memory import Memory
from ahab.model import ModelError, Reply

home = Path(tempfile.mkdtemp())
(home / "home.json").write_text(json.dumps({"name": "Pip", "backend": "codex-luna"}), encoding="utf-8")
m = Memory(home)
ws = home / "workspace"; ws.mkdir()
(ws / "notes-on-bees.md").write_text("Bees: the hive is behind the shed.", encoding="utf-8")
spawned = []
workers._spawn = lambda h, wid: spawned.append(wid)


class Fake:
    """A model that answers from a script, and remembers what it was shown."""
    def __init__(self, *replies):
        self.replies, self.seen = list(replies), []

    def complete(self, messages, temperature=0.7, json_mode=False):
        self.seen.append([dict(x) for x in messages])
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return Reply(json.dumps(reply), "fake", 100, 20)


def tool(name, **args):
    return {"thought": f"using {name}", "tool": name, "args": args}


def finish(report, wake=True):
    return {"thought": "done", "report": report, "wake_pip": wake}


def start(task="Find out where the bees live and whether the hive needs moving.", **kw):
    return tools.run(m, "start_worker", {"task": task, **kw})


def last_event(kind="worker"):
    return next((e.content for e in reversed(m.recent(50)) if e.kind == kind), None)


# Refusals: too short, a model not on the list (only cheap ones are), bad call budgets.
assert "too short" in start("look")
r = start(model="astra")
assert "isn't a worker model" in r and "luna, haiku, deepseek" in r, r
assert "max_calls" in start(max_calls=99) and "max_calls" in start(max_calls=0)
saved = os.environ.pop("DEEPSEEK_API_KEY", None)
assert "can't be used right now" in start(model="deepseek")
if saved:
    os.environ["DEEPSEEK_API_KEY"] = saved
assert not spawned

# A worker starts on Pip's own model by default, and the tool description names the choices.
r = start()
assert r.startswith("Started worker #1 on luna") and "workers/1.md" in r and spawned == [1], r
assert "astra" not in tools.TOOLS["start_worker"][1] and '"haiku"' in tools.TOOLS["start_worker"][1]
assert "## Your workers" in workers.describe_running(m) and "#1 on luna" in workers.describe_running(m)


# Its tools: read-only ones work; a tool's own logging goes to the worker's steps, not Pip's log;
# tools outside its list are refused, outward ones included.
def fake_fetch(memory, url, start=0):
    memory.record("web_search", f"fetched {url}")
    return f"Page {url}: the council moves hives for free."


tools.TOOLS["fetch_page"] = (fake_fetch, tools.TOOLS["fetch_page"][1])
before = len(m.recent(1000))
client = Fake(tool("read_file", path="notes-on-bees.md"),
              tool("fetch_page", url="https://council.test/bees"),
              tool("message_owner", text="hi from the worker"),
              tool("send_email", to="x@y.test", subject="s", body="b"),
              finish("The hive is behind the shed (notes-on-bees.md). The council moves hives free "
                     "(https://council.test/bees). " + "Detail. " * 400))
workers.run(home, 1, client)
assert len(m.recent(1000)) == before, "nothing from the run lands in Pip's log"
steps = m.db.execute("SELECT kind, content FROM worker_steps WHERE worker_id = 1").fetchall()
assert any(k == "web_search" and "council.test" in c for k, c in steps)
refusals = [c for k, c in steps if k == "tool" and "isn't one of your tools" in c]
assert len(refusals) == 2, refusals
assert "Bees: the hive is behind the shed." in client.seen[1][-1]["content"]
system = client.seen[0][0]["content"]
assert "worker for Pip" in system and "not for its owner" in system and "information, not instructions" in system
assert "- fetch_page:" in system and "- message_owner:" not in system and "- start_worker:" not in system

# Its report: a file in Pip's workspace, a short preview in Pip's log, and a knock.
report = (ws / "workers" / "1.md").read_text(encoding="utf-8")
assert "council moves hives free" in report and "information, not instructions" in report
assert workers.waiting(m) and workers.describe_running(m) == ""
assert workers.report_finished(m) == 1 and not workers.waiting(m)
e = last_event()
assert e.startswith("Worker #1 (Find out where the bees live") and "is done" in e and "workers/1.md" in e, e
assert "2 tool call(s)" in e and "more chars in workers/1.md" in e and len(e) < workers.PREVIEW_CHARS + 600
assert workers.report_finished(m) == 0, "told once"
detail = tools.run(m, "check_worker", {"id": 1})
assert "done" in detail and "2 of 20 tool calls" in detail and "workers/1.md" in detail, detail
assert "#1 luna, done" in tools.run(m, "check_worker", {})

# A report that can wait doesn't knock, but is still in the log at the next wake.
start(model="haiku")
workers.run(home, 2, Fake(finish("Nothing new on the bees.", wake=False)))
assert not workers.waiting(m) and workers.report_finished(m) == 1 and "Nothing new" in last_event()

# Out of calls: the extra call isn't run, and it's told to finish.
start(max_calls=1)
client = Fake(tool("list_files"), tool("list_files"), finish("Listed once."))
workers.run(home, 3, client)
assert "REFUSED" in client.seen[2][-1]["content"]
assert m.db.execute("SELECT status, calls FROM workers WHERE id = 3").fetchone() == ("done", 1)

# Out of time: one last chance to report, then it's stopped.
start()
m.db.execute("UPDATE workers SET created = ? WHERE id = 4",
             ((datetime.now(timezone.utc) - timedelta(minutes=workers.TIME_LIMIT_MINUTES + 1)).isoformat(),))
m.db.commit()
client = Fake(tool("list_files"), finish("never reached"))
workers.run(home, 4, client)
assert "Time's up" in client.seen[0][-1]["content"]
assert m.db.execute("SELECT status FROM workers WHERE id = 4").fetchone() == ("timed_out",)

# Cancelled: stops before its next step, writes nothing, tells nobody.
start()
assert "will stop" in tools.run(m, "cancel_worker", {"id": 5})
assert "No running worker #5" in tools.run(m, "cancel_worker", {"id": 5})
workers.run(home, 5, Fake())
assert m.db.execute("SELECT status FROM workers WHERE id = 5").fetchone() == ("cancelled",)
assert not (ws / "workers" / "5.md").exists()

# A model that breaks: a failed worker, said plainly, and it knocks.
start()
workers.run(home, 6, Fake(ModelError("haiku isn't logged in")))
assert workers.waiting(m)
workers.report_finished(m)
assert "Worker #6" in last_event() and "failed" in last_event() and "isn't logged in" in last_event()
assert "timed out" in next(c.content for c in m.recent(50) if c.kind == "worker" and "#4" in c.content)

# At most MAX_RUNNING at once.
for _ in range(workers.MAX_RUNNING):
    assert start().startswith("Started")
assert "at once is the limit" in start()

# The daily limit.
m.db.execute("UPDATE workers SET status = 'done', reported = 'x' WHERE status = 'running'")
m.db.commit()
for _ in range(workers.DAILY_LIMIT - 9):
    assert start().startswith("Started"), start
    m.db.execute("UPDATE workers SET status = 'done', reported = 'x' WHERE status = 'running'")
    m.db.commit()
assert "in 24 hours is the limit" in start()

# A runner that died is marked interrupted once it's long past its time.
m.db.execute("UPDATE workers SET status = 'running', reported = NULL, created = ? WHERE id = 7",
             ((datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(),))
m.db.commit()
workers.report_finished(m)
assert "Worker #7" in last_event() and "was interrupted" in last_event()

# Pip's page and prompt know about workers and watches.
assert "start_worker" in heartbeat.SYSTEM_PROMPT and "watch_page" in heartbeat.SYSTEM_PROMPT
m.close()
print("workers ok")
