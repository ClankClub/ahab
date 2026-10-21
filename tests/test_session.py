"""The running session: one conversation across wake-ups, folded into a summary when it grows."""
import json
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, ROOT)
from ahab import cli_model, console, heartbeat, inbox, session
from ahab.memory import Memory
from ahab.model import ModelError, Reply


class Fake:
    """Answers the wake-up from a list of JSON replies, and a summary request with `summary`."""

    def __init__(self, replies, summary="You and your owner talked about whales. Nothing is open."):
        self.replies, self.summary, self.seen, self.asked = list(replies), summary, [], []

    def complete(self, messages, temperature=0.7, json_mode=False):
        if not json_mode:                                   # the summary call
            self.asked.append(messages)
            if isinstance(self.summary, Exception):
                raise self.summary
            return Reply(self.summary, "fake", 500, 50, cached_tokens=100)
        self.seen.append([dict(m) for m in messages])
        return Reply(json.dumps(self.replies.pop(0)), "fake", 100, 10)


def done(message=None, minutes=600, notes=None):
    return {"thought": "done", "message": message, "notes": notes, "next_wake_minutes": minutes}


prompt = heartbeat.system_prompt("Ahab")
assert "your session" in prompt and "Right now" in prompt and "Recent conversation" not in prompt

home = Path(tempfile.mkdtemp())
m = Memory(home)
m.write_notes("Open: the whale essay.")

# 0. A home from before sessions: its last finished wake and the conversation it had.
inbox.owner_sends(m, "Old question about tides")
inbox.deliver(m)
inbox.ahab_sends(m, "Old answer about tides")
m.record("sleep", "Sleeping 600 min. [0 tool calls, 1 in (0 cached), 1 out]")
m.record("alarm", "Alarm: water the plants")                  # between wakes: belongs in the next opening

# 1. First wake: the old conversation seeds the summary; the opening has the owner's message and the alarm.
inbox.owner_sends(m, "Tell me about whales.\nBlue ones.")
f = Fake([{"thought": "look it up", "tool": "write_file", "args": {"path": "whales.md", "content": "w" * 5000}},
          done("Blue whales are big.", notes="Open: the whale essay, now drafted in whales.md.")])
assert heartbeat.beat(m, f, prompt) == 600
first = f.seen[0]
assert len(first) == 2, "first wake: one page, the session merged into it"
page = first[1]["content"]
assert page.index("## Who you are (SOUL.md)") < page.index("## Earlier in this session") < page.index("Woke at") \
    < page.index("## Right now") < page.index("## Your notes") < page.index("## Your memory folder")
assert "Owner: Old question about tides" in page and "You: Old answer about tides" in page, "seeded from the old page"
opening = page.split("Woke at")[1].split("## Right now")[0]
assert "Owner: Tell me about whales.\nBlue ones." in opening and "alarm: Alarm: water the plants" in opening, opening
assert "Old question" not in opening, "what the last finished wake already saw isn't news"

# 2. Second wake: the first wake's turns are on the page as the model saw them, cut where they were long.
inbox.owner_sends(m, "And orcas?")
f2 = Fake([done("Orcas are dolphins.")])
heartbeat.beat(m, f2, prompt)
second = f2.seen[0]
roles = [x["role"] for x in second]
assert roles == ["system", "user", "assistant", "user", "assistant", "user"], roles
assert all(a != b or a == "system" for a, b in zip(roles, roles[1:])), "no two turns in a row from one side"
tool_reply, tool_result, final = second[2]["content"], second[3]["content"], second[4]["content"]
assert json.loads(tool_reply)["args"]["content"].startswith("w" * 600) and "more chars" in tool_reply
assert tool_result.startswith("Tool result:\nCreated whales.md") and len(tool_result) < 1500, tool_result
assert json.loads(final)["message"] == "Blue whales are big."
assert json.loads(final)["notes"].startswith("(you rewrote your notes here:"), "notes kept by length only"
now_text = second[5]["content"]
assert "Owner: And orcas?" in now_text and "Tell me about whales" not in now_text.split("## Right now")[0]
assert "write_file" not in now_text.split("## Right now")[0], "the wake's own tool calls aren't news again"
assert "now drafted in whales.md" in now_text and sum(x["content"].count("## Your notes") for x in second) == 1

# 3. The page's opening stays the same from wake to wake (what a prompt cache needs).
f3 = Fake([done()])
heartbeat.beat(m, f3, prompt)
third = f3.seen[0]
assert third[:len(second) - 1] == second[:-1], "everything before the last wake's opening is unchanged"
s2 = cli_model.flatten(second[:-1])[1]
assert cli_model.flatten(third)[1].startswith(s2.rsplit("\n\nNow write your next reply", 1)[0])
assert "Nothing new since your last wake-up." in third[-1]["content"]

# 4. A failed wake leaves its note in the session, and the next opening follows it without a gap.
class Down(Fake):
    def complete(self, messages, temperature=0.7, json_mode=False):
        raise ModelError("the model is down")


assert heartbeat.beat(m, Down([]), prompt) == heartbeat.RETRY_MINUTES
f4 = Fake([done()])
heartbeat.beat(m, f4, prompt)
last = f4.seen[0][-1]["content"]
assert "[This wake-up failed here (ModelError: the model is down)" in last and last.count("Woke at") == 2, last[:600]

# 5. Shrinking for the session.
long = json.dumps({"thought": "t" * 4000, "tool": "write_file", "args": {"path": "a", "content": "x" * 2000},
                   "message": "m" * 3000})
kept = json.loads(session.shrink_reply(long))
assert len(kept["thought"]) < 3100 and len(kept["args"]["content"]) < 700 and kept["message"] == "m" * 3000
assert session.shrink_reply("not json at all") == "not json at all"
assert session.shrink_result("short") == "short" and len(session.shrink_result("r" * 5000)) < 800

# 6. Folding: past its size the oldest wakes become a summary; the newest stays word for word.
before = session.size(m)
assert before > 0
wakes = len(session._wakes(m))
assert session.compact(m, f4, 100_000) == "under its size"
result = session.compact(m, f4, 400)
assert result.startswith("folded"), result
assert len(session._wakes(m)) < wakes and len(session._wakes(m)) >= 1 and session.size(m) < before
assert "whales" in session.summary(m)
asked = f4.asked[-1][1]["content"]
assert "## The summary so far" in asked and "Old question about tides" in asked and "Blue whales are big." in asked
ev = [e for e in m.recent(50) if e.kind == "session"][-1]
assert "[500 in (100 cached), 50 out]" in ev.content.splitlines()[0], ev.content
f5 = Fake([done()])
heartbeat.beat(m, f5, prompt)
page5 = "\n\n".join(x["content"] for x in f5.seen[0][1:])
assert "## Earlier in this session (your summary)\nYou and your owner talked about whales." in page5
assert "Old question about tides" not in page5, "the old seed was folded into the new summary"
assert "Folded" in page5.split("## Right now")[0], "Pip hears that it was folded"
assert session.summary(m) and m.db.execute("SELECT COUNT(*) FROM session WHERE role = 'summary' AND folded = 0"
                                           ).fetchone()[0] == 1
assert m.db.execute("SELECT COUNT(*) FROM session WHERE folded = 1").fetchone()[0] > 0, "folded turns are kept"

# The heartbeat folds by itself at the end of a wake that leaves the session too big.
f6 = Fake([done("hi")], summary="Short summary.")
heartbeat.beat(m, f6, prompt, session_tokens=200)
assert f6.asked and session.summary(m) == "Short summary."

# 7. A summary that fails changes nothing and waits an hour; far past the size it folds anyway.
clock = [time.time()]
bad = Fake([], summary=ModelError("rate limited"))
for _ in range(3):
    session.add(m, 10**6, "user", "filler " * 400)
    session.add(m, 10**6 + 1, "user", "more filler " * 400)
size = session.size(m)
cap = int(size / 1.2)                                         # over the size, under one and a half times it
assert session.compact(m, bad, cap, clock=lambda: clock[0]) == "failed" and session.size(m) == size
assert "it's tried again in an hour" in m.recent(1)[0].content
assert session.compact(m, bad, cap, clock=lambda: clock[0] + 60) == "waiting to retry" and len(bad.asked) == 1
clock[0] += 3601
assert session.compact(m, bad, cap, clock=lambda: clock[0]) == "failed" and len(bad.asked) == 2
assert session.compact(m, bad, size // 2, clock=lambda: clock[0] + 60) == "folded without a summary"
assert "without a summary, because writing one failed (rate limited)" in session.summary(m)
assert session.summary(m).startswith("Short summary."), "the summary before it is kept"

# 8. The owner's fresh start.
session.start_fresh(m)
assert session.turns(m) == [] and "started this session fresh" in session.summary(m)
f7 = Fake([done()])
heartbeat.beat(m, f7, prompt)
assert len(f7.seen[0]) == 2 and "started this session fresh" in f7.seen[0][1]["content"]

# 9. How big: home.json, else the backend, else the default.
assert session.limit({}) == session.DEFAULT_TOKENS
assert session.limit({"backend": "codex-luna"}) == session.DEFAULT_TOKENS
assert session.limit({"backend": "local"}) == 16_000
assert session.limit({"backend": "local", "session_tokens": 50_000}) == 50_000

# 10. The status screen shows it.
assert console.session_size(home, {}).startswith("session ") and console.session_size(home, {}).endswith("/100k")
assert console.session_size(Path(tempfile.mkdtemp()), {}) == ""

# 11. merge joins back-to-back turns of one side, never the system prompt or a message with images.
merged = session.merge([{"role": "system", "content": "s"}, {"role": "system", "content": "t"},
                        {"role": "user", "content": "a"}, {"role": "user", "content": "b"},
                        {"role": "user", "content": "c", "images": [{"name": "x"}]}])
assert [x["content"] for x in merged] == ["s", "t", "a\n\nb", "c"]

# 12. Restarts keep the wake-up Pip chose.
m.record("sleep", "Sleeping 600 min. [0 tool calls, 1 in (0 cached), 1 out]")
at = heartbeat.planned_wake(m)
assert at is not None and abs(at - (time.time() + 600 * 60)) < 120
assert "kept your planned wake-up at" in heartbeat.start_message(home, False, False, at)
assert "woke immediately" in heartbeat.start_message(home, False, False, None)
m.record("wake", "Woke at now.")                               # a wake that never finished: wake now
assert heartbeat.planned_wake(m) is None
past = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(timespec="seconds")
m.db.execute("INSERT INTO events (time, kind, content) VALUES (?, 'sleep', 'Sleeping 60 min.')", (past,))
m.db.commit()
assert heartbeat.planned_wake(m) is None, "a planned wake already past means wake now"

m.close()
print("session ok")
