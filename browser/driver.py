"""The browser box's driver: one Chromium, held open by Playwright, that
Perro's code sends commands to.

Runs inside the perro-browser container (perro/browser.py starts it there).
`driver.py serve` is the container's main process: it listens on a Unix
socket inside the box, so nothing is reachable from outside it, and starts
Chromium when the first command comes. `driver.py call` is how a command gets
in: perro/browser.py runs it with `docker exec`, sends one JSON line on its
standard input and reads one back.

What the browser may do is set here, whatever a page asks for:
  - Every connection goes through the box's proxy (the only way out of the
    container anyway), which reaches public HTTPS sites and nothing private.
  - Only GET and HEAD requests go out. Anything that would create, post or
    change something (a form, a script's POST, a beacon) is stopped here and
    listed, so a page can't be made to send things on Perro's behalf.
  - No WebSockets, no WebRTC, no service workers, no downloads, no popups
    (they're closed and listed), and dialogs are dismissed.
  - No logins: addresses with a user name in them are refused, and the whole
    profile (cookies, storage) is thrown away after IDLE_SECONDS idle or on
    "reset".
  - Pages are read as text, TEXT_LIMIT characters at a time.

Commands: {"do": "open", "url": ...}, {"do": "text", "start": n},
{"do": "state"}, {"do": "blocked"}, {"do": "reset"}, {"do": "ping"},
{"do": "selftest"} (checks the rules above on a real page).
"""

import asyncio
import json
import os
import re
import sys
import time
import urllib.parse

SOCKET = "/tmp/perro-driver.sock"
PROXY = os.environ.get("PERRO_PROXY", "http://perro-browser-proxy:3128")
IDLE_SECONDS = 15 * 60
NAV_TIMEOUT_MS = 30_000
SETTLE_MS = 5_000
TEXT_LIMIT = 8000
URL_LIMIT = 2000
CALL_TIMEOUT = 120
ALLOWED_METHODS = {"GET", "HEAD"}
BLOCKED_KEPT = 50
CHROMIUM_ARGS = [
    f"--proxy-server={PROXY}",
    "--proxy-bypass-list=<-loopback>",          # even localhost goes to the proxy, which refuses it
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--disable-quic",
]
# WebRTC can open its own connections; take it away before any page script runs.
NO_WEBRTC = """
for (const name of ["RTCPeerConnection", "webkitRTCPeerConnection", "RTCDataChannel",
                    "RTCSessionDescription", "RTCIceCandidate"]) {
  try { Object.defineProperty(window, name, {value: undefined, configurable: false}); } catch (e) {}
}
"""


def log(**fields) -> None:
    print(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), **fields}), flush=True)


def check_url(url) -> tuple[str, str]:
    """The address to open, and a note if it was changed. Raises ValueError
    for one the browser won't open."""
    url = str(url or "").strip()
    if not url:
        raise ValueError("No address given.")
    if len(url) > URL_LIMIT:
        raise ValueError(f"The address is over {URL_LIMIT} characters.")
    if "://" not in url:
        url = "https://" + url
    parts = urllib.parse.urlsplit(url)
    note = ""
    if parts.scheme == "http":
        parts = parts._replace(scheme="https")
        note = "Opened as https: the browser reaches secure sites only."
    if parts.scheme != "https":
        raise ValueError("Only web addresses (https://...) can be opened.")
    if not parts.hostname:
        raise ValueError("That address has no site in it.")
    if parts.username is not None or parts.password is not None:
        raise ValueError("Addresses with a user name or password in them aren't opened: no logins.")
    if parts.port not in (None, 443):
        raise ValueError("Only the standard web port (443) can be reached.")
    return urllib.parse.urlunsplit(parts), note


def describe(error: Exception) -> str:
    text = str(error).split("\n")[0]
    if "ERR_TUNNEL_CONNECTION_FAILED" in text or "ERR_PROXY_CONNECTION_FAILED" in text:
        return ("The site couldn't be reached: the name doesn't exist, or it points at a private or "
                "local address, which the browser never visits.")
    if "Timeout" in text:
        return "The page took longer than 30 seconds to load."
    match = re.search(r"net::ERR_[A-Z_]+", text)
    return f"The page didn't load ({match.group(0)})." if match else f"The page didn't load: {text[:300]}"


class Browser:
    def __init__(self, playwright):
        self.pw = playwright
        self.browser = None
        self.context = None
        self.page = None
        self.blocked: list[str] = []
        self.last_used = time.monotonic()

    def _note(self, what: str) -> None:
        log(blocked=what)
        self.blocked.append(what)
        del self.blocked[:-BLOCKED_KEPT]

    async def _route(self, route) -> None:
        request = route.request
        if request.method not in ALLOWED_METHODS:
            self._note(f"{request.method} {request.url[:200]}")
            await route.abort("blockedbyclient")
        else:
            await route.continue_()

    async def _websocket(self, ws) -> None:
        self._note(f"WebSocket {ws.url[:200]}")
        await ws.close()

    def _popup(self, page) -> None:
        if page is not self.page:
            self._note(f"popup {page.url[:200]}")
            asyncio.ensure_future(page.close())

    async def tab(self):
        if self.page and not self.page.is_closed():
            return self.page
        if not self.browser or not self.browser.is_connected():
            self.browser = await self.pw.chromium.launch(args=CHROMIUM_ARGS)
        self.context = await self.browser.new_context(
            service_workers="block", accept_downloads=False, locale="en-US",
            viewport={"width": 1280, "height": 900})
        await self.context.add_init_script(NO_WEBRTC)
        await self.context.route("**/*", self._route)
        await self.context.route_web_socket(re.compile(".*"), self._websocket)
        self.page = await self.context.new_page()
        self.context.on("page", self._popup)
        self.page.on("dialog", lambda dialog: asyncio.ensure_future(dialog.dismiss()))
        return self.page

    async def reset(self) -> None:
        """Throw the whole profile away; the next command starts clean."""
        if self.context:
            try:
                await self.context.close()
            except Exception:
                pass
        self.context = self.page = None
        self.blocked = []

    async def shut(self) -> None:
        await self.reset()
        if self.browser:
            try:
                await self.browser.close()
            except Exception:
                pass
        self.browser = None

    async def state(self, start: int = 0) -> dict:
        page = await self.tab()
        text = await page.evaluate("() => document.body ? document.body.innerText : ''")
        text = re.sub(r"\n{3,}", "\n\n", text or "").strip()
        start = max(0, int(start or 0))
        return {"url": page.url, "title": await page.title(), "length": len(text), "start": start,
                "text": text[start:start + TEXT_LIMIT]}

    async def open(self, url) -> dict:
        url, note = check_url(url)
        page = await self.tab()
        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        except Exception as e:
            return {"error": describe(e), "url": url}
        try:
            await page.wait_for_load_state("networkidle", timeout=SETTLE_MS)
        except Exception:
            pass                                     # busy pages never go idle; read what's there
        result = await self.state()
        result["status"] = response.status if response else None
        if note:
            result["note"] = note
        return result

    async def selftest(self) -> dict:
        """Try each forbidden thing from inside a real page, and report."""
        opened = await self.open("https://example.com/")
        if "error" in opened:
            return {"error": "example.com didn't open: " + opened["error"]}
        page = self.page
        self.blocked = []
        out = await page.evaluate("""async () => {
          const out = {};
          const tryFetch = async (options) => {
            try { await fetch("https://example.com/", options); return "sent"; }
            catch (e) { return "blocked"; }
          };
          out.get = await tryFetch({});
          out.post = await tryFetch({method: "POST", body: "x"});
          out.put = await tryFetch({method: "PUT", body: "x"});
          out.webrtc = typeof window.RTCPeerConnection;
          out.websocket = await new Promise(done => {
            try {
              const ws = new WebSocket("wss://example.com/socket");
              ws.onopen = () => done("open");
              ws.onclose = ws.onerror = () => done("blocked");
              setTimeout(() => done("no answer"), 8000);
            } catch (e) { done("blocked"); }
          });
          out.worker_post = await new Promise(done => {
            try {
              const src = 'fetch("https://example.com/w", {method: "POST", body: "x"})' +
                          '.then(() => postMessage("sent"), () => postMessage("blocked"))';
              const worker = new Worker(URL.createObjectURL(new Blob([src])));
              worker.onmessage = e => done(e.data);
              worker.onerror = () => done("error");
              setTimeout(() => done("no answer"), 8000);
            } catch (e) { done("no worker"); }
          });
          navigator.sendBeacon("https://example.com/beacon", "x");
          return out;
        }""")
        await page.evaluate("""() => {
          const form = document.createElement("form");
          form.method = "post"; form.action = "https://example.com/form";
          document.body.appendChild(form); form.submit();
        }""")
        await page.wait_for_timeout(2000)
        await page.evaluate("() => window.open('https://example.com/popup')")
        await page.wait_for_timeout(1000)
        out["beacon"] = "blocked" if any("/beacon" in b for b in self.blocked) else "not seen"
        out["form_post"] = "blocked" if any("/form" in b for b in self.blocked) else "not seen"
        out["popup"] = "closed" if any(b.startswith("popup") for b in self.blocked) else "not seen"
        out["blocked"] = list(self.blocked)
        await self.reset()
        return out

    async def run(self, command: dict) -> dict:
        self.last_used = time.monotonic()
        what = command.get("do")
        log(do=what, url=str(command.get("url", ""))[:200] or None)
        if what == "ping":
            return {"ok": True}
        if what == "open":
            return await self.open(command.get("url"))
        if what == "text":
            return await self.state(command.get("start", 0))
        if what == "state":
            page = await self.tab()
            return {"url": page.url, "title": await page.title()}
        if what == "blocked":
            seen, self.blocked = self.blocked, []
            return {"blocked": seen}
        if what == "reset":
            await self.reset()
            return {"ok": True}
        if what == "selftest":
            return await self.selftest()
        return {"error": f"Unknown command: {what!r}"}


async def serve() -> None:
    from playwright.async_api import async_playwright

    if os.path.exists(SOCKET):
        os.unlink(SOCKET)
    lock = asyncio.Lock()
    async with async_playwright() as playwright:
        browser = Browser(playwright)

        async def handle(reader, writer):
            try:
                line = await asyncio.wait_for(reader.readline(), 10)
                command = json.loads(line or b"{}")
                if not isinstance(command, dict):
                    raise ValueError("A command is a JSON object.")
                async with lock:
                    answer = await asyncio.wait_for(browser.run(command), CALL_TIMEOUT - 10)
            except ValueError as e:
                answer = {"error": str(e)}
            except asyncio.TimeoutError:
                answer = {"error": "The browser took too long and was reset."}
                await browser.shut()
            except Exception as e:                   # a crashed page or browser: start over next time
                log(failed=repr(e)[:500])
                answer = {"error": f"The browser failed ({type(e).__name__}) and was reset."}
                await browser.shut()
            writer.write(json.dumps(answer).encode() + b"\n")
            await writer.drain()
            writer.close()

        async def idle_watch():
            while True:
                await asyncio.sleep(60)
                if browser.browser and time.monotonic() - browser.last_used > IDLE_SECONDS:
                    async with lock:
                        log(idle="closing the browser")
                        await browser.shut()

        server = await asyncio.start_unix_server(handle, SOCKET, limit=1 << 20)
        os.chmod(SOCKET, 0o600)
        log(started=SOCKET, proxy=PROXY)
        asyncio.ensure_future(idle_watch())
        async with server:
            await server.serve_forever()


async def call() -> None:
    line = sys.stdin.buffer.readline()
    try:
        reader, writer = await asyncio.open_unix_connection(SOCKET, limit=1 << 22)
    except OSError:
        print(json.dumps({"error": "The browser isn't running."}))
        return
    writer.write(line.rstrip(b"\n") + b"\n")
    await writer.drain()
    answer = await asyncio.wait_for(reader.readline(), CALL_TIMEOUT)
    sys.stdout.write(answer.decode("utf-8", "replace"))


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "serve":
        asyncio.run(serve())
    elif mode == "call":
        asyncio.run(call())
    else:
        sys.exit("usage: driver.py serve | call")
