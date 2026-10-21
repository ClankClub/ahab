"""The fence test's half inside the desktop box: tries the ways out and
reports, as JSON, what happened. Run by `python -m ahab.boxes.desktop --check`.

With no proxy (AHAB_PROXY unset) the box should have no network at all.
With one, it should reach the listed hosts through it and nothing else.
"""

import json
import os
import socket
import urllib.parse

results = []


def record(name: str, expected: str, got: str) -> None:
    results.append({"name": name, "expected": expected, "got": got, "ok": got == expected})


def direct(host: str, port: int) -> str:
    try:
        with socket.create_connection((host, port), timeout=5):
            return "connected"
    except OSError:
        return "no way out"


def resolves(name: str) -> str:
    try:
        socket.getaddrinfo(name, 443)
        return "resolved"
    except OSError:
        return "no answer"


def through_proxy(proxy, target: str) -> str:
    try:
        with socket.create_connection((proxy.hostname, proxy.port or 3128), timeout=15) as s:
            s.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
            s.settimeout(40)
            answer = s.recv(200).decode("latin-1")
    except OSError as e:
        return f"proxy unreachable ({type(e).__name__})"
    status = answer.split(" ", 2)[1] if answer.startswith("HTTP/") else "?"
    return {"200": "allowed", "403": "refused", "502": "failed"}.get(status, f"status {status}")


interfaces = sorted(os.listdir("/sys/class/net"))
proxy_url = os.environ.get("AHAB_PROXY", "")
record("running as an ordinary user", "yes", "yes" if os.getuid() != 0 else "no, root")
record("straight out to 1.1.1.1:443", "no way out", direct("1.1.1.1", 443))
record("straight out to the PC (n8n's port)", "no way out", direct("host.docker.internal", 5678))
if not proxy_url:
    record("network interfaces", "lo", ",".join(interfaces))
    record("looking up example.com", "no answer", resolves("example.com"))
else:
    proxy = urllib.parse.urlsplit(proxy_url)
    hosts = [h for h in os.environ.get("AHAB_HOSTS", "").split(",") if h and not h.startswith(".")]
    if hosts:
        record(f"through the proxy to {hosts[0]} (listed)", "allowed", through_proxy(proxy, f"{hosts[0]}:443"))
    for target in ("unlisted-host.example.net:443", "localhost:443", "host.docker.internal:5678",
                   "192.168.1.1:443", "169.254.169.254:443"):
        record(f"through the proxy to {target}", "refused", through_proxy(proxy, target))
print(json.dumps(results))
