"""Offline tests for perro/browser.py and the driver's address rules: Docker is
faked, nothing runs a browser."""
import importlib.util, json, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from perro import browser, workshop

# --- how the box runs ---------------------------------------------------------------
args = browser.container_args("perro-browser:abc")
joined = " ".join(args)
for must in ("--network perro-browser ", "--read-only", "--cap-drop ALL", "no-new-privileges",
             "--pids-limit", "--memory 2g", "PERRO_PROXY=http://perro-browser-proxy:3128"):
    assert must in joined, must
for never in ("-p", "--publish", "--mount", "-v", "--volume", "--privileged", "--cap-add", "--add-host"):
    assert never not in args, never
assert "docker.sock" not in joined and args[args.index("--network") + 1] == "perro-browser"
assert args[-1] == "perro-browser:abc"
assert browser.ALLOW == ("*",) and "*" not in workshop.ALLOW     # only the browser's proxy is open

# --- setting it up, with Docker faked -------------------------------------------------
calls = []
state = {"container": None}


def fake_docker(*a, timeout=120, check=True, stdout=None, input=None):
    calls.append((a, input))
    out, code = "", 0
    if a[0] == "inspect":
        out, code = (state["container"], 0) if state["container"] else ("", 1)
    elif a[0] == "run":
        state["container"] = next(x.split("=", 1)[1] for x in a if x.startswith("perro.hash=")) + " true"
    elif a[0] == "exec" and "call" in a:
        command = json.loads(input)
        out = json.dumps({"ok": True} if command["do"] == "ping" else {"echo": command})
    return subprocess.CompletedProcess(a, code, out, "")


browser.docker = fake_docker
proxies = []
browser.ensure_proxy = lambda *a: proxies.append(a)
browser.image = lambda: "perro-browser:abc"
browser.ensure_box()
assert proxies == [("perro-browser-proxy", "perro-browser", "perro-browser-out", ("*",))]
runs = [a for a, _ in calls if a[0] == "run"]
assert len(runs) == 1 and "--label" in runs[0]
calls.clear()
browser.ensure_box()                                   # unchanged and running: left alone
assert not any(a[0] in ("run", "rm") for a, _ in calls), calls
state["container"] = state["container"].replace(" true", " false")
calls.clear()
browser.ensure_box()                                   # stopped: started again, not remade
assert any(a[:1] == ("start",) for a, _ in calls) and not any(a[0] == "run" for a, _ in calls)

# --- talking to the driver -----------------------------------------------------------
answer = browser.call({"do": "open", "url": "https://example.com"})
assert answer == {"echo": {"do": "open", "url": "https://example.com"}}, answer
exec_call = next(a for a, _ in calls if a[0] == "exec")
assert exec_call[:3] == ("exec", "-i", "perro-browser")


def gone(*a, **k):
    return subprocess.CompletedProcess(a, 1, "", "Error response from daemon: No such container: perro-browser")


browser.docker = gone
assert browser.call({"do": "ping"}) == {"error": "The browser box isn't running."}
browser.docker = lambda *a, **k: subprocess.CompletedProcess(a, 0, "not json\n", "")
assert "couldn't be read" in browser.call({"do": "ping"})["error"]


def slow(*a, **k):
    raise subprocess.TimeoutExpired("docker", 1)


browser.docker = slow
assert "in time" in browser.call({"do": "ping"})["error"]

# --- the driver's address rules (its own module; Playwright is only needed to serve) --
spec = importlib.util.spec_from_file_location("driver", ROOT / "browser" / "driver.py")
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)
assert driver.check_url("https://example.com/a?b=1") == ("https://example.com/a?b=1", "")
assert driver.check_url("example.com") == ("https://example.com", "")
url, note = driver.check_url("http://example.com/x")
assert url == "https://example.com/x" and "https" in note
for bad in ("", "file:///etc/passwd", "ftp://example.com/", "javascript:alert(1)", "data:text/html,hi",
            "https://user:pw@example.com/", "https://user@example.com/", "https://example.com:8080/",
            "https:///nohost", "https://example.com/" + "a" * 2001, "chrome://settings"):
    try:
        driver.check_url(bad)
    except ValueError:
        pass
    else:
        raise AssertionError(f"accepted {bad!r}")
assert "private or local" in driver.describe(Exception("net::ERR_TUNNEL_CONNECTION_FAILED at https://x"))
assert driver.ALLOWED_METHODS == {"GET", "HEAD"}
print("browser tests passed")
