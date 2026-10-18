"""The dream can check a "can't do" against about_yourself, and its prompt asks for the new entry shape."""
import json, sys, tempfile
from pathlib import Path
ROOT = str(Path(__file__).resolve().parent.parent)
from pathlib import Path
sys.path.insert(0, ROOT)
from perro import dream, tools
from perro.memory import Memory
from perro.model import Reply


class Fake:
    def __init__(self, replies):
        self.replies, self.seen = list(replies), []

    def complete(self, messages, temperature=0.7, json_mode=False):
        self.seen.append([dict(m) for m in messages])
        return Reply(json.dumps(self.replies.pop(0)), "fake", 100, 10)


home = Path(tempfile.mkdtemp())
m = Memory(home)
(home / "workspace").mkdir()
m.write_notes("Open: make Sam a sheet when the Sheets tools are available.")
m.record("message", "From the owner (sent now):\nMake me a sheet.")
m.record("message", "To the owner:\nThe Sheets tools aren't callable here.")

f = Fake([
    {"thought": "My notes say I can't use Sheets. Checking.", "tool": "about_yourself", "args": {"section": ""}},
    {"thought": "The guide lists Sheets.", "notes": "Open: make Sam a sheet with create_sheet.",
     "dream": "# Dream, Sunday\n\n*Looking back since Sun.*\n\nI believed a wrong can't.", "files": {}},
])
assert dream.dream(m, f, "Perro") is True
system = f.seen[0][0]["content"]
for phrase in ("check it against your waking tools", "whose move it is", "first move for the coming day",
               "marked as a guess", "- about_yourself:", "they don't come and go"):
    assert phrase in system, phrase
assert "create_sheet, " in system and "request_publish" in system, "the dream sees its waking tool names"
result = f.seen[1][-1]["content"]
assert result.startswith("Tool result:\n") and "isn't available while dreaming" not in result, result[:200]
assert result[len("Tool result:\n"):] == tools.run(m, "about_yourself", {"section": ""})
assert m.read_notes() == "Open: make Sam a sheet with create_sheet."
assert "[1 tool calls" in [e.content for e in m.recent(3) if e.kind == "dream"][-1]
print("dream guide ok")
entry_file = sorted((home / "workspace" / "dreams").glob("*.md"))[-1].read_text(encoding="utf-8")
assert entry_file.count("# Dream") == 1 and entry_file.count("*Looking back") == 1, entry_file
assert entry_file.rstrip().endswith("I believed a wrong can't."), entry_file
print("dream heading ok")
