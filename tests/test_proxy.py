"""Offline tests for workshop/proxy.py: the rules both boxes' proxies decide by.
No network: name lookups are faked."""
import asyncio, importlib.util, os, socket, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(allow: str):
    os.environ["ALLOW"] = allow
    spec = importlib.util.spec_from_file_location(f"proxy_{len(allow)}", ROOT / "workshop" / "proxy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- the workshop's allowlist ------------------------------------------------------
shop = load("chatgpt.com,.npmjs.org")
assert shop.decide("CONNECT chatgpt.com:443 HTTP/1.1") == ("chatgpt.com", "")
assert shop.decide("CONNECT registry.npmjs.org:443 HTTP/1.1")[0] == "registry.npmjs.org"
assert shop.decide("CONNECT npmjs.org:443 HTTP/1.1")[0] == "npmjs.org"
assert shop.decide("CONNECT example.com:443 HTTP/1.1") == (None, "not on the allowlist")
assert shop.decide("CONNECT evilnpmjs.org:443 HTTP/1.1")[0] is None

# --- the browser's any-public-site mode, and what both refuse -------------------------
web = load("*")
assert web.decide("CONNECT example.com:443 HTTP/1.1") == ("example.com", "")
assert web.decide("CONNECT Example.COM.:443 HTTP/1.1") == ("example.com", "")
for line, why in (("GET http://example.com/ HTTP/1.1", "only HTTPS"),
                  ("CONNECT example.com:80 HTTP/1.1", "only port 443"),
                  ("CONNECT example.com:5678 HTTP/1.1", "only port 443"),
                  ("CONNECT 192.168.1.1:443 HTTP/1.1", "addresses must be names"),
                  ("CONNECT 8.8.8.8:443 HTTP/1.1", "addresses must be names"),
                  ("CONNECT [::1]:443 HTTP/1.1", "addresses must be names"),
                  ("CONNECT [fe80::1%eth0]:443 HTTP/1.1", "addresses must be names"),
                  ("CONNECT :443 HTTP/1.1", "no host"),
                  ("CONNECT example.com:443", "only HTTPS")):
    host, reason = web.decide(line)
    assert host is None and reason.startswith(why), (line, reason)

# Which addresses count as public: the check that keeps "*" off the owner's network.
for address in ("93.184.215.14", "2606:2800:21f:cb07:6820:80da:af6b:8b2c", "1.1.1.1"):
    assert web.public(address), address
for address in ("127.0.0.1", "10.0.0.5", "172.17.0.1", "192.168.65.254", "169.254.169.254",
                "100.64.0.1", "0.0.0.0", "::1", "fe80::1", "fd00::1", "::ffff:127.0.0.1",
                "::ffff:192.168.1.1", "2002:c0a8:0101::1", "64:ff9b::7f00:1", "64:ff9b::c0a8:101",
                "fe80::1%eth0"):
    assert not web.public(address), address


def resolving(answers):
    async def fake(host, port, type=0):
        if answers is None:
            raise socket.gaierror("no such name")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port)) for a in answers]
    return fake


async def lookups():
    loop = asyncio.get_running_loop()
    for answers, expected in ((["93.184.215.14"], "93.184.215.14"),
                              (["127.0.0.1"], None),
                              (["93.184.215.14", "192.168.1.10"], None),   # one private answer spoils it
                              ([], None), (None, None)):
        loop.getaddrinfo = resolving(answers)
        assert await web.public_address("example.com") == expected, answers

asyncio.run(lookups())
print("proxy tests passed")
