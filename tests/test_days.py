"""The day files count the tokens a wake logs, in the format wakes use now and the older one."""
import sys
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ahab import days

when = datetime(2026, 10, 21, 9, 0).astimezone()
events = [
    (1, when, "wake", "Waking."),
    (2, when, "sleep", "Sleeping 60 min. [3 tool calls, 1000 in (400 cached), 200 out]"),
    (3, when, "sleep", "Sleeping 5 min. [0 tool calls, 50 in, 7 out]"),
]
text, wakes, t_in, t_out = days.render_day("2026-10-21", events, 0)
assert (wakes, t_in, t_out) == (1, 1050, 207), (wakes, t_in, t_out)
assert "1,050 tokens in" in text and "207 out" in text
print("ok")
