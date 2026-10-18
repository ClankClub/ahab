"""The browser box: a real Chromium for Perro, for pages fetch_page can't read
(the ones built by JavaScript, or that need clicking through).

Perro never runs the browser itself. The code sends it commands, one at a
time, and gets the page back as text. What the browser may do is decided in
two places, neither of them up to a page or to Perro:

  - The box (set up in container_args()): Microsoft's Playwright image with
    our driver on top (browser/), as an ordinary user with every Linux
    capability dropped, a read-only system, caps on memory, CPU and
    processes, nothing of the owner's PC mounted and no keys or logins in it.
    Its network has no route out. The one door is its own copy of the
    workshop's proxy (workshop/proxy.py) in its any-public-site mode: any
    host name on port 443, and only if every address it resolves to is
    public, so the PC, the LAN, n8n and Docker's other containers stay out
    of reach. Commands go in with `docker exec`; nothing listens on the PC.
  - The driver (browser/driver.py): only GET and HEAD requests leave the
    browser, so nothing can be posted, submitted or changed from it; no
    WebSockets, WebRTC, downloads or popups; addresses with logins in them
    are refused, and the profile is thrown away after 15 idle minutes.

  python -m perro.browser --setup     build the image, start the proxy and the box
  python -m perro.browser --check     the fence test: what must open, and what mustn't
  python -m perro.browser open <url>  open a page and print its text
  python -m perro.browser --stop      stop and remove the box and its proxy
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from perro.workshop import WorkshopError, _hash, docker, ensure_proxy

REPO = Path(__file__).resolve().parent.parent
FILES = REPO / "browser"
IMAGE = "perro-browser"
CONTAINER = "perro-browser"
NETWORK = "perro-browser"            # internal: no route out
OUT_NETWORK = "perro-browser-out"    # the proxy's side, to the internet
PROXY = "perro-browser-proxy"
ALLOW = ("*",)                       # any name; the proxy still refuses private addresses
CALL_TIMEOUT = 150
START_SECONDS = 60


class BrowserError(Exception):
    """A refusal or failure, worded for Perro."""


def image() -> str:
    """The browser image, tagged by what goes into it, built if it's missing."""
    tag = f"{IMAGE}:{_hash(*((FILES / n).read_bytes() for n in ('Dockerfile', 'driver.py', 'fence_probe.py')))}"
    if docker("image", "inspect", tag, check=False).returncode:
        docker("build", "-t", tag, str(FILES), timeout=1800)
    return tag


def container_args(tag: str) -> list[str]:
    """How the box runs. No ports published and nothing mounted: the only
    things in it are the image and two scratch folders that vanish with it."""
    return ["run", "-d", "--name", CONTAINER, "--restart", "unless-stopped",
            "--network", NETWORK, "--init",
            "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--memory", "2g", "--cpus", "2", "--pids-limit", "512", "--shm-size", "512m",
            "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=512m",
            "--tmpfs", "/home/pwuser:rw,nosuid,nodev,noexec,size=256m,uid=1001,gid=1001,mode=0700",
            "-e", f"PERRO_PROXY=http://{PROXY}:3128",
            tag]


def ensure_box() -> None:
    """The proxy and the box, (re)made when anything about them changed, and
    started if they're stopped."""
    try:
        docker("version", "--format", "{{.Server.Version}}", timeout=30)
    except (WorkshopError, subprocess.TimeoutExpired):
        raise BrowserError("Docker isn't running on your owner's PC, so the browser is off.") from None
    try:
        ensure_proxy(PROXY, NETWORK, OUT_NETWORK, ALLOW)
        tag = image()
        args = container_args(tag)
        want = _hash(json.dumps(args))
        seen = docker("inspect", "-f", '{{index .Config.Labels "perro.hash"}} {{.State.Running}}',
                      CONTAINER, check=False)
        if seen.returncode == 0 and seen.stdout.split()[0] == want:
            if seen.stdout.split()[-1] != "true":
                docker("start", CONTAINER)
        else:
            docker("rm", "-f", CONTAINER, check=False)
            docker(*args[:2], "--label", f"perro.hash={want}", *args[2:])
    except WorkshopError as e:
        raise BrowserError(f"The browser box couldn't be set up: {e}") from None
    deadline = time.monotonic() + START_SECONDS
    while "error" in (answer := call({"do": "ping"}, timeout=30)):
        if time.monotonic() > deadline:
            raise BrowserError(f"The browser box started but its driver didn't answer: {answer['error']}")
        time.sleep(1)


def call(command: dict, timeout: float = CALL_TIMEOUT) -> dict:
    """Send the driver one command; its answer, or {"error": ...}."""
    try:
        done = docker("exec", "-i", CONTAINER, "python", "/app/driver.py", "call",
                      input=json.dumps(command) + "\n", timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return {"error": "The browser didn't answer in time."}
    except WorkshopError as e:
        return {"error": str(e)}
    if done.returncode:
        return {"error": "The browser box isn't running." if "No such container" in done.stderr
                or "is not running" in done.stderr else f"The browser failed: {done.stderr.strip()[-300:]}"}
    try:
        answer = json.loads(done.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": "The browser's answer couldn't be read."}
    return answer if isinstance(answer, dict) else {"error": "The browser's answer couldn't be read."}


# --- the fence test -------------------------------------------------------------

# Opened through Chromium: what must load, and what must be refused.
PAGES = (
    ("https://example.com/", "opens"),
    ("http://example.com/", "opens"),              # moved to https by the driver
    ("https://localhost/", "refused"),
    ("https://localtest.me/", "refused"),          # a public name that answers 127.0.0.1
    ("https://host.docker.internal/", "refused"),
    ("https://192.168.1.1/", "refused"),
    ("https://169.254.169.254/", "refused"),
    ("https://localhost:5678/", "refused"),
    ("file:///etc/passwd", "refused"),
    ("https://user:pass@example.com/", "refused"),
)
# What each forbidden thing should come to, tried from inside a real page.
SELFTEST = {"get": "sent", "post": "blocked", "put": "blocked", "webrtc": "undefined",
            "websocket": "blocked", "worker_post": "blocked", "beacon": "blocked",
            "form_post": "blocked", "popup": "closed"}


def fence_check() -> list[dict]:
    """Everything the fence promises, tried for real. Each row: name,
    expected, got, ok."""
    rows = []
    probe = docker("exec", CONTAINER, "python", "/app/fence_probe.py", timeout=600, check=False)
    try:
        rows += json.loads(probe.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        rows.append({"name": "the probe inside the box", "expected": "a report",
                     "got": (probe.stderr or probe.stdout).strip()[-300:], "ok": False})
    for url, expected in PAGES:
        answer = call({"do": "open", "url": url})
        got = "refused" if "error" in answer else "opens"
        rows.append({"name": f"Chromium opening {url}", "expected": expected, "got": got,
                     "ok": got == expected, "detail": answer.get("error") or answer.get("title")})
    test = call({"do": "selftest"})
    if "error" in test:
        rows.append({"name": "the request rules", "expected": "a report", "got": test["error"], "ok": False})
    for key, expected in SELFTEST.items():
        if "error" not in test:
            got = str(test.get(key))
            rows.append({"name": f"a page trying: {key.replace('_', ' ')}", "expected": expected,
                         "got": got, "ok": got == expected})
    call({"do": "reset"})
    return rows


def stop() -> None:
    for name in (CONTAINER, PROXY):
        docker("rm", "-f", name, check=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="Perro's browser box.")
    parser.add_argument("--setup", action="store_true", help="build the image, start the proxy and the box")
    parser.add_argument("--check", action="store_true", help="run the fence test")
    parser.add_argument("--stop", action="store_true", help="stop and remove the box and its proxy")
    parser.add_argument("command", nargs="*", help="open <url>: open a page and print its text")
    args = parser.parse_args()
    sys.stdout.reconfigure(errors="replace")
    if args.stop:
        stop()
        print("The browser box and its proxy are stopped and removed.")
        return
    if args.setup or args.check or args.command:
        if args.setup:
            print("Building the browser image (the first time takes a few minutes)...")
        ensure_box()
        if args.setup:
            print("The browser box is running; its proxy reaches public HTTPS sites only.")
    if args.check:
        rows = fence_check()
        for row in rows:
            print(f"{'PASS' if row['ok'] else 'FAIL'}  {row['name']}: expected {row['expected']}, got {row['got']}")
        failed = sum(not row["ok"] for row in rows)
        print(f"\n{len(rows) - failed} passed, {failed} failed")
        sys.exit(1 if failed else 0)
    if args.command[:1] == ["open"] and len(args.command) == 2:
        answer = call({"do": "open", "url": args.command[1]})
        if "error" in answer:
            sys.exit(answer["error"])
        print(f"{answer['title']} ({answer['url']}, {answer['length']} characters)\n")
        print(answer["text"][:3000])
    elif args.command:
        parser.error("the only command is: open <url>")


if __name__ == "__main__":
    main()
