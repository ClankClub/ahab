"""Test the mail watch (ahab.mail): who wakes Ahab, how fast, and the brakes on agents."""
import json
import os
import sys
import tempfile
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, ROOT)
from ahab import mail, telegram, tools
from ahab.memory import Memory

INBOX = []          # what the fake Gmail search returns, newest first
QUERIES = []
SENT = []


def message(id, sender, subject="Hi", thread=None):
    return {"id": id, "threadId": thread or f"t{id}", "labels": ["INBOX", "UNREAD"], "snippet": "",
            "headers": {"From": sender, "Subject": subject, "Date": "Sun, 18 Oct 2026 12:00:00 -0400"}}


def arrive(*messages):
    for m in messages:
        INBOX.insert(0, m)


class Fake(BaseHTTPRequestHandler):
    def _answer(self, body):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        query = dict(urllib.parse.parse_qsl(url.query))
        if url.path == "/webhook/ahab/email/search":
            QUERIES.append(query["q"])
            self._answer({"messages": INBOX[:int(query["max"])]})
        elif url.path == "/webhook/ahab/email/read":
            m = next(m for m in INBOX if m["id"] == query["id"])
            self._answer({"message": {"id": m["id"], "threadId": m["threadId"], "payload": {"headers": [
                {"name": k, "value": v} for k, v in m["headers"].items()]}}})
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        SENT.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        self._answer({"id": f"sent{len(SENT):04d}"})

    def log_message(self, *a):
        pass


server = HTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=server.serve_forever, daemon=True).start()
os.environ["AHAB_N8N_URL"] = f"http://127.0.0.1:{server.server_port}/webhook"
os.environ["AHAB_N8N_KEY"] = "test-key"
home = Path(tempfile.mkdtemp())
m = Memory(home)
T0 = 1_800_000_000.0
MIN = 60
DAY = 24 * 60 * MIN


def events():
    return [e.content for e in m.recent(200) if e.kind == "email"]


def check(now):
    """What the heartbeat does while sleeping: poll (forced) and ask whether to wake."""
    mail._watch["polled"] = 0
    return mail.waiting(m, now)


# No list: the watch does nothing and asks n8n nothing.
assert not check(T0) and not QUERIES
(home / "email.json").write_text(json.dumps({
    "owner": "owner@example.com",
    "allowed": ["friend@example.com", "bot@example.com", "other-bot@example.com"],
    "agents": ["bot@example.com", "other-bot@example.com", "owner@example.com", "stranger@example.com"]}))

# The first look only notes what's already there.
arrive(message("old000001", "Friend <friend@example.com>"), message("old000002", "bot@example.com"))
assert not check(T0)
assert "from:bot@example.com" in QUERIES[-1] and "from:owner@example.com" in QUERIES[-1] \
    and "from:stranger" not in QUERIES[-1] and QUERIES[-1].startswith("in:inbox newer_than:2d {"), QUERIES[-1]
mail.deliver(m, T0)
assert not events()

# Polls are spaced POLL_SECONDS apart.
n = len(QUERIES)
mail.waiting(m, T0 + 10)
assert len(QUERIES) == n

# A person wakes Ahab at the next check; the owner too, with a word of caution.
arrive(message("person001", "Friend <Friend@Example.com>", "Lunch?"), message("owner0001", "owner@example.com", "Note"))
assert check(T0 + MIN)
assert mail.deliver(m, T0 + MIN) == 2
assert "a person on your email list: 'Lunch?' (id person001)" in events()[-2], events()
assert "your owner's address" in events()[-1] and "check with them on Telegram" in events()[-1]
assert not check(T0 + 2 * MIN)

# A stranger (somehow in the results) is noted, never delivered.
arrive(message("strange01", "stranger@example.com"))
assert not check(T0 + 3 * MIN)
mail.deliver(m, T0 + 3 * MIN)
assert not any("stranger" in e for e in events())

# An agent slows down as it goes on: 1 min, 10 min, 1 hour, 6 hours, a day.
t = T0 + 10 * MIN
waits = []
for i, delay in enumerate((MIN, 10 * MIN, 60 * MIN, 6 * 60 * MIN, 24 * 60 * MIN), start=1):
    arrive(message(f"bot{i:06d}", "Bot <bot@example.com>", f"Round {i}", thread="tbot1"))
    assert not check(t), i
    page = mail.describe_waiting(m, t)
    assert f"'Round {i}'" in page and "more slowly" in page, page
    assert not check(t + delay - 5), i
    assert check(t + delay), i
    mail.deliver(m, t + delay)
    assert f"their {i} today" in events()[-1] and "letting the thread end" in events()[-1], events()[-1]
    t = t + delay + MIN
    waits.append(delay)
# That was five in about a day and a half, so the count has dropped: the first ones are over a day old.
assert mail._agent_count(m, "bot@example.com", t) < 5

# Another agent's count is its own.
arrive(message("obot00001", "other-bot@example.com"))
assert not check(t) and check(t + MIN)
mail.deliver(m, t + MIN)
assert "their 1 today" in events()[-1]

# The ceiling: a seventh email in a day is held. It doesn't wake Ahab, Ahab hears about
# it at the next wake, and send_email won't write to that agent.
t = T0 + 5 * 24 * 60 * MIN
for i in range(1, 8):
    arrive(message(f"burst{i:04d}", "bot@example.com", f"Burst {i}", thread="tbot2"))
    check(t)
    t += 1
assert mail._agent_count(m, "bot@example.com", t) == 7
assert check(t + MIN)                                      # the first of the burst is due after a minute
held = m.db.execute("SELECT id FROM mail_seen WHERE state = 'held'").fetchall()
assert held == [("burst0007",)], held
mail.deliver(m, t + 2 * DAY)
assert "burst0007" in events()[-1] and "past 6 from them today" in events()[-1] and "/talk" in events()[-1], events()[-1]
told = len(events())
mail.deliver(m, t + 3 * DAY)
assert len(events()) == told                            # told once, not every wake
n = len(SENT)
assert mail._agent_count(m, "bot@example.com", t) > mail.AGENT_CEILING
real_now = mail.time.time
mail.time.time = lambda: t
try:
    out = tools.run(m, "send_email", {"to": "bot@example.com", "subject": "x", "body": "y"})
    assert "paused from writing to them" in out and len(SENT) == n, out
    assert "Sent to friend@example.com" in tools.run(m, "send_email", {"to": "friend@example.com", "subject": "x", "body": "y"})
finally:
    mail.time.time = real_now

# A person writing in the agent's thread lifts its brake, and its waiting emails come through now.
t2 = T0 + 10 * DAY
for i in range(1, 4):
    arrive(message(f"loop{i:05d}", "bot@example.com", f"Loop {i}", thread="tbot3"))
    check(t2)
    t2 += 1
assert mail._agent_count(m, "bot@example.com", t2) == 3
assert m.db.execute("SELECT COUNT(*) FROM mail_seen WHERE state = 'waiting' AND wake > ?", (t2,)).fetchone()[0] == 3
arrive(message("human0001", "friend@example.com", "Re: Loop", thread="tbot3"))
check(t2 + MIN)
assert mail._agent_count(m, "bot@example.com", t2 + MIN) == 0
assert check(t2 + MIN) and mail.deliver(m, t2 + MIN) == 4
assert "a person on your email list" in events()[-1]

# The owner's /talk on Telegram lifts every brake, in code, and doesn't reach Ahab's inbox.
class Bot:
    said = []

    def send(self, chat, text):
        self.said.append(text)


t3 = T0 + 20 * DAY
for i in range(1, 8):
    arrive(message(f"again{i:04d}", "other-bot@example.com", f"Again {i}", thread="tobot"))
    check(t3)
    t3 += 1
assert mail._agent_count(m, "other-bot@example.com", t3) == 7
state = {"owner_chat_id": 5, "owner_user_id": 7}
before = m.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] if m.db.execute(
    "SELECT name FROM sqlite_master WHERE name = 'messages'").fetchone() else 0
real_now = mail.time.time
mail.time.time = lambda: t3
try:
    telegram.handle_update({"message": {"chat": {"type": "private", "id": 5}, "from": {"id": 7}, "text": " /Talk "}},
                           state, m, Bot())
finally:
    mail.time.time = real_now
assert "can email the agents again" in Bot.said[-1]
assert mail._agent_count(m, "other-bot@example.com", t3) == 0
assert "lifted the brakes" in events()[-1]
after = m.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] if m.db.execute(
    "SELECT name FROM sqlite_master WHERE name = 'messages'").fetchone() else 0
assert after == before
# A stranger's /talk is ignored.
telegram.handle_update({"message": {"chat": {"type": "private", "id": 9}, "from": {"id": 9}, "text": "/talk"}},
                       state, m, Bot())
assert len(Bot.said) == 1

# The owner's view.
assert "other-bot@example.com: 0 email(s)" in mail.status(m, t3)

# /talk released the waiting ones: they're due now.
assert check(t3) and mail.deliver(m, t3) == 7          # the held one too, as ordinary mail
assert not any("again0007" in e and "past 6" in e for e in events())

# n8n down: the watch says so once and carries on.
os.environ["AHAB_N8N_URL"] = "http://127.0.0.1:1/webhook"
mail._watch["error"] = ""
assert not check(t3 + DAY) and mail._watch["error"]
assert not check(t3 + DAY + MIN)
print("mail watch ok")
