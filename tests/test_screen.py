"""Window screenshots: refused unless the owner allows the app; the PNG written by hand is a real PNG."""
import json
import struct
import sys
import tempfile
import zlib
from pathlib import Path
ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, ROOT)
from ahab import senses, tools, workers
from ahab.boxes import screen
from ahab.memory import Memory

home = Path(tempfile.mkdtemp())
settings = lambda **kw: (home / "home.json").write_text(json.dumps({"name": "Pip", **kw}), encoding="utf-8")
settings()
m = Memory(home)
shot = lambda app: tools.run(m, "screenshot_window", {"app": app})

# Nothing is allowed until the owner lists an app; then only that app, by program name.
assert "No app is allowed yet" in shot("Obsidian") and "refused" in shot("Obsidian")
settings(screen_apps="Obsidian")
assert "No app is allowed yet" in shot("Obsidian"), "a list, not a string"
settings(screen_apps=["Obsidian.exe", " Notepad "])
assert screen.allowed(m) == ["notepad", "obsidian"]
assert "isn't one of the apps your owner allows: notepad, obsidian" in shot("chrome")

# Allowed apps, with Windows faked: a match by program name only, the biggest window,
# never a minimized one, and the picture saved and shown.
screen.sys = type("S", (), {"platform": "win32"})
screen._windows = lambda: [(1, "obsidian", "Vault - Obsidian", 100, False), (2, "obsidian", "Settings", 50, False),
                           (3, "notepad", "notes.txt", 10, True), (4, "chrome", "Obsidian - fake title", 999, False)]
grabbed = []
screen._capture = lambda hwnd: grabbed.append(hwnd) or (2, 1, b"\x00\x00\xff\x00\xff\x00\x00\x00")
senses.set_sight(m, type("C", (), {"can_see": True})())
r = shot("obsidian")
assert grabbed == [1] and 'window "Vault - Obsidian" (2x1)' in r and "attached to this result" in r, r
rel = senses.take_shown(m)[0]["name"]
assert rel.startswith("media/") and rel.endswith(".png")
assert "is minimized" in shot("Notepad")
settings(screen_apps=["paint"])
assert "isn't open" in shot("paint")

# The PNG: signature, header, and pixels that decode back (red, then blue).
data = (home / "workspace" / rel).read_bytes()
assert data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR"
assert struct.unpack(">II", data[16:24]) == (2, 1)
idat = data.index(b"IDAT")
length = struct.unpack(">I", data[idat - 4:idat])[0]
assert zlib.decompress(data[idat + 4:idat + 4 + length]) == b"\x00\xff\x00\x00\x00\x00\xff"

# Halving keeps every other pixel of every other row.
w, h, px = screen.halve(4, 2, bytes(range(24)))
assert (w, h, px) == (2, 1, bytes([0, 1, 2, 6, 7, 8]))

assert "screenshot_window" not in workers.WORKER_TOOLS
print("screen ok")
