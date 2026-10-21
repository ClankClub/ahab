"""The host shell: off until home.json names its user, commands and files go
through `sudo -u <user>` only, output is read in bounded pieces, and files
cross only into ~/inbox/ and back into workspace/shell/."""
import json
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ahab import hostshell, tools, workers
from ahab.memory import Memory

home = Path(tempfile.mkdtemp())
m = Memory(home)
run = lambda name, **args: tools.run(m, name, args)
settings = lambda conf: (home / "home.json").write_text(json.dumps({"name": "Pip", **conf}), encoding="utf-8")

# Off by default, and never as root or as the service user itself.
settings({})
assert "You don't have a shell" in run("shell", command="ls")
for bad in ("root", "ahab", "Pip; rm", "", 5):
    settings({"host_shell": {"user": bad}})
    assert "You don't have a shell" in run("shell", command="ls"), bad
assert "shell" not in workers.WORKER_TOOLS and "shell_files" not in workers.WORKER_TOOLS

# A stand-in for `sudo -n -H -u pip -- ...`: a Python script that plays the
# other user, with its own home folder.
box = Path(tempfile.mkdtemp())
FAKE = r'''
import os, sys, pathlib
home = pathlib.Path(sys.argv[1]); argv = sys.argv[2:]
if argv[0] == "timeout":                    # timeout -k 5 N bash -lc "cd ~ && CMD"
    cmd = argv[-1].removeprefix("cd ~ && ")
    if cmd == "flood":
        out = sys.stdout.buffer
        for _ in range(300):
            out.write(b"y" * 10000 + b"\n")
    elif cmd == "fail":
        print("no such thing"); sys.exit(3)
    elif cmd == "sudo-missing":
        print("sudo: a password is required"); sys.exit(1)
    else:
        print("ran:", cmd, "| timeout", argv[3])
elif argv[:2] == ["sh", "-c"] and "cat >" in argv[2]:
    target = home / argv[4]; target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(sys.stdin.buffer.read())
elif argv[:2] == ["sh", "-c"]:
    p = home / argv[4]
    if not p.is_file():
        print("cat: no such file"); sys.exit(1)
    sys.stdout.buffer.write(p.read_bytes())
'''
seen = []


def fake_as(name, *argv):
    seen.append((name, argv))
    return [sys.executable, "-c", FAKE, str(box), *argv]


hostshell._as = fake_as
settings({"host_shell": {"user": "pip"}})

out = run("shell", command="uname -a", timeout=5000)
assert out.startswith("exit code 0") and "ran: uname -a | timeout 600" in out, out   # capped at 600
assert seen[-1][0] == "pip" and seen[-1][1][:4] == ("timeout", "-k", "5", "600")
assert run("shell", command="fail").startswith("exit code 3")
flood = run("shell", command="flood")
assert "the rest was dropped" in flood and len(flood) < hostshell.OUTPUT_LIMIT + 200, len(flood)
assert "sudo rule is missing" in run("shell", command="sudo-missing")

# Files: in to ~/inbox/, out to workspace/shell/, never outside either.
ws = tools.workspace_root(m)
(ws / "notes.txt").write_text("hello shell", encoding="utf-8")
assert run("shell_files", action="put", path="notes.txt") == "Copied notes.txt to ~/inbox/notes.txt in your shell."
assert (box / "inbox" / "notes.txt").read_text() == "hello shell"
(box / "out").mkdir()
(box / "out" / "result.csv").write_text("a,b\n1,2\n")
assert run("shell_files", action="get", path="out/result.csv").startswith("Copied ~/out/result.csv to shell/out/result.csv")
assert (ws / "shell" / "out" / "result.csv").read_text() == "a,b\n1,2\n"
for bad in ("../ahab/home/memory.db", "/etc/passwd", "", "~/../x"):
    assert "relative to its home" in run("shell_files", action="get", path=bad), bad
assert "isn't a file" in run("shell_files", action="put", path="nothing-here.txt")
assert "Couldn't read" in run("shell_files", action="get", path="missing.txt")

# The daily brake.
hostshell.DAILY_LIMIT = len(seen)
assert "the limit" in run("shell", command="ls")

m.close()
print("ok")
