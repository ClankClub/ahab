"""The watchdog (perro.watchdog): what counts as down or stuck, and when the owner hears."""
import json
import os
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, ROOT)
from perro import watchdog
from perro.memory import Memory

HEALTHY = [True]


class Fake(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path == "/healthz" and HEALTHY[0] else 503)
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')

    def log_message(self, *a):
        pass


server = HTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=server.serve_forever, daemon=True).start()
os.environ["PERRO_N8N_URL"] = f"http://127.0.0.1:{server.server_port}/webhook"
os.environ["PERRO_N8N_KEY"] = "test-key"

home = Path(tempfile.mkdtemp())
(home / "home.json").write_text(json.dumps({"name": "Pip"}))
m = Memory(home)
NOW = time.time()
MIN = 60


def event(kind, content, ago):
    at = datetime.fromtimestamp(NOW - ago, timezone.utc).isoformat(timespec="seconds")
    m.db.execute("INSERT INTO events (time, kind, content) VALUES (?, ?, ?)", (at, kind, content))
    m.db.commit()


def alive(name, ago):
    path = home / f"{name}.alive"
    path.write_text("x")
    os.utime(path, (NOW - ago, NOW - ago))


said = []
works = [True]


def send(text):
    if works[0]:
        said.append(text)
    return works[0]


def check(at=0):
    return watchdog.check(home, NOW + at, send)


# keep_alive touches its file at once (and every minute after).
watchdog.keep_alive(home, "probe")
for _ in range(50):
    if (home / "probe.alive").exists():
        break
    time.sleep(0.02)
assert (home / "probe.alive").exists()

# All well: a fresh heartbeat asleep until later, no bridge set up, n8n answering.
alive("heartbeat", 30)
event("wake", "Woke.", 20 * MIN)
event("sleep", "Sleeping 600 min. [0 tool calls]", 19 * MIN)
assert watchdog.problems(home, m, NOW) == {}
assert check() == [] and check() == []

# The heartbeat's gone: nothing on the first check, the owner hears on the second, once.
alive("heartbeat", 20 * MIN)
assert check() == [] and not said
assert len(check()) == 1
assert said[-1].startswith("[watchdog] Pip:") and "The heartbeat isn't running (no sign of it since" in said[-1]
assert "ignore this" in said[-1]
assert check() == [] and len(said) == 1
assert "The watchdog told your owner" in m.recent(1)[0].content and m.recent(1)[0].kind == "watchdog"
# Back up: one more message saying so.
alive("heartbeat", 10)
assert len(check()) == 1 and "Back to normal: the heartbeat (out since" in said[-1]
assert check() == [] and json.loads((home / "watchdog.json").read_text()) == {}

# A blip that clears before the second check is never mentioned.
alive("heartbeat", 20 * MIN)
check()
alive("heartbeat", 10)
check()
assert len(said) == 2

# Alive but asleep well past the wake-up it chose: stuck.
event("sleep", "Sleeping 10 min. [0 tool calls]", 2 * 60 * MIN)
assert "didn't wake when it meant to" in watchdog.problems(home, m, NOW)["heartbeat_stuck"]
# Past it, but within the grace: fine.
event("sleep", "Sleeping 10 min. [0 tool calls]", 30 * MIN)
assert watchdog.problems(home, m, NOW) == {}
# A wake-up that started long ago and never finished: stuck. A recent one: fine.
event("wake", "Woke.", 2 * 60 * MIN)
assert "hasn't finished it" in watchdog.problems(home, m, NOW)["heartbeat_stuck"]
event("wake", "Woke.", 10 * MIN)
assert watchdog.problems(home, m, NOW) == {}

# The bridge is watched once it's paired.
(home / "telegram.json").write_text(json.dumps({"owner_chat_id": 5}))
assert "The Telegram bridge isn't running (no sign of it yet)" in watchdog.problems(home, m, NOW)["telegram"]
alive("telegram", 30)
assert watchdog.problems(home, m, NOW) == {}

# n8n down, and a send that fails: tried again next round, then told once.
HEALTHY[0] = False
assert "n8n isn't answering" in watchdog.problems(home, m, NOW)["n8n"]
check()
works[0] = False
assert check() == []
works[0] = True
assert len(check()) == 1 and "n8n isn't answering" in said[-1]
HEALTHY[0] = True
assert len(check()) == 1 and "Back to normal: n8n" in said[-1]
# Two at once go in one message.
os.environ["PERRO_N8N_URL"] = "http://127.0.0.1:1/webhook"
alive("telegram", 20 * MIN)
check()
check()
assert "n8n isn't answering" in said[-1] and "Telegram bridge isn't running" in said[-1]
# Without the n8n key, n8n isn't watched at all.
del os.environ["PERRO_N8N_KEY"]
assert "n8n" not in watchdog.problems(home, m, NOW)
print("watchdog ok")
