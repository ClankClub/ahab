"""about_yourself: sections, lookup, the live model line, and the prompt rules."""
import json, sys, tempfile
from pathlib import Path
ROOT = str(Path(__file__).resolve().parent.parent)
from datetime import datetime
from pathlib import Path

sys.path.insert(0, ROOT)
from ahab.memory import Memory
from ahab import tools
from ahab.heartbeat import SYSTEM_PROMPT, coming_days

with tempfile.TemporaryDirectory() as d:
    m = Memory(Path(d))
    top = tools.run(m, "about_yourself", {})
    assert "Sections: How you run, Your memory" in top and "# About yourself" not in top, top
    s = tools.run(m, "about_yourself", {"section": "what you can't do"})
    assert s.startswith("## What you can't do") and "email anyone" in s and "## Your mind" not in s, s[:200]
    assert tools.run(m, "about_yourself", {"section": "memory"}).startswith("## Your memory")
    assert "No section called" in tools.run(m, "about_yourself", {"section": "cooking"})
    assert "backend 'deepseek'" in tools.run(m, "about_yourself", {"section": "Your mind"}).splitlines()[-1]
    (Path(d) / "home.json").write_text(json.dumps({"backend": "codex-luna"}))
    last = tools.run(m, "about_yourself", {"section": "mind"}).splitlines()[-1]
    assert last == "This home runs you on: backend 'codex-luna', model gpt-6-luna, reasoning effort max, through Codex.", last
    m.close()

assert "about_yourself" in SYSTEM_PROMPT and "instead of guessing" in SYSTEM_PROMPT
assert "A job stays" in SYSTEM_PROMPT and 'One "not now"' in SYSTEM_PROMPT and "day of the week" in SYSTEM_PROMPT
s = coming_days(datetime(2026, 10, 18))
assert s.startswith("Coming days: Mon 19 Oct, Tue 20 Oct") and s.endswith("Sun 01 Nov") and s.count(",") == 13, s
print("ALL PASSED")
