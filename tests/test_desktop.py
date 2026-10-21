"""The desktop box: offline by default, nothing of the PC mounted, files across
only as plain files inside the workspace, the tools' checks, the idle stop."""
import io
import json
import sys
import tarfile
import tempfile
import time
from pathlib import Path
ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, ROOT)
from ahab import desktop, senses, tools, workers
from ahab.memory import Memory

home = Path(tempfile.mkdtemp())
settings = lambda **kw: (home / "home.json").write_text(json.dumps({"name": "Pip", **kw}), encoding="utf-8")
settings()
m = Memory(home)
run = lambda name, **args: tools.run(m, name, args)
desktop.state_dir = lambda: (home / ".desk").mkdir(exist_ok=True) or home / ".desk"

# The box: no network unless hosts are listed, and nothing of the PC mounted.
args = desktop.container_args("img", [])
assert args[args.index("--network") + 1] == "none" and "AHAB_PROXY" not in " ".join(args)
assert "--read-only" in args and args[args.index("--cap-drop") + 1] == "ALL" and "-p" not in args
mounts = [args[i + 1] for i, a in enumerate(args) if a in ("--mount", "-v")]
assert mounts == [f"type=volume,src={desktop.VOLUME},dst=/home/pip"], mounts
args = desktop.container_args("img", ["example.com"])
assert args[args.index("--network") + 1] == desktop.NETWORK and "AHAB_HOSTS=example.com" in args

# The owner's host list: a list of plain names, nothing else.
assert desktop.hosts(home) == []
settings(desktop_hosts="example.com")
assert desktop.hosts(home) == [], "a list, not a string"
settings(desktop_hosts=["Example.com", ".wikipedia.org", "10.0.0.1", "localhost", "a b.com", "x.com:80"])
assert desktop.hosts(home) == [".wikipedia.org", "example.com"], desktop.hosts(home)
settings()

# A fake box: the driver's answers, and docker exec for the shell and tar.
sent, execs = [], []
PNG = senses.__dict__.get("_TEST_PNG") or (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
desktop._ready = lambda memory: None


def fake_call(command, timeout=0):
    sent.append(command)
    if command["do"] == "copy":
        return {"text": "copied words", "length": 12}
    if command["do"] == "click" and command.get("x") == 5000:
        return {"error": "(5000, 1) is off the screen"}
    return {"image": __import__("base64").b64encode(PNG).decode(), "cursor": [10, 20],
            "windows": [{"title": "notes.txt - Mousepad", "app": "mousepad", "x": 0, "y": 0, "w": 500,
                         "h": 400, "focused": True}], "done": f"Did {command['do']}."}


box_files = {}


class Done:
    def __init__(self, out=b"", err=b"", code=0):
        self.stdout, self.stderr, self.returncode = out, err, code


def fake_exec(argv, data=None, timeout=0):
    execs.append((argv, data))
    if "bash" in argv:
        return Done(b"hello from the box\n", b"", 0)
    if argv[-3:] == ["tar", "-xf", "-"] or "-xf" in argv:
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            for member in tar:
                box_files[member.name] = (member, tar.extractfile(member).read() if member.isfile() else b"")
        return Done()
    if "-cf" in argv:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as tar:
            for name, body in (("report/table.xlsx", b"sheet"), ("report/../../escape.txt", b"x")):
                info = tarfile.TarInfo(name)
                info.size = len(body)
                tar.addfile(info, io.BytesIO(body))
            link = tarfile.TarInfo("report/link")
            link.type, link.linkname = tarfile.SYMTYPE, "/etc/passwd"
            tar.addfile(link)
        return Done(buffer.getvalue())
    return Done(b"f 5 a.txt\nd 0 docs\n")


desktop.call = fake_call
desktop._exec = fake_exec

# Looking and acting: the picture is attached, kept outside the home, and the windows listed.
senses.set_sight(m, type("C", (), {"can_see": True})())
r = run("desktop_look")
assert "attached" in r and '"notes.txt - Mousepad" (mousepad)' in r and "focused" in r, r
shown = senses.take_shown(m)
assert shown and not Path(shown[0]["path"]).resolve().is_relative_to(home.resolve() / "workspace"), shown
r = run("desktop_act", action="click", x=100, y=200)
assert r.startswith("Did click.") and sent[-1] == {"do": "click", "x": 100, "y": 200}, r
assert "refused" in run("desktop_act", action="click", x=5000, y=1)
assert "Bad args" in run("desktop_act", action="explode")
assert "copied words" in run("desktop_act", action="copy")
r = run("desktop_look", save=True)
assert "Saved as media/" in r, r
senses.set_sight(m, type("C", (), {"can_see": False})())
assert "can't see images" in run("desktop_look") and "can't see images" in run("desktop_act", action="move", x=1, y=1)
assert "copied words" in run("desktop_act", action="copy"), "copy is text, fine without sight"
senses.set_sight(m, type("C", (), {"can_see": True})())

# The shell: text in, text out, as the box's user, with a capped time.
r = run("desktop_shell", command="echo hello", timeout=999)
assert r.startswith("exit code 0") and "hello from the box" in r, r
argv = next(a for a, _ in reversed(execs) if "bash" in a)
assert argv[argv.index("timeout") + 3] == str(desktop.SHELL_MAX_TIMEOUT) and "1000:1000" in argv, argv
assert "Bad args" in run("desktop_shell", command="  ")

# Files in: plain files only, from inside the workspace.
ws = tools.workspace_root(m)
(ws / "data").mkdir()
(ws / "data" / "rows.csv").write_text("a,b\n1,2\n", encoding="utf-8")
outside = Path(tempfile.mkdtemp()) / "secret.txt"
outside.write_text("not yours", encoding="utf-8")
try:
    (ws / "data" / "sneaky").symlink_to(outside)
except OSError:
    pass                                            # no symlink rights on this Windows; the rest still runs
r = run("desktop_files", do="put", path="data")
assert "Copied 1 file(s)" in r and list(box_files) == ["from-workspace/data/rows.csv"], (r, list(box_files))
assert "refused" in run("desktop_files", do="put", path="../x")
assert "refused" in run("desktop_files", do="get", path="/etc/passwd")
assert "refused" in run("desktop_files", do="get", path="a/../../b")

# Files out: into workspace/desktop/, never through a link or out of the workspace.
r = run("desktop_files", do="get", path="report")
assert "Brought 1 file(s)" in r and (ws / "desktop" / "report" / "table.xlsx").read_bytes() == b"sheet", r
assert not (ws / "escape.txt").exists() and not (ws / "desktop" / "report" / "link").exists()
assert "a.txt (5 bytes)" in run("desktop_files", do="list")

# Reset wipes the home volume and counts against the daily delete limit.
docker_calls = []
desktop.docker = lambda *a, **k: docker_calls.append(a) or Done()
before = tools.deleted_today(m)
assert "Wiped the desktop" in run("desktop_files", do="reset")
assert ("volume", "rm", "-f", desktop.VOLUME) in docker_calls and tools.deleted_today(m) == before + 1

# The idle stop: only when the box has sat unused long enough.
assert desktop.idle_at() is None and desktop.idle_stop() is False
desktop._touch()
assert desktop.idle_at() > time.time() and desktop.idle_stop() is False
(desktop.state_dir() / "last_used").write_text(str(time.time() - desktop.IDLE_SECONDS - 1))
desktop.running = lambda: True
assert desktop.idle_stop() is True and ("stop", "-t", "5", desktop.CONTAINER) in docker_calls
assert desktop.idle_at() is None

# Workers don't get the desktop (one shared box), and the tools are counted.
assert not {"desktop_look", "desktop_act", "desktop_shell", "desktop_files"} & set(workers.WORKER_TOOLS)
assert len(tools.TOOLS) == 86
print("desktop ok")
