"""The approval step: request, card, decide (CLI and Telegram), run once, report."""
import json
import subprocess
import sys
from pathlib import Path
ROOT = str(Path(__file__).resolve().parent.parent)
import tempfile
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, ROOT)
from perro import approvals, heartbeat, telegram
from perro.memory import Memory
from perro.model import Reply

home = Path(tempfile.mkdtemp())
m = Memory(home)
events = lambda kind: [e.content for e in m.recent(1000) if e.kind == kind]

# --- filing ---------------------------------------------------------------
for bad_kind, bad_payload, why in [("nope", {}, "No action called"),
                                   ("test", {}, "short text"),
                                   ("test", {"text": "x" * 201}, "short text"),
                                   ("test", ["list"], "must be an object")]:
    try:
        approvals.request(m, bad_kind, bad_payload)
        raise AssertionError("should refuse")
    except ValueError as e:
        assert why in str(e), e
try:
    approvals.request(m, "test", {"text": "hi"}, note="n" * 501)
    raise AssertionError("long note")
except ValueError as e:
    assert "limit" in str(e)

a = approvals.request(m, "test", {"text": "first"}, note="  just   checking\n the path ")
req = approvals.get(m, a)
assert req.status == "pending" and req.note == "just checking the path", req
assert "Text: first" in req.card and req.card.startswith("A test approval.")
t = req.text()
assert t.startswith(f"Perro asks your approval (#{a}):") and "Perro's note: just checking" in t
assert "Expires " in t, t
page = approvals.describe_pending(m)
assert page.startswith("## Waiting on your owner's approval") and f"#{a} (asked" in page, page
assert not approvals.waiting(m), "pending isn't approved"

# --- deciding is one-shot --------------------------------------------------
assert approvals.decide(m, 999, True, "x") == "There's no request #999."
assert approvals.decide(m, a, True, "owner at the command line").startswith("Approved")
assert approvals.decide(m, a, False, "owner") == f"#{a} was already approved."
assert approvals.waiting(m)
assert approvals.describe_pending(m) == ""

b = approvals.request(m, "test", {"text": "second"})
assert approvals.decide(m, b, False, "owner").startswith("Denied")
assert approvals.waiting(m), "a still waits to run"

# --- running: once, by code, outcomes logged ---------------------------------
ran = []
approvals.register("boom", lambda p: "Will fail.", lambda mem, p: (_ for _ in ()).throw(RuntimeError("nope")))
approvals.register("count", lambda p: "Counts.", lambda mem, p: ran.append(p) or "counted")
c = approvals.request(m, "boom", {})
d = approvals.request(m, "count", {"n": 1})
approvals.decide(m, c, True, "owner")
approvals.decide(m, d, True, "owner")
assert approvals.run_decided(m) == 4
assert approvals.run_decided(m) == 0, "nothing runs twice"
assert ran == [{"n": 1}], ran
st = {i: approvals.get(m, i).status for i in (a, b, c, d)}
assert st == {a: "done", b: "denied", c: "failed", d: "done"}, st
assert approvals.get(m, a).result == "The test went through: first"
log = events("approval")
assert any(f"approved #{a}" in x and "Done: The test went through: first" in x for x in log), log
assert any(f"denied #{b}" in x and "wasn't done" in x for x in log), log
assert any(f"#{c}" in x and "It failed: RuntimeError: nope" in x for x in log), log
assert not approvals.waiting(m)

# A run claimed elsewhere (status changed under us) isn't run again.
e = approvals.request(m, "count", {"n": 2})
approvals.decide(m, e, True, "owner")
m.db.execute("UPDATE approvals SET status='running' WHERE id=?", (e,)); m.db.commit()
approvals.run_decided(m)
assert ran == [{"n": 1}], "a running request isn't started twice"

# --- expiry ------------------------------------------------------------------
f = approvals.request(m, "test", {"text": "old"})
old = (approvals._now() - timedelta(hours=approvals.EXPIRE_HOURS, minutes=1)).isoformat()
m.db.execute("UPDATE approvals SET created=? WHERE id=?", (old, f)); m.db.commit()
assert approvals.decide(m, f, True, "owner") == f"#{f} was already expired."
approvals.run_decided(m)
assert any(f"#{f}" in x and "expired after 48 hours" in x for x in events("approval"))
assert "Expired unanswered" in approvals.get(m, f).closed_text()

# --- the pending cap -----------------------------------------------------------
ids = [approvals.request(m, "test", {"text": f"n{i}"}) for i in range(approvals.MAX_PENDING)]
try:
    approvals.request(m, "test", {"text": "one too many"})
    raise AssertionError("cap")
except ValueError as e:
    assert "already waiting" in str(e)
for i in ids:
    approvals.decide(m, i, False, "owner")
approvals.run_decided(m)
print("core ok")

# --- the heartbeat: approval runs before the model, shows on the page ----------
class Fake:
    def __init__(self):
        self.seen = []
    def complete(self, messages, temperature=0.7, json_mode=False):
        self.seen.append(messages[1]["content"])
        return Reply(json.dumps({"thought": "ok", "message": None, "notes": None,
                                 "next_wake_minutes": 60}), "fake", 10, 1)

g = approvals.request(m, "test", {"text": "via the heartbeat"})
h = approvals.request(m, "test", {"text": "still waiting"})
approvals.decide(m, g, True, "owner")
fake = Fake()
heartbeat.beat(m, fake, heartbeat.system_prompt("Perro"))
page = fake.seen[0]
assert approvals.get(m, g).status == "done"
assert "Done: The test went through: via the heartbeat" in page, "outcome is in the events Perro sees"
assert "## Waiting on your owner's approval" in page and f"#{h} (asked" in page
assert page.index("## Your alarms") < page.index("## Waiting on") < page.index("## Your skills")
approvals.decide(m, h, False, "owner")
fake = Fake()
heartbeat.beat(m, fake, heartbeat.system_prompt("Perro"))
assert "## Waiting on" not in fake.seen[0], "no section when nothing waits"
print("heartbeat ok")

# --- Telegram ------------------------------------------------------------------
OWNER, STRANGER = {"id": 111}, {"id": 999}
state = {"owner_chat_id": 111, "owner_user_id": 111}

class FakeBot(telegram.Bot):
    def __init__(self):
        super().__init__("SECRET-TOKEN")
        self.calls, self.next_id = [], 500
    def call(self, method, http_timeout=30, **p):
        self.calls.append((method, p))
        if method == "sendMessage":
            self.next_id += 1
            return {"message_id": self.next_id}
        return True

bot = FakeBot()
k = approvals.request(m, "test", {"text": "tap me"})
telegram.forward_approvals(state, m, bot)
method, p = bot.calls[-1]
assert method == "sendMessage" and p["chat_id"] == 111 and "Text: tap me" in p["text"]
buttons = p["reply_markup"]["inline_keyboard"][0]
assert [x["callback_data"] for x in buttons] == [f"ap:{k}:yes", f"ap:{k}:no"]
assert approvals.get(m, k).telegram_message == 501
n = len(bot.calls)
telegram.forward_approvals(state, m, bot)
assert len(bot.calls) == n, "a card is sent once"

def tap(sender, data, chat_id=111, message_id=501, chat_type="private"):
    return {"update_id": 1, "callback_query": {
        "id": "cb1", "from": sender, "data": data,
        "message": {"message_id": message_id, "chat": {"id": chat_id, "type": chat_type}}}}

def last_answer():
    return [p for meth, p in bot.calls if meth == "answerCallbackQuery"][-1]["text"]

telegram.handle_update(tap(STRANGER, f"ap:{k}:yes", chat_id=999), state, m, bot)
assert last_answer() == "Not yours to answer." and approvals.get(m, k).status == "pending"
telegram.handle_update(tap(STRANGER, f"ap:{k}:yes"), state, m, bot)   # stranger, owner's chat id
assert approvals.get(m, k).status == "pending"
telegram.handle_update(tap(OWNER, f"ap:{k}:yes", chat_type="group"), state, m, bot)
assert approvals.get(m, k).status == "pending"
telegram.handle_update(tap(OWNER, f"ap:{k}:yes", message_id=777), state, m, bot)
assert last_answer() == "That button doesn't match a request." and approvals.get(m, k).status == "pending"
for junk in ["ap:x:yes", f"ap:{k}:maybe", "hello", f"ap:{k}", ""]:
    telegram.handle_update(tap(OWNER, junk), state, m, bot)
    assert approvals.get(m, k).status == "pending", junk

telegram.handle_update(tap(OWNER, f"ap:{k}:yes"), state, m, bot)
assert approvals.get(m, k).status == "approved"
assert last_answer().startswith("Approved")
meth, p = [c for c in bot.calls if c[0] == "editMessageText"][-1]
assert p["message_id"] == 501 and p["reply_markup"] == {"inline_keyboard": []}
assert "Carrying it out." in p["text"], p["text"]
telegram.handle_update(tap(OWNER, f"ap:{k}:no"), state, m, bot)
assert last_answer() == f"#{k} was already approved."

# Telegram's decision is run by the heartbeat; then the card gets the outcome.
approvals.run_decided(m)
telegram.forward_approvals(state, m, bot)
meth, p = [c for c in bot.calls if c[0] == "editMessageText"][-1]
assert "Done: The test went through: tap me" in p["text"], p["text"]
n = len(bot.calls)
telegram.forward_approvals(state, m, bot)
assert len(bot.calls) == n, "a card is closed once"

# A denial closes the card without waiting for Perro to wake.
q = approvals.request(m, "test", {"text": "deny me"})
telegram.forward_approvals(state, m, bot)
qmsg = approvals.get(m, q).telegram_message
telegram.handle_update(tap(OWNER, f"ap:{q}:no", message_id=qmsg), state, m, bot)
telegram.forward_approvals(state, m, bot)
meth, p = [c for c in bot.calls if c[0] == "editMessageText"][-1]
assert p["message_id"] == qmsg and "Denied" in p["text"], p
# The token never shows in what the bridge would print.
assert "SECRET-TOKEN" not in json.dumps(bot.calls)
print("telegram ok")

# --- command line ----------------------------------------------------------------
m.close()
run = lambda *a: subprocess.run([sys.executable, "-m", "perro.approvals", "--home", str(home), *a],
                                cwd=ROOT, capture_output=True, text=True)
out = run("--test", "from the cli").stdout
assert out.startswith("Filed test request #"), out
num = out.split("#")[1].split(".")[0]
listed = run().stdout
assert f"(#{num})" in listed and "from the cli" in listed, listed
assert run("--no", num).stdout.startswith(f"Denied #{num}")
assert run().stdout.strip() == "Nothing waiting."
r = run("--test", "")
assert r.returncode != 0 and "short text" in r.stderr, r
print("cli ok")
print("ALL OK")
