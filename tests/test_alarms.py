"""Alarms (perro.alarms): one-offs ring once; repeating ones move on, at the same local time."""
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, ROOT)
from perro import alarms, tools
from perro.memory import Memory

UTC = timezone.utc
home = Path(tempfile.mkdtemp())

# An alarms table from before repeats gets the new column, and its alarms still ring.
db = sqlite3.connect(home / "memory.db")
db.execute("CREATE TABLE alarms (id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT NOT NULL, "
           "due TEXT NOT NULL, message TEXT NOT NULL, fired TEXT, canceled TEXT)")
db.execute("INSERT INTO alarms (created, due, message) VALUES ('2026-10-01T00:00:00+00:00', "
           "'2026-10-01T01:00:00+00:00', 'From before')")
db.commit(); db.close()
m = Memory(home)
assert alarms.ring_due(m) == 1 and "From before" in m.recent(1)[0].content
assert alarms.pending(m) == []


def past(alarm_id, ago):
    m.db.execute("UPDATE alarms SET due = ? WHERE id = ?",
                 ((datetime.now(UTC) - ago).replace(microsecond=0).isoformat(), alarm_id))
    m.db.commit()


# A one-off rings once.
out = tools.run(m, "set_alarm", {"minutes_from_now": 60, "message": "Call back"})
assert out.startswith("Alarm #2 set") and "until canceled" not in out, out
past(2, timedelta(minutes=1))
assert alarms.ring_due(m) == 1 and alarms.ring_due(m) == 0 and alarms.pending(m) == []

# A daily one rings and moves on a day, same alarm, same message.
out = tools.run(m, "set_alarm", {"minutes_from_now": 60, "message": "Read 5 chapters", "repeat": "daily"})
assert "then every day at that time until canceled" in out, out
assert "(then every day): Read 5 chapters" in alarms.describe_pending(m)
past(3, timedelta(minutes=1))
rang_at = datetime.fromisoformat(alarms.pending(m)[0][1])
assert alarms.ring_due(m) == 1
event = m.recent(1)[0].content
assert "repeats daily; next" in event and "Read 5 chapters" in event, event
(alarm_id, next_due, _), = alarms.pending(m)
assert alarm_id == 3 and datetime.fromisoformat(next_due) == alarms.next_time(rang_at, "daily")
assert timedelta(hours=23) <= datetime.fromisoformat(next_due) - rang_at <= timedelta(hours=25)
assert alarms.ring_due(m) == 0

# Off for three days: it rings once, and its next time is still ahead.
past(3, timedelta(days=3, minutes=5))
assert alarms.ring_due(m) == 1 and alarms.ring_due(m) == 0
assert datetime.fromisoformat(alarms.pending(m)[0][1]) > datetime.now(UTC)
# Canceling stops the series.
assert tools.run(m, "cancel_alarm", {"id": 3}) == "Canceled alarm #3."
assert alarms.pending(m) == []

# Weekdays skip the weekend; weekly is seven days on.
friday = datetime(2026, 10, 16, 13, 0, tzinfo=UTC)          # a Friday, 9:00 in New York
assert alarms.next_time(friday, "weekdays").astimezone().weekday() == 0
assert alarms.next_time(friday, "weekly") - friday == timedelta(days=7)
assert alarms.next_time(friday, "daily").astimezone().weekday() == 5

# Daylight saving: the local clock time holds. (Checked where the local zone is US Eastern,
# as on the owner's PC and in CI.)
before, after = datetime(2026, 10, 31, 13, 0, tzinfo=UTC), datetime(2026, 11, 2, 14, 0, tzinfo=UTC)
if before.astimezone().utcoffset() == timedelta(hours=-4) and after.astimezone().utcoffset() == timedelta(hours=-5):
    nxt = alarms.next_time(before, "daily")                  # Sat 9:00 EDT -> Sun 9:00 EST
    assert nxt == datetime(2026, 11, 1, 14, 0, tzinfo=UTC), nxt
    assert nxt.astimezone().hour == 9
else:
    print("(not US Eastern here: daylight-saving check skipped)")

# A repeat that isn't one of the three is refused, and nothing is set.
out = tools.run(m, "set_alarm", {"minutes_from_now": 60, "message": "x", "repeat": "hourly"})
assert "repeat must be one of daily, weekdays, weekly" in out and alarms.pending(m) == [], out
print("alarms ok")
