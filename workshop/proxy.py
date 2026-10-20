"""The only way out for Ahab's boxes: an HTTPS proxy that decides, host by
host, what a box may reach.

A box (the workshop's builder, or the browser) sits on a Docker network with
no route outside. This proxy is on that network and on the normal one, so the
box can reach the internet only by asking it. It can't read the traffic (it's
HTTPS, passed through as is); it decides on the host name alone. Every
decision is printed, so `docker logs <proxy>` shows what a box tried to reach.

ALLOW says which names may pass:
  - "example.com" allows that host only; ".example.com" also its subdomains.
    The workshop runs this way, with a short list (Codex's service and npm).
  - "*" allows any name at all. The browser runs this way: it has to reach
    whatever public site it's asked for.

Whatever ALLOW says, it refuses plain HTTP, ports other than 443, IP
addresses given directly, and any name that turns out to point at a private
address. That last check is what keeps "*" from being a door into the
owner's own network (the PC, the LAN, n8n, Docker's other containers, cloud
metadata): the name is resolved here, every address it gives is checked, and
the connection goes to the checked address, so a trick DNS answer can't
change it in between.

Standard library only; runs in the stock python image.
"""

import asyncio
import ipaddress
import json
import os
import socket
import sys
import time

PORT = 3128
ALLOW = [h.strip().lower() for h in os.environ.get("ALLOW", "").split(",") if h.strip()]
IDLE_SECONDS = 300
NAT64 = ipaddress.ip_network("64:ff9b::/96")


def log(**fields) -> None:
    print(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), **fields}), flush=True)


def allowed(host: str) -> bool:
    return any(rule == "*" or host == rule or (rule.startswith(".") and (host.endswith(rule) or host == rule[1:]))
               for rule in ALLOW)


def public(address: str) -> bool:
    """Is this address on the public internet? IPv6 forms that carry an IPv4
    address inside (mapped, 6to4, Teredo, NAT64) are judged by that one."""
    ip = ipaddress.ip_address(address.split("%")[0])
    if ip.version == 6:
        inner = ip.ipv4_mapped or ip.sixtofour or (ip.teredo[1] if ip.teredo else None)
        if inner is None and ip in NAT64:
            inner = ipaddress.ip_address(int(ip) & 0xFFFFFFFF)
        if inner is not None and not inner.is_global:
            return False
    return ip.is_global


async def public_address(host: str) -> str | None:
    """The host's first address, or None if any of them is private."""
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        return None
    addresses = [info[4][0] for info in infos]
    if not addresses or not all(public(a) for a in addresses):
        return None
    return addresses[0]


def decide(line: str) -> tuple[str | None, str]:
    """Read a request's first line. Returns (host, "") to go ahead, or
    (None, why) to refuse. The address check comes after, in handle()."""
    parts = line.split()
    if len(parts) != 3 or parts[0] != "CONNECT":
        return None, "only HTTPS (CONNECT) is allowed"
    host, _, port = parts[1].rpartition(":")
    host = host.strip("[]").lower().rstrip(".")
    if port != "443":
        return None, "only port 443"
    if not host:
        return None, "no host"
    try:
        ipaddress.ip_address(host.split("%")[0])
        return None, "addresses must be names"
    except ValueError:
        pass
    if not allowed(host):
        return None, "not on the allowlist"
    return host, ""


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await asyncio.wait_for(reader.read(65536), IDLE_SECONDS):
            writer.write(data)
            await writer.drain()
    except (asyncio.TimeoutError, ConnectionError, OSError):
        pass
    finally:
        writer.close()


async def handle(client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter) -> None:
    try:
        head = await asyncio.wait_for(client_r.readuntil(b"\r\n\r\n"), 30)
    except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
        client_w.close()
        return
    line = head.split(b"\r\n", 1)[0].decode("latin-1")

    def refuse(reason: str, target: str) -> None:
        log(refused=target, reason=reason)
        client_w.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
        client_w.close()

    host, why = decide(line)
    if host is None:
        target = line.split()[1] if len(line.split()) == 3 and line.startswith("CONNECT ") else line[:200]
        return refuse(why, target)
    address = await public_address(host)
    if address is None:
        return refuse("doesn't resolve to a public address", host)
    try:
        server_r, server_w = await asyncio.wait_for(asyncio.open_connection(address, 443), 30)
    except (OSError, asyncio.TimeoutError) as e:
        log(failed=host, reason=type(e).__name__)
        client_w.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
        client_w.close()
        return
    log(allowed=host)
    client_w.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
    await client_w.drain()
    await asyncio.gather(pipe(client_r, server_w), pipe(server_r, client_w))


async def main() -> None:
    if not ALLOW:
        sys.exit("ALLOW is empty; refusing to start a proxy that lets nothing through.")
    server = await asyncio.start_server(handle, "0.0.0.0", PORT)
    log(started=PORT, allow=ALLOW)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
