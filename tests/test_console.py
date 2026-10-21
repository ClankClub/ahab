"""The status screen: reads a home without changing it, fits the screen, says
asleep or awake from the last event, and can hide the chat."""
import json
import sys
import tempfile
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from datetime import datetime, timedelta, timezone
from ahab import agents, console, inbox, workers
from ahab.memory import Memory

home = Path(tempfile.mkdtemp())
assert "no memory yet" in console.frame(home, 80, 24, color=False)[0]

m = Memory(home)
inbox.owner_sends(m, "Hello there, a long message " + "word " * 60)
inbox.ahab_sends(m, "Hi back.")
m.record("sleep", "Sleeping 60 min. [0 tool calls]")
m.close()
(home / "home.json").write_text(json.dumps({"name": "Testbot"}), encoding="utf-8")
(home / "heartbeat.alive").touch()
before = (home / "memory.db").read_bytes()

for w, h in ((80, 24), (40, 12), (120, 40)):
    lines = console.frame(home, w, h, color=False)
    assert len(lines) <= h, (w, h, len(lines))
    assert all(len(l) <= w for l in lines), (w, [l for l in lines if len(l) > w])
text = "\n".join(console.frame(home, 100, 30, color=False))
assert text.startswith("TESTBOT · asleep, next wake") and "Hi back." in text and "Hello there" in text
assert "heartbeat ok" in text and "bridge down" in text and "backup off" in text
assert "\033[" in console.frame(home, 80, 24)[0]                         # colour when asked
assert (home / "memory.db").read_bytes() == before                       # read-only

# Overslept: the wake time has passed.
assert "waking soon" in console.state(home, time.time() + 2 * 3600)

# The chat can be hidden for a screen in a shared room.
(home / "home.json").write_text(json.dumps({"name": "Testbot", "console": {"show_chat": False}}), encoding="utf-8")
hidden = "\n".join(console.frame(home, 100, 30, color=False))
assert "Hi back." not in hidden and "Hello there" not in hidden and "sleep" in hidden

# Big numbers are cut (not rounded) to thousandths of the unit.
assert [console.compact(n) for n in (0, 999, 1000, 999_999, 14_176_736, 1_000_000, 2_500_000_000)] == \
    ["0", "999", "1.000 k", "999.999 k", "14.176 M", "1.000 M", "2.500 B"]

# The model line reads home.json's backend the way Ahab's own page does.
assert console.backend_line({}) == "DeepSeek → deepseek-flash"
assert console.backend_line({"backend": "codex-luna"}) == "Codex → gpt-6-luna → Max"
assert console.backend_line({"backend": "claude-sonnet"}) == "Claude → sonnet → Medium"
assert console.backend_line({"backend": "nope"}) == "nope (unknown backend)"

# Tokens: the cost line of a wake, dream, digest and search, in either format, plus the helper tables.
home2 = Path(tempfile.mkdtemp())
(home2 / "home.json").write_text(json.dumps({"name": "Testbot", "backend": "codex-luna"}), encoding="utf-8")
assert console.spent(home2) == (0, 0)                                    # no memory yet
m = Memory(home2)
m.record("sleep", "Sleeping 60 min. [3 tool calls, 1000 in (400 cached), 200 out]")
m.record("dream", "Dreamed over the day. [1 tool calls, 5000 in (0 cached), 500 out, 12s]\n\nThought: 9999 in, 9999 out")
m.record("digest", "Digest done [2 tool calls, 300 in, 30 out, 5s]")
m.record("web_search", "'bees' by codex: 3 result(s), searched n/a, 40000 tokens in, 800 out")
m.record("tool", "read_file {}\n77 in, 77 out")                          # not a cost line: not counted
assert console.spent(home2) == (46300, 1530)                             # no helper tables yet
workers._table(m)
agents._table(m)
now = time.time()
iso = lambda ago: (datetime.fromtimestamp(now, timezone.utc) - timedelta(seconds=ago)).isoformat(timespec="seconds")
m.db.execute("INSERT INTO workers (created, task, model, max_calls, status, finished, tokens_in, tokens_out) "
             "VALUES (?, 'an old job', 'haiku', 10, 'done', ?, 100, 10)", (iso(7200), iso(3600)))
m.db.commit()
assert console.spent(home2) == (46400, 1540)
assert console.spent(home2) == (46400, 1540)                             # asking again counts nothing twice
m.record("sleep", "Sleeping 5 min. [1 tool calls, 10 in (0 cached), 5 out]")
assert console.spent(home2) == (46410, 1545)                             # only the new row is read

lines = console.frame(home2, 100, 30, now=now, color=False)
assert lines[1] == "Codex → gpt-6-luna → Max · 47.955 k tokens (46.410 k in, 1.545 k out)", lines[1]
assert lines[2].startswith("─") and "helpers idle · 1 finished in the last day" in "\n".join(lines)
narrow = console.frame(home2, 40, 12, now=now, color=False)
assert narrow[1].startswith("Codex → gpt-6-luna") and "47.955 k tokens" in narrow[1] and "(" not in narrow[1]
assert all(len(l) <= 40 for l in narrow)

# Helpers at work: a line each, with what they were asked unless the chat is hidden.
m.db.execute("INSERT INTO workers (created, task, model, max_calls, status) "
             "VALUES (?, 'Count the bees in the garden', 'luna', 10, 'running')", (iso(300),))
m.db.execute("INSERT INTO agent_tasks (created, agent, task, minutes_cap, status) "
             "VALUES (?, 'scout', 'Dig into the hive', 30, 'running')", (iso(5400),))
m.db.commit()
text = "\n".join(console.frame(home2, 100, 30, now=now, color=False))
assert "helper worker luna" in text and "  5m  Count the bees in the garden" in text, text
assert "helper agent scout" in text and "1h30  Dig into the hive" in text and "helpers idle" not in text, text
(home2 / "home.json").write_text(json.dumps({"name": "Testbot", "console": {"show_chat": False}}), encoding="utf-8")
private = "\n".join(console.frame(home2, 100, 30, now=now, color=False))
assert "helper agent scout" in private and "Dig into the hive" not in private and "bees" not in private
(home2 / "home.json").write_text(json.dumps({"name": "Testbot"}), encoding="utf-8")
m.db.execute("UPDATE workers SET status = 'done', finished = ? WHERE status = 'running'", (iso(60),))
m.db.execute("UPDATE agent_tasks SET status = 'failed', finished = ? WHERE status = 'running'", (iso(30),))
m.db.commit()
assert "helpers idle · 3 finished in the last day, 1 failed" in "\n".join(console.frame(home2, 100, 30, now=now, color=False))
m.close()

# CPU temperature: the package sensor of coretemp wins; a dead sensor (-273) and a missing tree are no reading.
root = Path(tempfile.mkdtemp())
assert console.cpu_temp(root) is None
def sensor(folder, name, file, text):
    d = root / folder
    d.mkdir(parents=True, exist_ok=True)
    (d / "name" if folder.startswith("hwmon") else d / "type").write_text(name)
    (d / file).write_text(text)
sensor("thermal/thermal_zone0", "acpitz", "temp", "36000")
assert console.cpu_temp(root) is None, "a board sensor isn't the CPU's"
sensor("thermal/thermal_zone1", "x86_pkg_temp", "temp", "38000")
assert console.cpu_temp(root) == 38.0
sensor("hwmon/hwmon5", "coretemp", "temp1_input", "41000")
assert console.cpu_temp(root) == 41.0, "coretemp is preferred"
sensor("hwmon/hwmon5", "coretemp", "temp1_input", "-273150")
assert console.cpu_temp(root) == 38.0, "a dead sensor falls through to the next"
line = console.machine(home)
assert "disk " in line and "G" in line and "%" not in line.split("disk ")[1], line
print("ok")
