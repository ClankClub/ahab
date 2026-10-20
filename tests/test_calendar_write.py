"""Offline tests for add_event / update_event (ahab/google.py) against a fake relay."""
import sys, tempfile
from pathlib import Path
ROOT = str(Path(__file__).resolve().parent.parent)
from datetime import datetime
from pathlib import Path
sys.path.insert(0, ROOT)
from ahab import google, n8n, tools
from ahab.memory import Memory

events, sent = {}, []
BASE = "https://www.googleapis.com/calendar/v3/calendars/primary/events"


def fake(path, payload, limit=None):
    sent.append(payload)
    assert path == "ahab/google" and payload["service"] == "calendar" and payload["url"].startswith(BASE)
    rest, method = payload["url"][len(BASE):], payload["method"]
    if method != "GET":
        assert payload["query"] == {"sendUpdates": "none"}, payload["query"]
        assert set(payload["body"]) <= {"summary", "description", "location", "start", "end", "reminders"}
    if method == "POST":
        e = dict(payload["body"], id=f"ev{len(events) + 1:04d}x")
        events[e["id"]] = e
        return {"status": 200, "body": e}
    eid = rest.lstrip("/")
    if eid not in events:
        return {"status": 404, "body": {"error": {"message": "Not Found"}}}
    if method == "PATCH":
        events[eid].update(payload["body"])
    return {"status": 200, "body": events[eid]}


n8n.send = fake
m = Memory(Path(tempfile.mkdtemp()))
run = lambda name, args: tools.run(m, name, args)
local = lambda s: datetime.strptime(s, "%Y-%m-%d %H:%M").astimezone().isoformat(timespec="seconds")

out = run("add_event", {"title": "Dentist", "start": "2026-10-20 14:30", "location": "Main St", "remind_minutes": 30})
assert out == 'Added to your calendar: "Dentist", Tue 20 Oct 14:30-15:30 @ Main St (id ev0001x).', out
e = events["ev0001x"]
assert e["start"] == {"dateTime": local("2026-10-20 14:30")} and e["end"] == {"dateTime": local("2026-10-20 15:30")}
assert e["reminders"] == {"useDefault": False, "overrides": [{"method": "popup", "minutes": 30}]}

out = run("add_event", {"title": "Trip", "start": "2026-10-23", "end": "2026-10-25"})
assert out == 'Added to your calendar: "Trip", Fri 23 Oct to Sun 25 Oct (all day) (id ev0002x).', out
assert events["ev0002x"]["end"] == {"date": "2026-10-26"}, "Google's end is the day after the last"
assert "Sat 24 Oct (all day)" in run("add_event", {"title": "Market", "start": "2026-10-24"})
assert "T" not in run("add_event", {"title": "Call", "start": "2026-10-21T09:00", "end": "2026-10-21 09:20"}).split("(id")[0][-12:]

# Moving the start keeps the length; moving the end keeps the start.
out = run("update_event", {"id": "ev0001x", "start": "2026-10-21 10:00"})
assert out == 'Updated: "Dentist", Wed 21 Oct 10:00-11:00 @ Main St (id ev0001x).', out
assert "10:00-12:15" in run("update_event", {"id": "ev0001x", "end": "2026-10-21 12:15"})
assert "Fri 23 Oct to Sun 25 Oct" in run("update_event", {"id": "ev0002x", "start": "2026-10-23"})
assert "Mon 26 Oct to Wed 28 Oct" in run("update_event", {"id": "ev0002x", "start": "2026-10-26"})
assert "Cancelled: Dentist" in run("update_event", {"id": "ev0001x", "title": "Cancelled: Dentist", "location": ""})
assert "@" not in run("update_event", {"id": "ev0001x", "notes": "they called"})
# A timed event turned into a whole day.
assert "Thu 22 Oct (all day)" in run("update_event", {"id": "ev0001x", "start": "2026-10-22"})

# Refusals, before anything is sent.
n = len(sent)
for name, args, words in [
    ("add_event", {"title": "", "start": "2026-10-20 14:00"}, "title is empty"),
    ("add_event", {"title": "x", "start": "tomorrow 2pm"}, "start must be like"),
    ("add_event", {"title": "x", "start": "2026-10-20 14:00", "end": "2026-10-20 13:00"}, "end must be after start"),
    ("add_event", {"title": "x", "start": "2026-10-20 14:00", "end": "2026-10-21"}, "both be times or both be dates"),
    ("add_event", {"title": "x", "start": "2026-10-20", "end": "2026-10-19"}, "end is before start"),
    ("add_event", {"title": "x", "start": "2026-10-20 14:00", "remind_minutes": "30"}, "remind_minutes must be"),
    ("add_event", {"title": "x", "start": "2026-10-20 14:00", "attendees": ["a@b.c"]}, "Bad args"),
    ("update_event", {"id": "ev0001x"}, "Say what to change"),
    ("update_event", {"id": "../x", "title": "y"}, "must be an id"),
]:
    out = run(name, args)
    assert words in out, (args, out)
assert len(sent) == n, "nothing reaches the relay"
assert "Google said no (HTTP 404)" in run("update_event", {"id": "nosuchevent", "title": "y"})
print("calendar write ok")
