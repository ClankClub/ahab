"""The reply form: a backend that takes a JSON Schema (Codex) gets Perro's reply format as
one, and a reply that isn't JSON at all is asked about again instead of dropped."""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, ROOT)
os.environ["PERRO_CLI_HOME"] = tempfile.mkdtemp(prefix="clihome-")
from perro import cli_model, heartbeat, inbox, tools
from perro.memory import Memory
from perro.model import Reply

FORM = heartbeat.REPLY_FORM


def strict(schema, where="form"):
    """OpenAI's strict mode, which Codex uses: every object closed, every field required."""
    types = schema.get("type")
    types = types if isinstance(types, list) else [types]
    if "object" in types:
        assert schema.get("additionalProperties") is False, where
        assert sorted(schema["required"]) == sorted(schema["properties"]), where
        for name, sub in schema["properties"].items():
            strict(sub, f"{where}.{name}")
    if "array" in types:
        strict(schema["items"], f"{where}[]")


strict(FORM)
assert FORM["properties"]["tool"]["enum"] == [*tools.TOOLS, None]
assert {"message", "notes", "next_wake_minutes"} <= set(FORM["properties"])

# What the form makes the model write comes back in the usual shape.
request = {"thought": "Look.", "tool": "read_file", "args": '{"path": "notes.txt"}',
           "message": None, "notes": None, "next_wake_minutes": None}
decision, dropped = heartbeat.parse_reply(json.dumps(request))
assert decision["args"] == {"path": "notes.txt"} and "next_wake_minutes" not in decision and not dropped
decision, _ = heartbeat.parse_reply(json.dumps({**request, "args": ""}))
assert decision["args"] == {}
final = {"thought": "Done.", "tool": None, "args": None, "message": "Hi.", "notes": None, "next_wake_minutes": 90}
decision, _ = heartbeat.parse_reply(json.dumps(final))
assert "tool" not in decision and "args" not in decision and decision["next_wake_minutes"] == 90
# Args that aren't a JSON object stay as they are, and the tool says so.
decision, _ = heartbeat.parse_reply(json.dumps({**request, "args": "notes.txt"}))
assert decision["args"] == "notes.txt"
home = Path(tempfile.mkdtemp())
m = Memory(home)
assert "must be a JSON object" in tools.run(m, "read_file", decision["args"])
# A plain reply, as DeepSeek writes it, is unchanged.
plain = {"thought": "x", "tool": "list_files", "args": {"path": "."}}
assert heartbeat.parse_reply(json.dumps(plain))[0] == plain


class Fake:
    def __init__(self, replies):
        self.replies, self.seen, self.modes = list(replies), [], []

    def complete(self, messages, temperature=0.7, json_mode=False):
        self.seen.append([dict(x) for x in messages])
        self.modes.append(json_mode)
        r = self.replies.pop(0)
        return Reply(r if isinstance(r, str) else json.dumps(r), "fake", 100, 10)


prompt = heartbeat.system_prompt("Perro")
sent = lambda: [e.content for e in m.recent(200) if e.kind == "tool" and e.content.startswith("message_owner")]

# The beat hands the client the form, and a form reply works end to end.
inbox.owner_sends(m, "Read my notes file?")
(home / "workspace").mkdir(exist_ok=True)
(home / "workspace" / "notes.txt").write_text("Buy milk.", encoding="utf-8")
f = Fake([request, {**final, "message": "It says: buy milk."}])
assert heartbeat.beat(m, f, prompt) == 90
assert f.modes == [FORM, FORM]
assert "Buy milk." in f.seen[1][-1]["content"]
assert "It says: buy milk." in sent()[-1] and "-> Sent" in sent()[-1]
assert not [e for e in m.recent(30) if e.kind == "error"]

# The 2026-10-18 failure: the answer written as prose. Asked again, it goes out.
inbox.owner_sends(m, "Where does Lobo's name come from?")
f = Fake(["Oh, from lobotomite. The wolf was a bonus.",
          {**final, "message": "Oh, from lobotomite. The wolf was a bonus.", "next_wake_minutes": 600}])
assert heartbeat.beat(m, f, prompt) == 600
assert len(f.seen) == 2
assert f.seen[1][-2] == {"role": "assistant", "content": "Oh, from lobotomite. The wolf was a bonus."}
assert '"message" field' in f.seen[1][-1]["content"]
assert "from lobotomite" in sent()[-1]
errors = [e.content for e in m.recent(30) if e.kind == "error"]
assert errors and "asked again in the same wake-up" in errors[-1] and "lobotomite" in errors[-1], errors

# Only once a wake: two slips in a row fail the beat as before, keeping the text.
f = Fake(["Prose.", "More prose."])
assert heartbeat.beat(m, f, prompt) == heartbeat.RETRY_MINUTES
assert "Beat failed: JSONDecodeError" in m.recent(5)[-1].content or any(
    "Beat failed: JSONDecodeError" in e.content and "More prose." in e.content for e in m.recent(5))
# An empty reply isn't asked about again (the clients already retry those).
f = Fake([""])
assert heartbeat.beat(m, f, prompt) == heartbeat.RETRY_MINUTES and len(f.seen) == 1

# The Codex backend passes the form as --output-schema, in a file it cleans up.
seen = {}
OUT = "\n".join(json.dumps(e) for e in (
    {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(final)}},
    {"type": "turn.completed", "usage": {"input_tokens": 600, "output_tokens": 5}}))


def fake_run(self, args, stdin, env_name, profile):
    seen["args"] = args
    if "--output-schema" in args:
        path = args[args.index("--output-schema") + 1]
        seen["path"], seen["schema"] = path, json.loads(Path(path).read_text(encoding="utf-8"))
    return OUT


cli_model.CliClient._run = fake_run
client = cli_model.CliClient("codex", "gpt-6-luna")
msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hello"}]
client.complete(msgs, json_mode=FORM)
assert seen["schema"] == FORM and not Path(seen["path"]).exists()
assert seen["args"][-1] == "-"
seen.clear()
client.complete(msgs, json_mode=True)
assert "--output-schema" not in seen["args"]
# Claude Code gets no schema (its --json-schema hands the model a tool; see cli_model).
cli_model.CliClient._run = lambda self, args, *a: seen.update(args=args) or json.dumps({"result": "{}"})
cli_model.CliClient("claude", "haiku").complete(msgs, json_mode=FORM)
assert "--json-schema" not in seen["args"]
print("reply form ok")
