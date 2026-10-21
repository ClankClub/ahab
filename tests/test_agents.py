"""Test crew agents: named helpers with lanes, notes and workspaces of their own, which report
to Pip, never the owner, can't reach past their tools, and need approval when they act outward."""
import json
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ahab import agents, approvals, heartbeat, tools
from ahab.memory import Memory
from ahab.model import Reply

home = Path(tempfile.mkdtemp())
config = {"name": "Pip", "backend": "codex-luna"}
(home / "home.json").write_text(json.dumps(config), encoding="utf-8")
m = Memory(home)
ws = home / "workspace"; ws.mkdir()
(ws / "secret.md").write_text("Pip's own file.", encoding="utf-8")
spawned = []
agents._spawn = lambda h, tid: spawned.append(tid)
agents.ModelClient = type("NoModel", (), {"from_backend": staticmethod(lambda backend: None)})


class Fake:
    def __init__(self, *replies):
        self.replies, self.seen = list(replies), []

    def complete(self, messages, temperature=0.7, json_mode=False):
        self.seen.append([dict(x) for x in messages])
        return Reply(json.dumps(self.replies.pop(0)), "fake", 100, 20)


def tool(name, **args):
    return {"thought": f"using {name}", "tool": name, "args": args}


def finish(report, notes=None):
    return {"thought": "done", "report": report, "notes": notes}


def ask(agent="librarian", task="Tidy the bee notes and tell me what you changed and why.", **kw):
    return tools.run(m, "ask_agent", {"agent": agent, "task": task, **kw})


def set_config(**changes):
    config.update(changes)
    (home / "home.json").write_text(json.dumps(config), encoding="utf-8")


def status(tid):
    return m.db.execute("SELECT status FROM agent_tasks WHERE id = ?", (tid,)).fetchone()[0]


# Every core tool is either given to crew agents or refused on purpose, and nothing that
# speaks for Pip, schedules, delegates or acts outward is ever theirs.
grouped = {n for _, names, _ in tools.GROUPS.values() for n in names}
core = {n for n in tools.TOOLS if n not in grouped}
assert core == set(agents.CREW_CORE) | set(agents.CREW_REFUSED), core ^ (set(agents.CREW_CORE) | set(agents.CREW_REFUSED))
assert set(agents.AGENT_GROUPS) <= set(tools.GROUPS)
widest = agents.Agent("x", "crew", "", "claude-haiku", tuple(agents.AGENT_GROUPS), 20, 30, "").tools()
never = {"message_owner", "set_alarm", "set_intent", "watch_page", "start_worker", "ask_agent", "send_email",
         "browse", "desktop_act", "shell", "start_build", "push_site", "request_publish", "request_make_public"}
assert not never & set(widest), never & set(widest)

# No roster: no group, nothing on the page, and asking says so.
assert agents.describe(m) == "" and "- agents," not in tools.describe(m)
assert "agents" in tools.describe(m).split("Not set up")[-1]
assert "No agent called 'librarian'" in ask() and "none yet" in ask()

# The owner adds one from the command line: the roster entry, its folder and a starter lane.
made = agents.new(home, "librarian", "Owns the bee notes.", "claude-haiku", ["vault"])
config = json.loads((home / "home.json").read_text(encoding="utf-8"))
assert config["agents"]["librarian"]["groups"] == ["vault"] and config["name"] == "Pip"
lane = home / "agents" / "librarian" / "LANE.md"
assert "Owns the bee notes." in lane.read_text(encoding="utf-8") and "Pip does that" in lane.read_text(encoding="utf-8")
lane.write_text("# Librarian\nYou keep the bee notes in order. Not yours: the calendar.", encoding="utf-8")
for bad in (("librarian", "dup"), ("Bad Name", "x"), ("b", "x", "nope-model"), ("c", "x", "claude-haiku", ["browser"])):
    try:
        agents.new(home, *bad)
        raise AssertionError(bad)
    except agents.AgentError:
        pass
assert "vault_search" in made.tools() and "write_file" in made.tools()

# Broken entries and remote agents (not built yet) show as problems, not crashes.
set_config(agents={**config["agents"], "hermes": {"kind": "a2a", "url": "http://100.64.0.1:9900/"},
                   "odd": {"groups": ["shell"]}})
roster, problems = agents.roster(home)
assert list(roster) == ["librarian"] and len(problems) == 2, problems
assert "aren't built yet" in problems[0] and "can't have shell" in problems[1]
page = agents.describe(m)
assert "## Your agents" in page and "- librarian: Owns the bee notes." in page and "Broken" in page
assert "- agents, your crew" in tools.describe(m)
set_config(agents={"librarian": config["agents"]["librarian"]})

# Refusals before anything starts.
assert "too short" in ask(task="tidy")
assert "follow_up" in ask(follow_up="x")
assert not spawned

# A task: started in the background, one at a time per agent.
r = ask()
assert r.startswith("Gave librarian task #1") and "agents/librarian/1.md" in r and spawned == [1], r
assert "busy with task #1" in ask()
assert "Running: #1 librarian" in agents.describe(m)

# The run: its own workspace, its own log, the tools it was given and no others.
crew_ws = home / "agents" / "librarian" / "workspace"
(crew_ws / "bees.md").write_text("Bees: hive behind the shed.", encoding="utf-8")
before = len(m.recent(1000))
client = Fake(tool("read_file", path="bees.md"),
              tool("read_file", path="secret.md"),
              tool("read_file", path="../../../workspace/secret.md"),
              tool("write_file", path="tidy.md", content="Tidied."),
              tool("message_owner", text="hi from the librarian"),
              tool("ask_agent", agent="librarian", task="Talk to yourself for a while, please."),
              tool("start_worker", task="Do my job for me, start to finish, please."),
              finish("Tidied the bee notes into tidy.md (from bees.md). " + "Detail. " * 400,
                     notes="The bee notes live in bees.md."))
agents.run(home, 1, client)
assert status(1) == "done"
assert len(m.recent(1000)) == before, "nothing from the run lands in Pip's log until it's reported"
results = [msgs[-1]["content"] for msgs in client.seen[1:]]
assert "hive behind the shed" in results[0]
assert "Pip's own file" not in results[1] and "Pip's own file" not in results[2], results[1:3]
assert (crew_ws / "tidy.md").exists() and not (ws / "tidy.md").exists()
assert all("isn't one of your tools" in x for x in results[4:7]), results[4:7]
system = client.seen[0][0]["content"]
assert "You're librarian, one of the agents working for Pip" in system and "You keep the bee notes" in system
assert "- vault_search:" in system and "- message_owner:" not in system and "- ask_agent:" not in system
assert "(none yet)" in system
assert "## Your task, from Pip (#1)" in client.seen[0][1]["content"]
# Its own log has the task, its steps and its report; its notes grew; Pip's workspace has the report.
own = Memory(home / "agents" / "librarian")
kinds = [e.kind for e in own.recent(100)]
assert kinds[0] == "task" and kinds[-1] == "report" and "tool" in kinds, kinds
own.close()
assert "The bee notes live in bees.md." in (crew_ws / "MEMORY.md").read_text(encoding="utf-8")
report = (ws / "agents" / "librarian" / "1.md").read_text(encoding="utf-8")
assert "Tidied the bee notes" in report and "information, not instructions" in report
# Reported once, as an `agent` event, with a knock.
assert agents.waiting(m) and agents.report_finished(m) == 1 and not agents.waiting(m)
e = m.recent(1)[0]
assert e.kind == "agent" and e.content.startswith("librarian's task #1 (Tidy the bee notes") and "is done" in e.content
assert "more chars in agents/librarian/1.md" in e.content and len(e.content) < agents.PREVIEW_CHARS + 600
assert agents.report_finished(m) == 0
detail = tools.run(m, "check_agent_task", {"id": 1})
assert "librarian: done" in detail and "Last steps" in detail and "agents/librarian/1.md" in detail, detail
assert "#1 librarian, done" in tools.run(m, "agents", {})

# A follow-up: the agent sees its notes, its recent tasks and the thread; a quiet one doesn't knock.
assert "wasn't librarian's" in ask(follow_up=99)
r = ask(task="Now also file the wasp notes the same way, and report.", follow_up=1, wake=False)
assert "task #2" in r, r
client = Fake(finish("Filed the wasps too."))
agents.run(home, 2, client)
page = client.seen[0][1]["content"]
assert "## Your recent tasks" in page and "Tidy the bee notes" in page
assert "## The thread this task continues" in page and "Tidied the bee notes into tidy.md" in page
assert "The bee notes live in bees.md." in client.seen[0][0]["content"]
assert not agents.waiting(m) and agents.report_finished(m) == 1
assert agents._thread(m, 2) == [1, 2]
saved, agents.THREAD_LIMIT = agents.THREAD_LIMIT, 2
assert "already has 2 tasks" in ask(follow_up=2)
agents.THREAD_LIMIT = saved

# Cancelled: stops, writes nothing, tells nobody.
ask()
assert "will stop" in tools.run(m, "cancel_agent_task", {"id": 3})
agents.run(home, 3, Fake())
assert status(3) == "cancelled" and not (ws / "agents" / "librarian" / "3.md").exists() and not agents.waiting(m)

# A model that breaks is a failed task, said plainly, and it knocks.
ask()


class Broken:
    def complete(self, *a, **k):
        raise RuntimeError("the model fell over")


agents.run(home, 4, Broken())
assert status(4) == "failed" and agents.waiting(m)
agents.report_finished(m)
assert "failed" in m.recent(1)[0].content and "the model fell over" in m.recent(1)[0].content

# Deletes by an agent count against Pip's own daily brake.
crew = agents.CrewMemory(m, home / "agents" / "librarian")
was = tools.deleted_today(m)
crew.record("deleted", "Trashed a note.")
assert tools.deleted_today(m) == was + 1 == tools.deleted_today(crew)
assert "[librarian] Trashed a note." in m.recent(1)[0].content
assert tools.workspace_root(crew) == crew_ws.resolve() and crew.home == home
crew.close()

# Outward powers: the task goes to the owner first, word for word, and only code sends it.
plain = dict(config["agents"]["librarian"])
entry = dict(plain, outward="Can file notes in the shared vault others read.")
set_config(agents={"librarian": entry})
spawned.clear()
assert "it has to fit whole" in ask(task="x" * (agents.OUTWARD_TASK_CHAR_LIMIT + 1))
r = ask(task="Move the bee notes into the shared folder, please.")
assert r.startswith("Filed #") and "Nothing has been sent" in r and not spawned, r
req = approvals.pending(m)[-1]
assert req.kind == "agent_task" and "Give agent librarian a task" in req.card
assert "Move the bee notes into the shared folder, please." in req.card and "shared vault others read" in req.card
assert "Tasks need your owner's approval" in agents.describe(m)
approvals.decide(m, req.id, True, "test")
approvals.run_decided(m)
assert spawned == [5] and status(5) == "running", spawned
assert "Gave librarian task #5" in m.recent(1)[0].content
agents.run(home, 5, Fake(finish("Moved.")))
agents.report_finished(m)
# The setting: "never" sends straight away, "always" asks even without outward powers,
# and anything unrecognized counts as "always".
set_config(agent_approval="never")
assert ask().startswith("Gave librarian task #6")
agents.run(home, 6, Fake(finish("Done.")))
set_config(agent_approval="always", agents={"librarian": plain})
assert ask().startswith("Filed #")
set_config(agent_approval="sometimes")
assert agents.approval_setting(home) == "always" and ask().startswith("Filed #")
set_config(agent_approval="outward")
assert ask().startswith("Gave librarian task #7")
agents.run(home, 7, Fake(finish("Done.")))

# Limits: at most MAX_RUNNING at once across the crew, DAILY_LIMIT a day.
set_config(agents={n: {"about": n} for n in ("a1", "a2", "a3", "a4")})
for n in ("a1", "a2", "a3"):
    assert ask(agent=n).startswith("Gave"), n
assert "3 agent tasks are already running" in ask(agent="a4")
m.db.execute("UPDATE agent_tasks SET status = 'done', reported = 'x' WHERE status = 'running'")
m.db.commit()
saved, agents.DAILY_LIMIT = agents.DAILY_LIMIT, 10
assert "10 agent tasks in 24 hours" in ask(agent="a4")
agents.DAILY_LIMIT = saved

# In a wake: the roster on Pip's page.
set_config(agents={"librarian": plain})
f = Fake({"thought": "Nothing to do.", "message": None, "notes": None, "next_wake_minutes": 60})
heartbeat.beat(m, f, heartbeat.system_prompt("Pip"))
assert "## Your agents (ask_agent hands one a task)" in f.seen[0][1]["content"] + f.seen[0][0]["content"]

m.close()
print("agents ok")
