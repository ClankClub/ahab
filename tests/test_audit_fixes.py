"""Three bugs from an outside audit, each held shut: desktop tar names that are
special on Windows, Telegram's two threads saving state at once, and owner
messages marked read without fitting on the page."""
import io
import sys
import tarfile
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from perro import desktop, inbox, telegram, tools
from perro.memory import Memory

home = Path(tempfile.mkdtemp())
m = Memory(home)
ws = tools.workspace_root(m)


# --- desktop: a Linux tar name can't land outside workspace/desktop/ on Windows

def archive(entries):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w") as tar:
        for name, body in entries:
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
    return out.getvalue()


(ws / "SOUL.md").write_bytes(b"original")
bad = ["report/..\\..\\SOUL.md", "report/x:stream", "report/NUL.txt", "report/com1.log",
       "report/trailing.", "report/trailing ", "report/x?y", "report/a\x01b"]
saved = desktop._tar_out(m, archive([(n, b"bad") for n in bad] + [("report/good.txt", b"good")]), "report")
assert saved == ["desktop/report/good.txt"], saved
assert (ws / "SOUL.md").read_bytes() == b"original"
assert (ws / saved[0]).read_bytes() == b"good"
assert not any(p.name.startswith("..") for p in ws.rglob("*")), list(ws.rglob("*"))


# --- telegram: two saves at once don't trip over the shared temp file

state_home = Path(tempfile.mkdtemp())
original, active, peak, errors = Path.replace, [0], [0], []
count = threading.Lock()


def slow_replace(path, target):
    with count:
        active[0] += 1
        peak[0] = max(peak[0], active[0])
    time.sleep(0.05)
    try:
        return original(path, target)
    finally:
        with count:
            active[0] -= 1


def save(n):
    try:
        telegram.save_state(state_home, {"owner_chat_id": 1}, update_offset=n)
    except Exception as e:
        errors.append(repr(e))


with patch.object(Path, "replace", slow_replace):
    threads = [threading.Thread(target=save, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
assert peak[0] == 1 and not errors, (peak, errors)
assert telegram.load_state(state_home)["owner_chat_id"] == 1

# ...and a sending side that dies takes the bridge down loudly, rather than
# leaving it taking messages in and sending nothing out.
telegram.save_state(state_home, {"owner_chat_id": 1, "owner_user_id": 1})
died = threading.Event()


def broken(*args):
    died.set()
    raise OSError("disk trouble")


class Bot:
    calls = 0

    def updates(self, offset):
        self.calls += 1
        died.wait(3)
        if self.calls > 2:
            raise KeyboardInterrupt   # the fix didn't fire; end the test instead of hanging
        return []


with patch.object(telegram.watchdog, "keep_alive"), patch.object(telegram, "forward_replies", broken):
    try:
        telegram.run(state_home, Bot())
        raise AssertionError("the bridge kept running with its sender dead")
    except RuntimeError as e:
        assert "sending side" in str(e), e


# --- inbox: only what fits on the page is marked read; the rest wait, knocked

def unread():
    return m.db.execute("SELECT COUNT(*) FROM messages WHERE sender='owner' AND read_at IS NULL").fetchone()[0]


for i in range(31):
    inbox.owner_sends(m, f"command-{i:03d}")
assert inbox.deliver(m) == 30 and unread() == 1 and inbox.knocked(m)
page = inbox.describe_recent(m)
assert "command-000" in page and "command-029" in page and "command-030" not in page
assert "1 more message(s) from your owner are waiting" in page
assert inbox.deliver(m) == 1 and unread() == 0 and not inbox.knocked(m)
assert "command-030" in inbox.describe_recent(m) and "waiting" not in inbox.describe_recent(m)

inbox.owner_sends(m, "first-long " + "a" * 9000)
inbox.owner_sends(m, "second-long " + "b" * 9000)
assert inbox.deliver(m) == 1 and unread() == 1
page = inbox.describe_recent(m)
assert "first-long" in page and "second-long" not in page and "waiting" in page
assert inbox.deliver(m) == 1 and "second-long" in inbox.describe_recent(m)

# One message at the size limit still goes in whole, on its own.
whole = "whole " + "x" * (inbox.MESSAGE_CHAR_LIMIT - 6)
inbox.owner_sends(m, whole)
inbox.owner_sends(m, "next one waits")
assert inbox.deliver(m) == 1 and whole in inbox.describe_recent(m) and unread() == 1

m.close()
print("ok")
