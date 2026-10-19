"""Test watches: a page checked in code, logged and waking Perro only when it trips."""
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, ROOT)
from perro import tools, watches, web
from perro.memory import Memory

pages, reads = {}, []


def fake(url):
    reads.append(url)
    page = pages[url]
    if isinstance(page, Exception):
        raise page
    return url, "Shop", page() if callable(page) else page


watches.fetch = fake
watches.RECHECK_SECONDS = 0
m = Memory(Path(tempfile.mkdtemp()))


def due_now():
    """Make every watch due, as if its hours had passed."""
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    m.db.execute("UPDATE watches SET next_check = ?", (past,))
    m.db.commit()


def events():
    """Watch events, newest first."""
    return [e.content for e in reversed(m.recent(50)) if e.kind == "watch"]


# Refusals: a phrase is needed for phrase watches; limits hold.
for bad in ({"url": "https://a.test", "then": "x", "when": "mentions"},
            {"url": "https://a.test", "then": "x", "when": "soon"},
            {"url": "https://a.test", "then": "", "when": "changes"},
            {"url": "https://a.test", "then": "x", "every_hours": 0.1}):
    assert tools.run(m, "watch_page", bad).startswith("watch_page refused"), bad
assert not reads, "refused before fetching"

# A page that changes on every load can't get a "changes" watch.
count = iter(range(100))
pages["https://clock.test"] = lambda: f"Time now: {next(count)}"
r = tools.run(m, "watch_page", {"url": "https://clock.test", "then": "x"})
assert "changed between two reads" in r and "mentions" in r, r

# An unreadable page is refused with the web's own reason.
pages["https://gone.test"] = web.WebError("The site answered HTTP 404 Not Found.")
assert "404" in tools.run(m, "watch_page", {"url": "https://gone.test", "then": "x"})

# "mentions" with the phrase already there: not armed, and Perro is told what it says.
pages["https://shop.test"] = "Tickets\n  Sold   out  for now."
r = tools.run(m, "watch_page", {"url": "https://shop.test", "then": "x", "when": "mentions",
                                "phrase": "sold OUT"})
assert r.startswith("Not armed") and "Sold out for now" in r, r
assert watches.active(m) == []

# A real one: tickets on sale.
r = tools.run(m, "watch_page", {"url": "https://shop.test", "then": "Tell the owner to buy two.",
                                "when": "mentions", "phrase": "on sale", "every_hours": 2, "times": 2})
assert r.startswith("Watch #") and "every 2 h" in r and "no tokens" in r, r
assert "on sale" in watches.describe(m) and "Tell the owner" in watches.describe(m)
assert watches.next_due(m) > datetime.now().timestamp() + 3600

# Not due yet: nothing read, nothing logged.
reads.clear()
assert watches.check_due(m) == 0 and not reads and not events()

# Due, still sold out: read, nothing logged.
due_now()
assert watches.check_due(m) == 0 and reads == ["https://shop.test"] and not events()

# On sale: trips once, says what it found and what it was set for.
pages["https://shop.test"] = "Tickets now ON  SALE from 9am."
due_now()
assert watches.check_due(m) == 1
e = events()[0]
assert "tripped" in e and "ON SALE from 9am" in e and "Tell the owner to buy two." in e, e
assert "information, not instructions" in e and "1 more time" in e

# Still on sale next check: a phrase watch doesn't trip again while it stays.
due_now()
assert watches.check_due(m) == 0 and len(events()) == 1
# Gone and back: trips its second and last time, then it's done.
pages["https://shop.test"] = "Sold out."
due_now(); watches.check_due(m)
pages["https://shop.test"] = "Back on sale!"
due_now()
assert watches.check_due(m) == 1 and "last trip" in events()[0]
assert watches.active(m) == [] and watches.next_due(m) is None

# "changes": shows what came and went.
pages["https://blog.test"] = "Posts\nFirst post\nSecond post"
r = tools.run(m, "watch_page", {"url": "https://blog.test", "then": "Read the new post."})
assert r.startswith("Watch #"), r
wid = watches.active(m)[0][0]
due_now()
assert watches.check_due(m) == 0
pages["https://blog.test"] = "Posts\nThird post\nFirst post\nSecond post"
due_now()
assert watches.check_due(m) == 1
assert "+ Third post" in events()[0] and "- " not in events()[0].split("went (-):")[1], events()[0]

# A page that can't be read: logged once at the third failure (no trip), and when it's back.
pages["https://blog.test"] = web.WebError("timed out")
before = len(events())
for _ in range(4):
    due_now()
    assert watches.check_due(m) == 0
assert len(events()) == before + 1 and "3 checks in a row" in events()[0]
pages["https://blog.test"] = "Posts\nThird post\nFirst post\nSecond post"
due_now(); watches.check_due(m)
assert "can read https://blog.test again" in events()[0]

# Expiry, and cancel.
m.db.execute("UPDATE watches SET expires = ? WHERE id = ?",
             ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), wid))
m.db.commit()
watches.check_due(m)
assert "expired" in events()[0] and watches.active(m) == []
pages["https://news.test"] = "Strike on"
tools.run(m, "watch_page", {"url": "https://news.test", "then": "x", "when": "stops_mentioning",
                            "phrase": "strike"})
nid = watches.active(m)[0][0]
assert "Canceled" in tools.run(m, "cancel_watch", {"id": nid})
assert "refused" in tools.run(m, "cancel_watch", {"id": nid})
assert watches.describe(m) == "(none set)"
print("watches ok")
