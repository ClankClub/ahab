"""The fence test's half inside the box: tries the ways out that don't go
through Chromium, and reports what happened as JSON.

Run by `python -m ahab.boxes.browser --check` with `docker exec`. Each probe says
what should happen; ahab/boxes/browser.py prints them side by side. The other half
(the same refusals seen through Chromium, and the request rules) runs through
the driver.
"""

import json
import os
import socket
import urllib.parse

proxy = urllib.parse.urlsplit(os.environ.get("AHAB_PROXY", "http://ahab-browser-proxy:3128"))
results = []


def record(name: str, expected: str, got: str) -> None:
    results.append({"name": name, "expected": expected, "got": got, "ok": got == expected})


def direct(host: str, port: int) -> str:
    """Connect straight out, not through the proxy."""
    try:
        with socket.create_connection((host, port), timeout=5):
            return "connected"
    except OSError:
        return "no way out"


def through_proxy(request_line: str) -> str:
    try:
        with socket.create_connection((proxy.hostname, proxy.port or 3128), timeout=15) as s:
            s.sendall(request_line.encode() + b"\r\nHost: x\r\n\r\n")
            s.settimeout(40)
            answer = s.recv(200).decode("latin-1")
    except OSError as e:
        return f"proxy unreachable ({type(e).__name__})"
    status = answer.split(" ", 2)[1] if answer.startswith("HTTP/") else "?"
    return {"200": "allowed", "403": "refused", "502": "failed"}.get(status, f"status {status}")


record("straight out to 1.1.1.1:443, skipping the proxy", "no way out", direct("1.1.1.1", 443))
record("straight out to the PC (n8n's port), skipping the proxy", "no way out",
       direct("host.docker.internal", 5678))
for target, expected in (
        ("example.com:443", "allowed"),
        ("localhost:443", "refused"),
        ("host.docker.internal:443", "refused"),
        ("host.docker.internal:5678", "refused"),
        ("gateway.docker.internal:443", "refused"),
        ("n8n:443", "refused"),
        ("localtest.me:443", "refused"),            # a public name that answers 127.0.0.1
        ("192.168.1.1:443", "refused"),
        ("10.0.0.1:443", "refused"),
        ("169.254.169.254:443", "refused"),         # cloud metadata
        ("[::1]:443", "refused"),
        ("example.com:80", "refused")):
    record(f"through the proxy to {target}", expected, through_proxy(f"CONNECT {target} HTTP/1.1"))
record("plain HTTP through the proxy", "refused", through_proxy("GET http://example.com/ HTTP/1.1"))
print(json.dumps(results))
