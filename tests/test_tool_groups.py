"""Tool grouping: the core in full, other groups one line each until used or asked for,
groups this machine can't run named once, and every tool still callable."""
import json, os, sys, tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ahab import heartbeat, tools
from ahab.memory import Memory
from ahab.model import Reply


class Fake:
    def __init__(self, replies): self.replies, self.seen = list(replies), []
    def complete(self, messages, temperature=0.7, json_mode=False):
        self.seen.append([dict(m) for m in messages])
        return Reply(json.dumps(self.replies.pop(0)), "fake", 100, 10)


os.environ.pop("AHAB_N8N_KEY", None)
home = Path(tempfile.mkdtemp())
(home / "workspace").mkdir()
m = Memory(home)

# Every grouped tool exists and sits in exactly one group; the core is what's left.
grouped = [n for _, names, _ in tools.GROUPS.values() for n in names]
assert len(grouped) == len(set(grouped)) and set(grouped) <= set(tools.TOOLS)
core = [n for n in tools.TOOLS if n not in grouped]
for n in ("message_owner", "read_file", "edit_file", "recall", "web_search", "fetch_page", "tool_help"):
    assert n in core, n

# A fresh home with no n8n key: the core in full, the rest one line a group or named as missing.
listed = tools.describe(m)
assert len(listed) < len(tools.describe()) / 3, (len(listed), len(tools.describe()))
assert "- read_file: " in listed and "- browser, a real browser" in listed and "- browse: " not in listed
assert "Not set up on this machine yet" in listed and "notion" in listed.split("Not set up")[1]
assert "- search_notion: " not in listed and "search_notion" not in listed.split("Not set up")[0]
# With the key, the n8n groups appear (short), since the machine can now run them.
os.environ["AHAB_N8N_KEY"] = "test"
listed = tools.describe(m)
assert "- notion, Notion: search_notion" in listed and "- email, your email: search_email" in listed
assert "notion" not in listed.split("Not set up")[-1].split(":")[-1].split(", ")
# A group used this week is shown in full; one used longer ago isn't.
m.record("tool", "vault_search {} -> Found 0 notes.")
listed = tools.describe(m)
assert "- vault_search: " in listed and "- vault_move: " in listed and "- vault, the Obsidian" not in listed
m.db.execute("UPDATE events SET time = ? WHERE kind = 'tool'",
              ((datetime.now(timezone.utc) - timedelta(days=9)).isoformat(timespec="seconds"),))
assert "- vault_search: " not in tools.describe(m)

# tool_help gives a group's instructions, or one tool's; unknown names say what exists.
helped = tools.run(m, "tool_help", {"group": "Browser"})
assert helped.startswith(tools.HELP_MARK) and "- browse_click: " in helped, helped[:200]
assert "- send_email: " in tools.run(m, "tool_help", {"tool": "send_email"})
assert "notion" in tools.run(m, "tool_help", {"group": "nope"})
os.environ.pop("AHAB_N8N_KEY")
assert "Not set up on this machine yet" in tools.run(m, "tool_help", {"group": "notion"})
# A grouped tool works without tool_help, and bad args bring its instructions along.
bad = tools.run(m, "vault_read", {"nonsense": 1})
assert bad.startswith("Bad args for vault_read") and "- " not in bad.split("\n")[0], bad
assert f"{tools.HELP_MARK} vault_read: " in bad

# In a wake: the page carries the short list, and a tool_help result is never trimmed.
f = Fake([{"thought": "Need the browser.", "tool": "tool_help", "args": {"group": "browser"}},
          {"thought": "Read.", "tool": "list_files", "args": {}},
          {"thought": "Done.", "message": None, "notes": None, "next_wake_minutes": 60}])
heartbeat.beat(m, f, heartbeat.system_prompt("Pip"))
system = f.seen[0][0]["content"]
assert heartbeat.TOOL_LIST not in system and "- browser, a real browser" in system
last = f.seen[2]
kept = [x["content"] for x in last if x["role"] == "user" and x["content"].startswith("Tool result:\nHow to use")]
assert kept and "trimmed" not in kept[0] and "- browse_screenshot: " in kept[0], kept
assert "tool_help" in heartbeat.REPLY_FORM["properties"]["tool"]["enum"]

m.close()
print("tool groups ok")
