"""The browser box's driver: one Chromium, held open by Playwright, that
Ahab's code sends commands to.

Runs inside the ahab-browser container (ahab/boxes/browser.py starts it there).
`driver.py serve` is the container's main process: it listens on a Unix
socket inside the box, so nothing is reachable from outside it, and starts
Chromium when the first command comes. `driver.py call` is how a command gets
in: ahab/boxes/browser.py runs it with `docker exec`, sends one JSON line on its
standard input and reads one back.

What the browser may do is set here, whatever a page asks for:
  - Every connection goes through the box's proxy (the only way out of the
    container anyway), which reaches public HTTPS sites and nothing private.
  - Only GET and HEAD requests go out. Anything that would create, post or
    change something (a form, a script's POST, a beacon) is stopped here and
    listed, so a page can't be made to send things on Ahab's behalf.
  - No WebSockets, no WebRTC, no service workers, no downloads, no popups
    (they're closed and listed), and dialogs are dismissed.
  - No logins: addresses with a user name in them are refused, and the whole
    profile (cookies, storage) is thrown away after IDLE_SECONDS idle or on
    "reset".
  - No typing into password fields (or other sign-in and card fields), and
    nothing that would pick a file to upload.
  - Pages are read as text snapshots (SNAPSHOT below): headings, text, and
    the links, buttons and fields numbered [1], [2]..., TEXT_LIMIT characters
    at a time. The numbers are what "click" and "type" take; every snapshot
    numbers afresh. They're kept on the elements in an attribute whose name
    is random per session, so a page can't plant numbers of its own.

Commands: {"do": "open", "url": ...}, {"do": "look", "start": n},
{"do": "click", "n": 3}, {"do": "type", "n": 3, "text": ..., "enter": false},
{"do": "scroll", "direction": "down"}, {"do": "back"}, {"do": "screenshot"}, {"do": "state"},
{"do": "blocked"}, {"do": "reset"}, {"do": "ping"}, {"do": "selftest"}
(checks the rules above on a real page). Every command that shows a page
also says what was stopped since the last one ("stopped").
"""

import asyncio
import base64
import json
import os
import re
import secrets
import sys
import time
import urllib.parse

SOCKET = "/tmp/ahab-driver.sock"
PROXY = os.environ.get("AHAB_PROXY", "http://ahab-browser-proxy:3128")
IDLE_SECONDS = 15 * 60
NAV_TIMEOUT_MS = 30_000
SETTLE_MS = 5_000
TEXT_LIMIT = 8000
URL_LIMIT = 2000
CALL_TIMEOUT = 120
ALLOWED_METHODS = {"GET", "HEAD"}
BLOCKED_KEPT = 50
TYPE_LIMIT = 2000
CLICK_TIMEOUT_MS = 5_000
# Fields never typed into: passwords, one-time codes and card details. That's
# what "no logins" means here.
SECRET_AUTOCOMPLETE = re.compile(r"(current-password|new-password|one-time-code|cc-)")
TYPEABLE_INPUTS = {"text", "search", "email", "url", "tel", "number", "date", "datetime-local",
                   "month", "week", "time", ""}
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
# The page as text: blocks on their own lines, headings marked with #, list
# items with -, images by their alt text, and every link, button and field
# numbered, with the number kept on the element in the attribute named by
# the argument. Hidden things are left out.
SNAPSHOT = r"""(attr) => {
  const SKIP = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "HEAD", "SVG", "CANVAS", "IFRAME",
                        "OBJECT", "EMBED", "VIDEO", "AUDIO", "OPTION"]);
  const BLOCK = new Set(["ADDRESS", "ARTICLE", "ASIDE", "BLOCKQUOTE", "DD", "DIV", "DL", "DT", "FIELDSET",
                         "FIGCAPTION", "FIGURE", "FOOTER", "FORM", "H1", "H2", "H3", "H4", "H5", "H6",
                         "HEADER", "HR", "LI", "MAIN", "NAV", "OL", "P", "PRE", "SECTION", "TABLE", "TR",
                         "UL", "BR", "DETAILS", "CAPTION", "DIALOG"]);
  const ROLES = ["button", "link", "tab", "menuitem", "checkbox", "radio", "switch", "option",
                 "combobox", "textbox", "searchbox"];
  for (const el of document.querySelectorAll("[" + attr + "]")) el.removeAttribute(attr);
  const clip = (s, max) => {
    s = String(s || "").replace(/\s+/g, " ").trim();
    return s.length > max ? s.slice(0, max - 1) + "…" : s;
  };
  const hidden = el => {
    if (el.getAttribute("aria-hidden") === "true" || el.hidden) return true;
    const style = getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden" || style.visibility === "collapse") return true;
    return style.display !== "contents" && el.getClientRects().length === 0;
  };
  const kind = el => {
    const tag = el.tagName.toUpperCase(), role = (el.getAttribute("role") || "").toLowerCase();
    if (tag === "A" && el.hasAttribute("href")) return "link";
    if (tag === "BUTTON" || tag === "SUMMARY") return "button";
    if (tag === "SELECT") return "dropdown";
    if (tag === "TEXTAREA") return "textbox";
    if (tag === "INPUT") {
      const type = (el.getAttribute("type") || "text").toLowerCase();
      if (type === "hidden") return null;
      if (type === "password") return "password field";
      if (type === "file") return "file picker";
      if (type === "checkbox" || type === "radio") return type;
      if (["submit", "button", "reset", "image"].includes(type)) return "button";
      return type === "search" ? "searchbox" : "textbox";
    }
    if (el.getAttribute("contenteditable") === "true" || el.getAttribute("contenteditable") === "") return "textbox";
    return ROLES.includes(role) ? role : null;
  };
  const label = el => {
    const tag = el.tagName.toUpperCase();
    let s = el.getAttribute("aria-label") || "";
    if (!s && el.getAttribute("aria-labelledby"))
      s = el.getAttribute("aria-labelledby").split(/\s+/)
            .map(id => (document.getElementById(id) || {}).innerText || "").join(" ");
    if (!s && el.labels && el.labels.length) s = el.labels[0].innerText;
    if (!s && !["INPUT", "SELECT", "TEXTAREA"].includes(tag)) s = el.innerText;
    if (!s && el.querySelector) { const img = el.querySelector("img[alt]"); if (img) s = img.alt; }
    if (!s) s = el.getAttribute("title") || el.getAttribute("placeholder") || "";
    if (!s && tag === "INPUT" && ["submit", "button", "reset"].includes(el.type)) s = el.value;
    return clip(s || el.getAttribute("name") || "", 80);
  };
  const where = el => {
    try {
      const u = new URL(el.getAttribute("href"), location.href);
      if (!/^https?:$/.test(u.protocol)) return "";
      return clip(u.origin === location.origin ? u.pathname + u.search : u.host + u.pathname, 70);
    } catch (e) { return ""; }
  };
  let n = 0;
  const out = [];
  const walk = node => {
    if (node.nodeType === 3) { out.push(node.textContent); return; }
    if (node.nodeType !== 1) return;
    const el = node, tag = el.tagName.toUpperCase();
    if (SKIP.has(tag)) return;
    if (tag === "SLOT") { for (const child of el.assignedNodes()) walk(child); return; }
    if (hidden(el)) return;
    const k = kind(el);
    if (k) {
      n += 1;
      el.setAttribute(attr, String(n));
      let line = "[" + n + "] " + k + ' "' + label(el) + '"';
      if (k === "link") { const w = where(el); if (w) line += " -> " + w; }
      if ((k === "textbox" || k === "searchbox") && el.value) line += ' = "' + clip(el.value, 60) + '"';
      if (k === "dropdown" && el.selectedOptions && el.selectedOptions[0])
        line += ' = "' + clip(el.selectedOptions[0].text, 60) + '"';
      if ((k === "checkbox" || k === "radio") && el.checked) line += " (checked)";
      if (el.getAttribute("aria-expanded") === "true") line += " (open)";
      if (el.disabled) line += " (disabled)";
      out.push(" " + line + " ");
      return;
    }
    const block = BLOCK.has(tag);
    if (block) out.push("\n");
    if (/^H[1-6]$/.test(tag)) out.push("#".repeat(Number(tag[1])) + " ");
    if (tag === "LI") out.push("- ");
    if (tag === "IMG" && el.alt) out.push(" [image: " + clip(el.alt, 80) + "] ");
    for (const child of (el.shadowRoot || el).childNodes) walk(child);
    if (tag === "TD" || tag === "TH") out.push(" | ");
    if (block) out.push("\n");
  };
  // The main content first, so it isn't buried under menus; the rest after.
  const main = document.querySelector("main, [role=main]");
  if (main && !hidden(main)) {
    walk(main);
    out.push("\n--- Around the main content (menus, header, footer) ---\n");
    const walkRest = node => {          // everything but main: open up only main's ancestors
      if (node === main) return;
      if (node.nodeType === 1 && node.contains(main)) {
        for (const child of node.childNodes) walkRest(child);
      } else walk(node);
    };
    if (document.body) walkRest(document.body);
  } else if (document.body) walk(document.body);
  return out.join("").replace(/[ \t ]+/g, " ").replace(/ *\n */g, "\n")
            .replace(/\n{2,}/g, "\n").replace(/^(#+) *\n/gm, "$1 ").trim();
}"""


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
        self.blocked: list[str] = []      # everything stopped, for "blocked" and the self-test
        self.unseen: list[str] = []       # stopped since the last page shown
        self.attr = ""
        self.last_used = time.monotonic()

    def _note(self, what: str) -> None:
        log(blocked=what)
        for kept in (self.blocked, self.unseen):
            kept.append(what)
            del kept[:-BLOCKED_KEPT]

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
        self.attr = "data-p" + secrets.token_hex(4)
        return self.page

    async def reset(self) -> None:
        """Throw the whole profile away; the next command starts clean."""
        if self.context:
            try:
                await self.context.close()
            except Exception:
                pass
        self.context = self.page = None
        self.blocked, self.unseen = [], []

    async def shut(self) -> None:
        await self.reset()
        if self.browser:
            try:
                await self.browser.close()
            except Exception:
                pass
        self.browser = None

    async def settle(self, pause_ms: int = 500) -> None:
        """Give a click or a keypress time to start loading, then let it load."""
        page = self.page
        await page.wait_for_timeout(pause_ms)
        for state, ms in (("domcontentloaded", NAV_TIMEOUT_MS), ("networkidle", SETTLE_MS)):
            try:
                await page.wait_for_load_state(state, timeout=ms)
            except Exception:
                pass                                 # busy pages never go idle; read what's there

    def _open_page(self):
        """The page being read, or None if nothing's open (or it was reset)."""
        page = self.page
        if not page or page.is_closed() or page.url in ("", "about:blank"):
            return None
        return page

    async def look(self, start: int = 0) -> dict:
        page = self._open_page()
        if page is None:
            return {"error": "No page is open (the browser forgets everything after 15 idle minutes); "
                             "open one first."}
        try:
            text = await page.evaluate(SNAPSHOT, self.attr)
        except Exception:                            # mid-navigation: wait once, then try again
            await self.settle(0)
            text = await page.evaluate(SNAPSHOT, self.attr)
        start = max(0, int(start or 0))
        stopped, self.unseen = self.unseen, []
        return {"url": page.url, "title": await page.title(), "length": len(text), "start": start,
                "text": text[start:start + TEXT_LIMIT], "stopped": stopped}

    async def screenshot(self) -> dict:
        """What the open page looks like: the visible part, as a JPEG. It
        loads nothing new; a picture of what's already there."""
        page = self._open_page()
        if page is None:
            return {"error": "No page is open (the browser forgets everything after 15 idle minutes); "
                             "open one first."}
        shot = await page.screenshot(type="jpeg", quality=70, full_page=False, timeout=20_000)
        return {"url": page.url, "title": await page.title(), "image": base64.b64encode(shot).decode("ascii")}

    async def open(self, url) -> dict:
        url, note = check_url(url)
        page = await self.tab()
        self.unseen = []
        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        except Exception as e:
            return {"error": describe(e), "url": url}
        await self.settle(0)
        result = await self.look()
        result["status"] = response.status if response else None
        if note:
            result["note"] = note
        return result

    async def _element(self, n):
        """The numbered element, and what it is."""
        page = self._open_page()
        if page is None:
            return None, {"error": "No page is open; open one first."}
        try:
            n = int(n)
        except (TypeError, ValueError):
            return None, {"error": "n is the number in square brackets, like 12 for [12]."}
        element = page.locator(f'[{self.attr}="{n}"]').first
        if not await element.count():
            return None, {"error": f"There's no [{n}] on the page now; look again for fresh numbers."}
        info = await element.evaluate("""e => ({
            tag: e.tagName.toUpperCase(),
            type: (e.getAttribute("type") || "").toLowerCase(),
            autocomplete: (e.getAttribute("autocomplete") || "").toLowerCase(),
            editable: e.isContentEditable})""")
        return element, info

    async def click(self, n) -> dict:
        element, info = await self._element(n)
        if element is None:
            return info
        if info["type"] == "file":
            return {"error": "That picks a file to upload; the browser doesn't upload anything."}
        # Links and forms that would open a new tab open in this one instead.
        await element.evaluate("""e => {
            for (const holder of [e.closest("a"), e.closest("form"), e.form])
              if (holder) holder.removeAttribute("target");
        }""")
        try:
            await element.click(timeout=CLICK_TIMEOUT_MS)
        except Exception as e:
            return {"error": f"[{n}] couldn't be clicked ({str(e).splitlines()[0][:200]}). "
                             "It may be covered or gone; look again."}
        await self.settle()
        return await self.look()

    async def type(self, n, text, enter=False) -> dict:
        text = str(text if text is not None else "")
        if len(text) > TYPE_LIMIT:
            return {"error": f"That's {len(text)} characters; the limit is {TYPE_LIMIT}."}
        element, info = await self._element(n)
        if element is None:
            return info
        if info["type"] == "password" or SECRET_AUTOCOMPLETE.search(info["autocomplete"]):
            return {"error": "That's a password, sign-in code or card field. The browser never types into "
                             "those: no logins, no payments."}
        if info["type"] == "file":
            return {"error": "That picks a file to upload; the browser doesn't upload anything."}
        try:
            if info["tag"] == "SELECT":
                await element.select_option(label=text, timeout=CLICK_TIMEOUT_MS)
            elif info["tag"] == "TEXTAREA" or info["editable"] or (
                    info["tag"] == "INPUT" and info["type"] in TYPEABLE_INPUTS):
                await element.fill(text, timeout=CLICK_TIMEOUT_MS)
            else:
                return {"error": f"[{n}] isn't something to type into; click it instead."}
            if enter:
                await element.press("Enter", timeout=CLICK_TIMEOUT_MS)
        except Exception as e:
            return {"error": f"Couldn't type into [{n}] ({str(e).splitlines()[0][:200]})."}
        await self.settle()
        return await self.look()

    async def scroll(self, direction="down") -> dict:
        page = self._open_page()
        if page is None:
            return {"error": "No page is open; open one first."}
        sign = {"down": 1, "up": -1}.get(str(direction).lower())
        if sign is None:
            return {"error": 'direction is "down" or "up".'}
        await page.evaluate("s => window.scrollBy(0, s * window.innerHeight * 0.9)", sign)
        await page.wait_for_timeout(1500)            # room for whatever loads as you go
        return await self.look()

    async def back(self) -> dict:
        page = self._open_page()
        if page is None:
            return {"error": "No page is open; open one first."}
        try:
            response = await page.go_back(wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        except Exception as e:
            return {"error": describe(e)}
        if response is None and self._open_page() is None:
            return {"error": "There's nothing to go back to."}
        await self.settle(0)
        return await self.look()

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
          const fields = [["password", ""], ["text", "current-password"], ["text", "cc-number"],
                          ["file", ""], ["text", ""]];
          fields.forEach(([type, auto], i) => {
            const input = document.createElement("input");
            input.type = type; input.id = "ahab-test-" + i;
            if (auto) input.setAttribute("autocomplete", auto);
            document.body.appendChild(input);
          });
          return out;
        }""")
        # The typing rules, on the fields just added: four refused, the plain one typed into.
        await self.look()
        numbers = await page.evaluate(
            "attr => [0, 1, 2, 3, 4].map(i => document.getElementById('ahab-test-' + i).getAttribute(attr))",
            self.attr)
        refused = [("error" in await self.type(n, "secret")) for n in numbers[:3]]
        refused.append("error" in await self.click(numbers[3]))
        typed = await self.type(numbers[4], "hello")
        out["secret_fields"] = "refused" if all(refused) else f"typed into one: {refused}"
        out["plain_field"] = "typed" if "error" not in typed and 'hello' in typed["text"] else "failed"
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
        log(do=what, url=str(command.get("url", ""))[:200] or None, n=command.get("n"))
        if what == "ping":
            return {"ok": True}
        if what == "open":
            return await self.open(command.get("url"))
        if what == "look":
            return await self.look(command.get("start", 0))
        if what == "click":
            return await self.click(command.get("n"))
        if what == "type":
            return await self.type(command.get("n"), command.get("text"), bool(command.get("enter")))
        if what == "scroll":
            return await self.scroll(command.get("direction", "down"))
        if what == "back":
            return await self.back()
        if what == "screenshot":
            return await self.screenshot()
        if what == "state":
            page = self._open_page()
            return {"url": page.url, "title": await page.title()} if page else {"url": None, "title": None}
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
