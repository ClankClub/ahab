"""Standing intents (perro.intents): matched in code, with a fire budget, cooldown and expiry."""
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, ROOT)
from perro import intents, tools
from perro.memory import Memory

UTC = timezone.utc
m = Memory(Path(tempfile.mkdtemp()))


def wake():
    return m.record("wake", "Woke.").id


def owner_says(text):
    m.record("message", f"From the owner (sent x):\n{text}")


def last_intent_event():
    return [e for e in m.recent(10) if e.kind == "intent"][-1].content


def rest(iid, hours):
    m.db.execute("UPDATE intents SET last_fired = ? WHERE id = ?",
                 ((datetime.now(UTC) - timedelta(hours=hours)).replace(microsecond=0).isoformat(), iid))
    m.db.commit()


out = tools.run(m, "set_intent", {"when": ["the move", "deposit"], "then": "Ask whether the deposit came back."})
assert out.startswith("Standing intent #1 set") and "up to 3 time(s)" in out, out
assert '#1 when "the move" or "deposit"' in intents.describe(m)

# Only what came in this wake counts: an old message, Pip's own words and the log don't.
owner_says("We talked about the move last week.")
w = wake()
m.record("message", "To the owner:\nHow is the move going?")
m.record("thought", "deposit deposit deposit")
assert intents.check(m, w) == 0

# The owner mentions it: it fires once, into the log, and counts down.
w = wake(); owner_says("Ugh, THE   MOVE is next Friday.")
assert intents.check(m, w) == 1
event = last_intent_event()
assert "Standing intent #1 fired: your owner wrote with \"the move\"" in event, event
assert "Ask whether the deposit came back." in event and "2 more time(s)" in event, event
# Mentioned again in the cooldown: quiet. After it: fires.
w = wake(); owner_says("The deposit is still not back.")
assert intents.check(m, w) == 0
rest(1, 25)
w = wake(); owner_says("deposits, honestly")              # endings match
assert intents.check(m, w) == 1
rest(1, 25)
w = wake(); owner_says("They took the deposit.")
assert intents.check(m, w) == 1 and "its last time" in last_intent_event()
assert intents.armed(m) == [] and intents.describe(m) == "(none set)"

# Words match from the start of a word only.
tools.run(m, "set_intent", {"when": "move", "then": "x", "cooldown_hours": 0})
w = wake(); owner_says("Please remove that file.")
assert intents.check(m, w) == 0
w = wake(); owner_says("We moved!")
assert intents.check(m, w) == 1

# Email: only when asked to watch it, and then on the delivered line (sender, subject).
tools.run(m, "set_intent", {"when": ["clinic"], "then": "Remind them to book the follow-up.", "watch": "email"})
w = wake(); owner_says("The clinic called.")
assert intents.check(m, w) == 0
w = wake(); m.record("email", "New email from a person on your email list: 'Clinic results' (id 7).")
assert intents.check(m, w) == 1 and "an email came in" in last_intent_event()

# At most three fire in a wake, whatever matches.
for i in range(5):
    tools.run(m, "set_intent", {"when": ["banana"], "then": f"b{i}"})
w = wake(); owner_says("banana")
assert intents.check(m, w) == intents.MAX_FIRES_PER_WAKE

# Expiry: logged once, then gone.
m.db.execute("UPDATE intents SET expires = ? WHERE message = 'b4'",
             ((datetime.now(UTC) - timedelta(minutes=1)).isoformat(),))
m.db.commit()
w = wake()
intents.check(m, w)
assert "expired without being used up" in last_intent_event()
assert all(row[2] != "b4" for row in intents.armed(m))

# Cancel, and refusals that set nothing.
armed_before = len(intents.armed(m))
iid = intents.armed(m)[0][0]
assert tools.run(m, "cancel_intent", {"id": iid}) == f"Canceled standing intent #{iid}."
assert "No armed standing intent" in tools.run(m, "cancel_intent", {"id": iid})
for bad, why in [({"when": [], "then": "x"}, "at least one word"),
                 ({"when": ["ok"], "then": "x"}, "too short"),
                 ({"when": ["fine"], "then": ""}, "then needs"),
                 ({"when": ["fine"], "then": "x", "watch": "web"}, "watch must be one of"),
                 ({"when": ["fine"], "then": "x", "times": 0}, "times must be between")]:
    out = tools.run(m, "set_intent", bad)
    assert why in out, (bad, out)
assert len(intents.armed(m)) == armed_before - 1
print("intents ok")

# The guidance mail.py appends to an email event isn't matched, only sender and subject.
tools.run(m, "set_intent", {"when": ["answer"], "then": "x", "watch": "email"})
w = wake(); m.record("email", "New email from a@b.c: 'Hi' (id 9). read_email shows it; answer with send_email.")
assert intents.check(m, w) == 0
print("intents ok (email guidance)")
