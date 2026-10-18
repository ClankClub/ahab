"""The workshop's only way out: an HTTPS proxy that lets through a short list
of sites and refuses everything else.

The builder container sits on a Docker network with no route outside.
This proxy is on that network and on the normal one, so the builder can
reach the internet only by asking it, and it says yes only to the hosts in
ALLOW (Codex's own service and the npm registry, by default). It can't read
the traffic (it's HTTPS, passed through as is); it decides on the host name
alone. Every decision is printed, so `docker logs perro-workshop-proxy`
shows what a build tried to reach.

It refuses plain HTTP, ports other than 443, IP addresses given directly,
and any allowed name that turns out to point at a private address (so a
trick DNS answer can't make it a door into the owner's own network, where
n8n lives).

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
# "example.com" allows that host only; ".example.com" also its subdomains.
ALLOW = [h.strip().lower() for h in os.environ.get("ALLOW", "").split(",") if h.strip()]
IDLE_SECONDS = 300


def log(**fields) -> None:
    print(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), **fields}), flush=True)


def allowed(host: str) -> bool:
    return any(host == rule or (rule.startswith(".") and (host.endswith(rule) or host == rule[1:]))
               for rule in ALLOW)


async def public_address(host: str) -> str | None:
    """The host's first address, or None if any of them is private."""
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return None
    addresses = [info[4][0] for info in infos]
    if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
        return None
    return addresses[0]


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
    parts = line.split()

    def refuse(reason: str, target: str) -> None:
        log(refused=target, reason=reason)
        client_w.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
        client_w.close()

    if len(parts) != 3 or parts[0] != "CONNECT":
        return refuse("only HTTPS (CONNECT) is allowed", line[:200])
    host, _, port = parts[1].rpartition(":")
    host = host.strip("[]").lower()
    if port != "443":
        return refuse("only port 443", parts[1])
    try:
        ipaddress.ip_address(host)
        return refuse("addresses must be names", host)
    except ValueError:
        pass
    if not allowed(host):
        return refuse("not on the allowlist", host)
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
