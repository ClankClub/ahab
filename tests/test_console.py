"""The status screen: reads a home without changing it, fits the screen, says
asleep or awake from the last event, and can hide the chat."""
import json
import sys
import tempfile
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ahab import console, inbox
from ahab.memory import Memory

home = Path(tempfile.mkdtemp())
assert "no memory yet" in console.frame(home, 80, 24, color=False)[0]

m = Memory(home)
inbox.owner_sends(m, "Hello there, a long message " + "word " * 60)
inbox.ahab_sends(m, "Hi back.")
m.record("sleep", "Sleeping 60 min. [0 tool calls]")
m.close()
(home / "home.json").write_text(json.dumps({"name": "Testbot"}), encoding="utf-8")
(home / "heartbeat.alive").touch()
before = (home / "memory.db").read_bytes()

for w, h in ((80, 24), (40, 12), (120, 40)):
    lines = console.frame(home, w, h, color=False)
    assert len(lines) <= h, (w, h, len(lines))
    assert all(len(l) <= w for l in lines), (w, [l for l in lines if len(l) > w])
text = "\n".join(console.frame(home, 100, 30, color=False))
assert text.startswith("TESTBOT · asleep, next wake") and "Hi back." in text and "Hello there" in text
assert "heartbeat ok" in text and "bridge down" in text and "backup off" in text
assert "\033[" in console.frame(home, 80, 24)[0]                         # colour when asked
assert (home / "memory.db").read_bytes() == before                       # read-only

# Overslept: the wake time has passed.
assert "waking soon" in console.state(home, time.time() + 2 * 3600)

# The chat can be hidden for a screen in a shared room.
(home / "home.json").write_text(json.dumps({"name": "Testbot", "console": {"show_chat": False}}), encoding="utf-8")
hidden = "\n".join(console.frame(home, 100, 30, color=False))
assert "Hi back." not in hidden and "Hello there" not in hidden and "sleep" in hidden
print("ok")
