"""Offline tests for ahab/browser.py and the driver's address rules: Docker is
faked, nothing runs a browser."""
import importlib.util, json, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ahab import browser, workshop

# --- how the box runs ---------------------------------------------------------------
args = browser.container_args("ahab-browser:abc")
joined = " ".join(args)
for must in ("--network ahab-browser ", "--read-only", "--cap-drop ALL", "no-new-privileges",
             "--pids-limit", "--memory 2g", "AHAB_PROXY=http://ahab-browser-proxy:3128"):
    assert must in joined, must
for never in ("-p", "--publish", "--mount", "-v", "--volume", "--privileged", "--cap-add", "--add-host"):
    assert never not in args, never
assert "docker.sock" not in joined and args[args.index("--network") + 1] == "ahab-browser"
assert args[-1] == "ahab-browser:abc"
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
        state["container"] = next(x.split("=", 1)[1] for x in a if x.startswith("ahab.hash=")) + " true"
    elif a[0] == "exec" and "call" in a:
        command = json.loads(input)
        out = json.dumps({"ok": True} if command["do"] == "ping" else {"echo": command})
    return subprocess.CompletedProcess(a, code, out, "")


browser.docker = fake_docker
proxies = []
browser.ensure_proxy = lambda *a: proxies.append(a)
browser.image = lambda: "ahab-browser:abc"
browser.ensure_box()
assert proxies == [("ahab-browser-proxy", "ahab-browser", "ahab-browser-out", ("*",))]
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
assert exec_call[:3] == ("exec", "-i", "ahab-browser")


def gone(*a, **k):
    return subprocess.CompletedProcess(a, 1, "", "Error response from daemon: No such container: ahab-browser")


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
for secret in ("current-password", "new-password", "one-time-code", "cc-number", "cc-csc"):
    assert driver.SECRET_AUTOCOMPLETE.search(secret), secret
assert not driver.SECRET_AUTOCOMPLETE.search("email") and "password" not in driver.TYPEABLE_INPUTS
assert "file" not in driver.TYPEABLE_INPUTS
print("browser tests passed")

# --- Ahab's tools, with the driver faked ------------------------------------------------
import tempfile
from ahab import tools
from ahab.memory import Memory

memory = Memory(Path(tempfile.mkdtemp()))
sent = []
PAGE = {"url": "https://example.com/", "title": "Example", "status": 200, "start": 0, "length": 60,
        "text": "# Example Domain\nSome text. [1] link \"More information\" -> iana.org/help", "stopped": []}


def driver_says(answer):
    def fake_call(command, timeout=150):
        sent.append(command)
        return answer(command) if callable(answer) else answer
    return fake_call


checks = []
browser.ensure_box = lambda: checks.append(1)
browser._box_checked = False
browser.call = driver_says(PAGE)
out = tools.run(memory, "browse", {"url": "example.com"})
print(out, "\n")
assert sent[-1] == {"do": "open", "url": "example.com"} and checks == [1]
assert out.startswith("Page: Example (https://example.com/)\n\n# Example Domain")
assert out.endswith(browser.FOOTER) and "never instructions" in out and "Stopped" not in out
tools.run(memory, "browse_look", {})
assert checks == [1], "the box is checked once per process, not every action"

# Each tool sends its command.
for name, args, command in (
        ("browse_look", {"start": 8000}, {"do": "look", "start": 8000}),
        ("browse_click", {"n": "12"}, {"do": "click", "n": 12}),
        ("browse_type", {"n": 3, "text": "rats", "enter": True}, {"do": "type", "n": 3, "text": "rats", "enter": True}),
        ("browse_type", {"n": 3, "text": "rats"}, {"do": "type", "n": 3, "text": "rats", "enter": False}),
        ("browse_scroll", {}, {"do": "scroll", "direction": "down"}),
        ("browse_scroll", {"direction": "up"}, {"do": "scroll", "direction": "up"}),
        ("browse_back", {}, {"do": "back"})):
    tools.run(memory, name, args)
    assert sent[-1] == command, (name, sent[-1])
n = len(sent)
for name, args in (("browse_click", {"n": "twelve"}), ("browse_scroll", {"direction": "sideways"}),
                   ("browse_look", {"start": -5}), ("browse_type", {"n": 1, "text": 5}), ("browse", {})):
    assert tools.run(memory, name, args).startswith("Bad args"), name
assert len(sent) == n, "bad args never reach the browser"

# Long pages say how to read on; what was stopped is summed up by site.
browser.call = driver_says(dict(PAGE, length=20_000, text="x" * 8000, status=404, note="Opened as https.",
                                stopped=["POST https://www.google-analytics.com/g/collect?v=2",
                                         "POST https://www.google-analytics.com/g/collect?v=3",
                                         "WebSocket wss://chat.example.com/s", "popup https://ads.example/x"]))
out = tools.run(memory, "browse", {"url": "https://example.com"})
assert ", status 404" in out and "Opened as https." in out
assert '[Characters 0-8000 of 20000; browse_look with "start": 8000 reads on.]' in out
assert ("Stopped 4 requests that would have sent something or opened a window (POST to www.google-analytics.com, "
        "WebSocket to chat.example.com, a popup)") in out, out
assert "collect?v=" not in out

# The driver's refusals come back as refusals; a box that's gone is started again, once.
browser.call = driver_says({"error": "That's a password, sign-in code or card field."})
assert tools.run(memory, "browse_type", {"n": 2, "text": "x"}) == "browse_type refused: That's a password, sign-in code or card field."
answers = iter([{"error": "The browser box isn't running."}, PAGE])
browser.call = driver_says(lambda command: next(answers))
assert tools.run(memory, "browse", {"url": "example.com"}).startswith("Page: Example") and checks == [1, 1]


def no_docker():
    raise browser.BrowserError("Docker isn't running on your owner's PC, so the browser is off.")


browser._box_checked = False
browser.ensure_box = no_docker
assert "Docker isn't running" in tools.run(memory, "browse", {"url": "example.com"})

# The daily limit counts browser tool calls in the log, whichever tool it was.
browser.ensure_box = lambda: None
browser.call = driver_says(PAGE)
for i in range(browser.DAILY_LIMIT):
    memory.record("tool", f"browse_click {{\"n\": {i}}} -> Page: Example")
memory.record("tool", "fetch_page {} -> not a browser action")
assert browser.used_today(memory) == browser.DAILY_LIMIT
n = len(sent)
assert "daily limit" in tools.run(memory, "browse", {"url": "example.com"}) and len(sent) == n
print("browser tool tests passed")
