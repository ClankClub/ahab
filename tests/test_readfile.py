import sys, tempfile
from pathlib import Path
ROOT = str(Path(__file__).resolve().parent.parent)
from pathlib import Path
sys.path.insert(0, ROOT)
from ahab import tools
from ahab.memory import Memory
home = Path(tempfile.mkdtemp())
m = Memory(home)
ws = home / "workspace"; ws.mkdir(exist_ok=True)
(ws / "long.txt").write_text("a" * 8000 + "b" * 5000, encoding="utf-8")
(ws / "short.txt").write_text("hello", encoding="utf-8")
r = tools.read_file(m, "long.txt")
assert r.count("a") >= 8000 and "b" not in r.split("\n")[1] and "use start=8000" in r, r[-120:]
r = tools.read_file(m, "long.txt", start=8000)
assert "b" * 5000 in r and "a" not in r.split("\n", 1)[1] and "use start" not in r, r[-120:]
r = tools.read_file(m, "long.txt", start="7990")
assert "a" * 10 + "b" * 5000 in r and "use start" not in r
assert "only 13000 chars" in tools.read_file(m, "long.txt", start=20000)
assert tools.read_file(m, "short.txt").endswith("\nhello")
assert tools.read_file(m, "short.txt", start=0).endswith("hello")
for bad in (-1, "x"):
    try: tools.read_file(m, "short.txt", start=bad); raise SystemExit(f"accepted {bad!r}")
    except tools.ToolError: pass
print("read_file ok")
