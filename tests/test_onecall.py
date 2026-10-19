"""One-call chat: the final reply carries the message, no second model call."""
import json
import sys
from pathlib import Path
ROOT = str(Path(__file__).resolve().parent.parent)
import tempfile
from pathlib import Path

sys.path.insert(0, ROOT)
from perro import heartbeat, inbox
from perro.memory import Memory
from perro.model import Reply


class Fake:
    def __init__(self, replies):
        self.replies, self.seen = list(replies), []

    def complete(self, messages, temperature=0.7, json_mode=False):
        self.seen.append([dict(m) for m in messages])
        return Reply(json.dumps(self.replies.pop(0)), "fake", 100, 10)


prompt = heartbeat.system_prompt("Perro")
assert '"message": "<what to send your owner now' in prompt
assert "3. Message your owner" in prompt and "5. Choose when to wake next" in prompt
assert "every rewrite costs your owner money" in prompt

home = Path(tempfile.mkdtemp())
m = Memory(home)
m.write_notes("Open: nothing.")
kinds = lambda: [e.kind for e in m.recent(100)]

# 1. A plain chat: one model call, the reply delivered, notes untouched.
inbox.owner_sends(m, "How are you?\nSecond line.")
before = len(m.recent(1000))
f = Fake([{"thought": "Easy.", "message": "Fine, thanks.", "notes": None, "next_wake_minutes": 600}])
assert heartbeat.beat(m, f, prompt) == 600
assert len(f.seen) == 1, "one model call for a plain chat"
# The page never names the model; DeepSeek's peak prices show only on DeepSeek.
page = f.seen[0][1]["content"]
assert "Pricing right now" not in page and "gpt-6" not in page and "deepseek" not in page.lower()
f = Fake([{"thought": "-", "message": None, "notes": None, "next_wake_minutes": 600}])
f.backend = "deepseek"
heartbeat.beat(m, f, prompt)
page = f.seen[0][1]["content"]
assert "Pricing right now: your thinking costs double at peak" in page and "deepseek" not in page.lower(), page[:600]
from perro.model import ModelClient
assert ModelClient.from_backend("local").backend == "local"   # page_note stays out of the client
# The canary: a model call that used Codex's own tools is logged for Perro.
assert not [e for e in m.recent(1000) if e.kind == "model_tools"]
f = Fake([{"thought": "-", "message": None, "notes": None, "next_wake_minutes": 600}])
f.foreign = ["command_execution", "command_execution"]
heartbeat.beat(m, f, prompt)
leak = [e for e in m.recent(1000) if e.kind == "model_tools"]
assert len(leak) == 1 and "(command_execution)" in leak[0].content, leak
convo = inbox.describe_recent(m)
assert convo.index("Owner: How are you?") < convo.index("You: Fine, thanks."), convo
new = m.recent(1000)[before:]
assert [e.kind for e in new].count("notes") == 0
tool = [e for e in new if e.kind == "tool"][0]
assert tool.content.startswith("message_owner (final reply) -> Sent"), tool.content
assert "[0 tool calls" in new[-1].content, new[-1].content

# The log line for a message no longer looks empty.
snap = m.snapshot()
assert "From the owner (sent" in snap and "(text under Recent conversation)" in snap
assert "Second line." not in snap.split("## Recent events")[1]

# 2. Same notes handed back: not rewritten.
inbox.owner_sends(m, "ok")
before = len(m.recent(1000))
heartbeat.beat(m, Fake([{"thought": "x", "message": "ok", "notes": "Open: nothing.", "next_wake_minutes": 60}]), prompt)
assert "notes" not in [e.kind for e in m.recent(1000)[before:]]

# 3. A message that can't go out: logged, and Perro wakes soon instead of sleeping on it.
inbox.owner_sends(m, "write me a lot")
minutes = heartbeat.beat(m, Fake([{"thought": "x", "message": "y" * (inbox.MESSAGE_CHAR_LIMIT + 1000), "notes": None, "next_wake_minutes": 600}]), prompt)
assert minutes == heartbeat.RETRY_MINUTES, minutes
errs = [e.content for e in m.recent(5) if e.kind == "error"]
assert errs and "final message was NOT sent" in errs[-1], errs
assert "y" * 100 not in inbox.describe_recent(m)

# 4. A message inside a tool request isn't sent, and Perro is told so right away.
inbox.owner_sends(m, "list my files")
f = Fake([{"thought": "look", "tool": "list_files", "args": {}, "message": "Here are your files"},
          {"thought": "done", "message": "Nothing in there yet.", "notes": None, "next_wake_minutes": 60}])
heartbeat.beat(m, f, prompt)
told = f.seen[1][-1]["content"]
assert 'NOT sent' in told, told
convo = inbox.describe_recent(m)
assert "Here are your files" not in convo and "You: Nothing in there yet." in convo

# 5. null or empty message: nothing sent. message_owner still works as before.
count = lambda: inbox.describe_recent(m).count("You:")
n = count()
heartbeat.beat(m, Fake([{"thought": "quiet", "message": None, "notes": None, "next_wake_minutes": 60}]), prompt)
heartbeat.beat(m, Fake([{"thought": "quiet", "message": "", "notes": None, "next_wake_minutes": 60}]), prompt)
assert count() == n
heartbeat.beat(m, Fake([{"thought": "early word", "tool": "message_owner", "args": {"text": "On it."}},
                        {"thought": "done", "message": "Done.", "notes": "Open: report.", "next_wake_minutes": 60}]), prompt)
convo = inbox.describe_recent(m)
assert convo.index("You: On it.") < convo.index("You: Done.") and m.read_notes() == "Open: report."
print("one-call tests passed")
