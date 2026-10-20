import sys, tempfile
from pathlib import Path
ROOT = str(Path(__file__).resolve().parent.parent)
from pathlib import Path
sys.path.insert(0, ROOT)
from ahab import identity, heartbeat
from ahab.memory import Memory
m = Memory(Path(tempfile.mkdtemp())); identity.seed(m, "Ahab")
ws = identity.workspace_root(m)
# FEEDBACK.md is seeded too, but it's for the builders: never on the page.
assert (ws / "FEEDBACK.md").read_text(encoding="utf-8").startswith("# FEEDBACK.md")
assert "FEEDBACK.md: problems" not in identity.describe(m) and "FEEDBACK.md" in heartbeat.SYSTEM_PROMPT
(ws / "FEEDBACK.md").write_text("mine", encoding="utf-8"); identity.seed(m, "Ahab")
assert (ws / "FEEDBACK.md").read_text(encoding="utf-8") == "mine"
(ws / "AGENTS.md").write_text("a" * 9000, encoding="utf-8")
(ws / "SOUL.md").write_text("s" * 9000, encoding="utf-8")
page = identity.describe(m)
assert "a" * 9000 in page and "[cut: AGENTS.md" not in page
assert "[cut: SOUL.md is 9000 characters, over the 8000 limit" in page
(ws / "AGENTS.md").write_text("a" * 10_001, encoding="utf-8")
assert "[cut: AGENTS.md is 10001 characters, over the 10000 limit" in identity.describe(m)
sysp = heartbeat.system_prompt("Ahab")
assert "Keep each file under 8000 characters (AGENTS.md under 10000, MEMORY.md under 20000)." in sysp, sysp[:200]
print("caps ok")
