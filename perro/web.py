"""Web: a read-only window onto the internet.

Until now Perro's whole world was itself: its notes, its log, and an empty
workspace. These two tools let it look out:

  fetch_page        download one web page and read it as plain text
  search_wikipedia  search English Wikipedia (an official, free API, no key)

Read-only means GET requests only: no cookies, no logins, no forms, no
JavaScript. Perro sees a page the way a very plain browser would, as text with
numbered links, and moves around by fetching those links. It can look at any
public site; it can't act on one.

The real danger in letting a program fetch URLs is that "the internet" also
includes your own machine and home network: localhost servers, your router's
admin page, other devices. So every hostname goes through _vet() below. It's
looked up first, and the request is refused unless every address it resolves
to is public. The connection is then made to exactly the address that passed,
so a hostname can't pass the check and then quietly point somewhere else.
Every redirect gets the same check before it's followed.
"""

from __future__ import annotations

import html
import http.client
import ipaddress
import json
import re
import socket
import ssl
import sys
import time
import urllib.parse
import zlib
from html.parser import HTMLParser

USER_AGENT = "Perro/0.1 (personal agent)"
TIMEOUT = 20                     # seconds for any single network step
DEADLINE = 60                    # seconds for a whole fetch, redirects included
MAX_BYTES = 3 * 1024 * 1024      # stop downloading a page after this much
MAX_REDIRECTS = 5
URL_CHAR_LIMIT = 2_000
# What one fetch shows Perro: a slice of the page's text, plus the links in it.
PAGE_CHAR_LIMIT = 8_000
LINK_LIMIT = 40
LINK_URL_LIMIT = 500             # longer link URLs are left off the list
SEARCH_LIMIT = 8

HTML_TYPES = {"text/html", "application/xhtml+xml"}
TEXT_TYPES = {"application/json", "application/xml", "application/rss+xml",
              "application/atom+xml", "application/javascript"}
# Characters left alone when cleaning up a path or query for the request line.
URL_SAFE = "/%:@!$&'()*+,;=-._~"


class WebError(Exception):
    """A fetch was refused or failed. The message goes back to Perro as text."""


# --- the network fence ---------------------------------------------------

def _public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True only for addresses out on the public internet."""
    if ip.version == 6:
        # Some IPv6 addresses carry an IPv4 address inside them; check that too.
        embedded = [ip.ipv4_mapped, ip.sixtofour, *(ip.teredo or ())]
        if any(e is not None and not _public(e) for e in embedded):
            return False
    # is_global is False for loopback (127.x, ::1), private ranges (10.x,
    # 192.168.x...), link-local (169.254.x, where cloud metadata lives),
    # carrier-grade NAT, and other reserved blocks.
    return ip.is_global and not ip.is_multicast


def _vet(host: str, port: int) -> list[str]:
    """Look up host. Return its addresses if all are public; otherwise refuse."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        raise WebError(f"Couldn't find {host!r} (the name doesn't resolve).") from None
    addresses = []
    for family, _, _, _, sockaddr in infos:
        if family not in (socket.AF_INET, socket.AF_INET6):
            continue
        address = sockaddr[0].split("%")[0]
        if not _public(ipaddress.ip_address(address)):
            raise WebError(f"{host!r} is on a private or local network; "
                           "only public sites can be fetched.")
        addresses.append(address)
    if not addresses:
        raise WebError(f"Couldn't find {host!r} (no usable address).")
    return addresses


def _connect(addresses: list[str], port: int, timeout: float) -> socket.socket:
    error: OSError | None = None
    for address in addresses:
        try:
            return socket.create_connection((address, port), timeout)
        except OSError as e:
            error = e
    raise error


class _PinnedHTTP(http.client.HTTPConnection):
    """An HTTP connection that only ever dials the addresses _vet() approved."""

    def __init__(self, host: str, port: int, addresses: list[str], timeout: float):
        super().__init__(host, port, timeout=timeout)
        self._addresses = addresses

    def connect(self) -> None:
        self.sock = _connect(self._addresses, self.port, self.timeout)


class _PinnedHTTPS(http.client.HTTPSConnection):
    """Same, for HTTPS. The certificate is still checked against the hostname."""

    def __init__(self, host: str, port: int, addresses: list[str], timeout: float):
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._addresses = addresses

    def connect(self) -> None:
        sock = _connect(self._addresses, self.port, self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


# --- downloading ---------------------------------------------------------

def _parse(url: str) -> tuple[str, str, int, str]:
    """Check a URL and split it into (scheme, host, port, path-with-query)."""
    if len(url) > URL_CHAR_LIMIT:
        raise WebError(f"That address is over {URL_CHAR_LIMIT} characters.")
    if any(c in url for c in "\x00\r\n\t"):
        raise WebError("Addresses can't contain control characters.")
    parts = urllib.parse.urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise WebError("Only http:// and https:// addresses can be fetched.")
    if parts.username is not None or parts.password is not None:
        raise WebError("Addresses with a username or password in them aren't allowed.")
    host = parts.hostname
    if not host:
        raise WebError("That address has no site name in it.")
    try:
        host = host.encode("idna").decode("ascii")   # non-English domain names
        port = parts.port or (443 if scheme == "https" else 80)
    except (UnicodeError, ValueError):
        raise WebError("That address isn't valid.") from None
    path = urllib.parse.quote(parts.path or "/", safe=URL_SAFE)
    if parts.query:
        path += "?" + urllib.parse.quote(parts.query, safe=URL_SAFE + "?")
    return scheme, host, port, path


def _read(response: http.client.HTTPResponse, deadline: float) -> tuple[bytes, bool]:
    """Read the body, up to MAX_BYTES or the deadline. Returns (body, cut off?)."""
    chunks, size = [], 0
    while size <= MAX_BYTES:
        if time.monotonic() > deadline:
            if not chunks:
                raise WebError("The page took too long to download.")
            return b"".join(chunks), True
        # read1 returns whatever has arrived (up to 64 KB) instead of waiting
        # for the full amount, so a server dribbling bytes can't outlast the deadline.
        chunk = response.read1(64 * 1024)
        if not chunk:
            return b"".join(chunks), False
        chunks.append(chunk)
        size += len(chunk)
    return b"".join(chunks)[:MAX_BYTES], True


def _decompress(body: bytes, encoding: str) -> tuple[bytes, bool]:
    """Undo gzip/deflate if the server used it anyway. Capped at MAX_BYTES, so
    a small file that unpacks into gigabytes can't fill memory."""
    encoding = encoding.strip().lower()
    if encoding in ("", "identity"):
        return body, False
    if encoding not in ("gzip", "x-gzip", "deflate"):
        raise WebError(f"The page came compressed as {encoding!r}, which can't be read here.")
    wbits = 16 + zlib.MAX_WBITS if "gzip" in encoding else zlib.MAX_WBITS
    try:
        unpacker = zlib.decompressobj(wbits)
        out = unpacker.decompress(body, MAX_BYTES)
    except zlib.error:
        raise WebError("The page's compression was broken.") from None
    return out, bool(unpacker.unconsumed_tail)


def _describe(e: Exception) -> str:
    if isinstance(e, ssl.SSLCertVerificationError):
        return f"The site's security certificate couldn't be verified ({e.verify_message})."
    if isinstance(e, TimeoutError):
        return "The site didn't respond in time."
    if isinstance(e, ConnectionRefusedError):
        return "The site refused the connection."
    return f"The fetch failed: {type(e).__name__}: {e}"


def _get(url: str) -> tuple[str, str, bytes, bool, bool]:
    """Download a URL, following redirects, re-vetting every hop.

    Returns (final url, content type, body, cut off?, redirected?).
    """
    deadline = time.monotonic() + DEADLINE
    redirected = False
    for _ in range(MAX_REDIRECTS + 1):
        if time.monotonic() > deadline:
            raise WebError("The page took too long to download.")
        scheme, host, port, path = _parse(url)
        addresses = _vet(host, port)
        connection_class = _PinnedHTTPS if scheme == "https" else _PinnedHTTP
        connection = connection_class(host, port, addresses, TIMEOUT)
        try:
            connection.request("GET", path, headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,text/plain;q=0.9,*/*;q=0.5",
                "Accept-Language": "en",
                "Accept-Encoding": "identity",
            })
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader("Location")
                if not location:
                    raise WebError(f"The site sent a redirect (HTTP {response.status}) "
                                   "with no address.")
                url = urllib.parse.urljoin(url, location.strip())
                redirected = True
                continue
            if response.status >= 400:
                raise WebError(f"The site answered HTTP {response.status} {response.reason}.")
            content_type = response.getheader("Content-Type", "")
            body, cut = _read(response, deadline)
            body, unpack_cut = _decompress(body, response.getheader("Content-Encoding", ""))
            return url, content_type, body, cut or unpack_cut, redirected
        except WebError:
            raise
        except (OSError, http.client.HTTPException) as e:
            raise WebError(_describe(e)) from None
        finally:
            connection.close()
    raise WebError(f"Gave up after {MAX_REDIRECTS} redirects.")


# --- turning a page into text --------------------------------------------

class _TextExtractor(HTMLParser):
    """Strip a web page down to readable text, numbering its links [#1], [#2]...

    The numbers are written as [#n] rather than [n] so they can't be confused
    with the bracketed footnote numbers many pages (Wikipedia) already have.

    With only_main, text outside the page's <main> element (menus, sidebars,
    footers on sites that mark their content properly) is left out.
    """

    # Content that's never text a person would read. (Not <head>: its closing
    # tag is optional in HTML, and an unclosed skip would swallow the page.)
    SKIP = {"script", "style", "noscript", "template", "svg", "math", "nav",
            "button", "select", "iframe", "object", "canvas"}
    BLOCK = {"p", "div", "section", "article", "main", "header", "footer", "aside",
             "blockquote", "pre", "table", "tr", "ul", "ol", "dl", "dt", "dd",
             "figure", "figcaption", "hr", "address", "details", "summary", "li"}
    HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
            "meta", "source", "track", "wbr"}
    # Elements also skipped, whatever their tag: hidden ones, and a few classes
    # that are pure page furniture on Wikipedia ("[edit]" links, giant
    # navigation boxes, "Part of a series on..." sidebars, maintenance banners,
    # print-hidden extras).
    JUNK_CLASSES = {"mw-editsection", "navbox", "sidebar", "ambox", "noprint", "mw-jump-link"}

    def __init__(self, base_url: str, only_main: bool = False):
        super().__init__(convert_charrefs=True)
        self.base = base_url
        self.out: list[str] = []
        self.title = ""
        self.links: dict[str, int] = {}     # url -> its number
        self._only_main = only_main
        self._main = 0
        self._skip = 0
        self._junk_tag: str | None = None   # tag name of the junk element we're inside
        self._junk_depth = 0
        self._pre = 0
        self._in_title = False
        self._link: str | None = None
        self._link_start = 0
        self._chars = 0          # visible characters so far, to spot empty links

    def _ignoring(self) -> bool:
        return bool(self._skip or self._junk_tag or (self._only_main and not self._main))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs = dict(attrs)
        if tag == "title":
            self._in_title = True
        elif tag == "base" and attrs.get("href"):
            self.base = urllib.parse.urljoin(self.base, attrs["href"])
        elif tag == "main":
            self._main += 1
        if self._junk_tag:
            # Inside a junk element: only count same-named tags, to find its end.
            if tag == self._junk_tag:
                self._junk_depth += 1
            return
        classes = set((attrs.get("class") or "").split())
        if tag not in self.VOID and ("hidden" in attrs or attrs.get("aria-hidden") == "true"
                                     or classes & self.JUNK_CLASSES):
            self._junk_tag, self._junk_depth = tag, 1
            return
        if tag in self.SKIP:
            self._skip += 1
        if self._ignoring():
            return
        if tag in self.HEADINGS:
            self.out.append("\n\n" + "#" * self.HEADINGS[tag] + " ")
        elif tag == "li":
            self.out.append("\n- ")
        elif tag == "br":
            self.out.append("\n")
        elif tag in ("td", "th"):
            self.out.append(" | ")
        elif tag in self.BLOCK:
            self.out.append("\n")
        if tag == "pre":
            self._pre += 1
        elif tag == "a" and attrs.get("href"):
            url = urllib.parse.urldefrag(urllib.parse.urljoin(self.base, attrs["href"].strip()))[0]
            # Links to a spot on this same page (footnotes, "jump to") aren't
            # anywhere new, so they don't get a number.
            if (urllib.parse.urlsplit(url).scheme in ("http", "https")
                    and url != urllib.parse.urldefrag(self.base)[0]):
                self._link, self._link_start = url, self._chars
        elif tag == "img" and attrs.get("alt"):
            self.out.append(f" [image: {attrs['alt'].strip()}] ")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if self._junk_tag:
            if tag == self._junk_tag:
                self._junk_depth -= 1
                if not self._junk_depth:
                    self._junk_tag = None
            return
        if tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
            return
        if tag == "main":
            self._main = max(0, self._main - 1)
            if self._only_main and not self._main:
                self.out.append("\n")
                return
        if self._ignoring():
            return
        if tag == "a" and self._link:
            if self._chars > self._link_start:   # the link had visible text
                number = self.links.setdefault(self._link, len(self.links) + 1)
                self.out.append(f" [#{number}]")
            self._link = None
        elif tag == "pre":
            self._pre = max(0, self._pre - 1)
        if tag in self.HEADINGS or tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._ignoring():
            self.out.append(data if self._pre else re.sub(r"\s+", " ", data))
            self._chars += len(data.strip())

    def text(self) -> str:
        lines = (re.sub(r"[ \t]+", " ", line).strip() for line in "".join(self.out).split("\n"))
        lines = [line for line in lines if line not in ("|", "-")]
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _kind(content_type: str, body: bytes) -> str:
    """'html', 'text', or refuse: Perro can only read text."""
    mime = content_type.split(";")[0].strip().lower()
    if mime in HTML_TYPES:
        return "html"
    if mime.startswith("text/") or mime in TEXT_TYPES or mime.endswith(("+json", "+xml")):
        return "text"
    if not mime:
        head = body[:2048]
        if b"\x00" in head:
            raise WebError("That isn't a text page.")
        return "html" if b"<html" in head.lower() else "text"
    raise WebError(f"That's a {mime} file, not a text page; only text can be read.")


def _decode(body: bytes, content_type: str, kind: str) -> str:
    match = re.search(r"charset=[\"']?([\w.:-]+)", content_type, re.I)
    charset = match.group(1) if match else None
    if not charset and kind == "html":
        match = re.search(rb"<meta[^>]+charset=[\"']?([\w.:-]+)", body[:4096], re.I)
        charset = match.group(1).decode("ascii") if match else None
    try:
        text = body.decode(charset or "utf-8", errors="replace")
    except LookupError:   # a charset name Python doesn't know
        text = body.decode("utf-8", errors="replace")
    return text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")


def _extract(page: str, base_url: str) -> tuple[str, str, dict[str, int]]:
    """Return (title, text, links) for an HTML page."""
    # Prefer just the <main> part when the page has one and it holds real text.
    for only_main in ((True, False) if re.search(r"<main[\s>]", page, re.I) else (False,)):
        extractor = _TextExtractor(base_url, only_main=only_main)
        extractor.feed(page)
        extractor.close()
        text = extractor.text()
        if len(text) >= 200 or not only_main:
            break
    title = re.sub(r"\s+", " ", extractor.title).strip()[:200]
    return title, text, extractor.links


def _wikipedia_article(url: str) -> tuple[str, str] | None:
    """For a Wikipedia article address, return (title, address of the article
    alone). Wikipedia's official action=render gives just the article body,
    without the hundred-odd language links and menus a full page starts with."""
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if (parts.scheme.lower() in ("http", "https") and host.endswith(".wikipedia.org")
            and parts.path.startswith("/wiki/") and len(parts.path) > 6 and not parts.query):
        title = urllib.parse.unquote(parts.path[len("/wiki/"):])
        render = f"https://{host}/w/index.php?" + urllib.parse.urlencode(
            {"title": title, "action": "render"})
        return title.replace("_", " "), render
    return None


def _page(url: str) -> tuple[str, str, str, dict[str, int], bool, bool]:
    """(final url, title, text, links, cut off?, redirected?) for a URL."""
    wikipedia = _wikipedia_article(url)
    final_url, content_type, body, cut, redirected = _get(wikipedia[1] if wikipedia else url)
    kind = _kind(content_type, body)
    text = _decode(body, content_type, kind)
    title, links = "", {}
    if kind == "html":
        title, text, links = _extract(text, final_url)
    else:
        text = text.strip()
    if wikipedia:
        title, final_url, redirected = wikipedia[0], url, False
    return final_url, title, text, links, cut, redirected


def page_text(url: str) -> tuple[str, str, str]:
    """(final url, title, whole readable text) of a page, for code that reads
    it rather than showing it (watches.py). Same fence as fetch_page."""
    final_url, title, text, _, _, _ = _page(str(url).strip())
    return final_url, title, text


# --- the tools -----------------------------------------------------------

def fetch_page(url: str, start: int = 0) -> str:
    url = str(url).strip()
    if isinstance(start, bool) or not isinstance(start, (int, float, str)):
        raise WebError("start must be a whole number.")
    try:
        start = int(start)
    except ValueError:
        raise WebError("start must be a whole number.") from None
    if start < 0:
        raise WebError("start can't be negative.")

    final_url, title, text, links, cut, redirected = _page(url)
    named = f'{final_url}: "{title}"' if title else final_url
    if not text:
        return (f"Fetched {named}, but it has no readable text "
                "(the page may need JavaScript, which isn't available here).")
    if start >= len(text):
        raise WebError(f"That page has {len(text)} chars of text; start must be below that.")
    chunk = text[start:start + PAGE_CHAR_LIMIT]
    end = start + len(chunk)

    lines = [f"Fetched {named} ({len(text)} chars of text; showing {start}-{end})."]
    if redirected:
        lines.append(f"(Redirected from {url}.)")
    if cut:
        lines.append(f"(Only the first {MAX_BYTES // (1024 * 1024)} MB of the page was downloaded.)")
    lines += ["", chunk]
    if end < len(text):
        lines.append(f'\n[...more: fetch again with "start": {end}]')

    by_number = {n: u for u, n in links.items()}
    shown = list(dict.fromkeys(int(n) for n in re.findall(r"\[#(\d+)\]", chunk)))
    listed = [n for n in shown if n in by_number and len(by_number[n]) <= LINK_URL_LIMIT]
    if listed:
        lines.append("\nLinks in this part:")
        lines += [f"[#{n}] {by_number[n]}" for n in listed[:LINK_LIMIT]]
        if len(listed) > LINK_LIMIT:
            lines.append(f"(...and {len(listed) - LINK_LIMIT} more links in this part, not listed)")
    return "\n".join(lines)


def search_wikipedia(query: str) -> str:
    query = str(query).strip()
    if not query:
        raise WebError("search needs a non-empty query.")
    if len(query) > 300:
        raise WebError("Keep the search under 300 characters.")
    url = "https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode({
        "action": "query", "list": "search", "srsearch": query,
        "srlimit": SEARCH_LIMIT, "format": "json", "formatversion": 2,
    })
    _, _, body, _, _ = _get(url)
    try:
        hits = json.loads(body.decode("utf-8"))["query"]["search"]
    except (ValueError, KeyError, TypeError):
        raise WebError("Wikipedia sent back something unexpected.") from None
    if not hits:
        return f"No Wikipedia articles match {query!r}."
    lines = [f"{len(hits)} Wikipedia articles for {query!r}:"]
    for hit in hits:
        title = str(hit.get("title", ""))
        link = "https://en.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_"))
        snippet = html.unescape(re.sub(r"<[^>]+>", "", str(hit.get("snippet", ""))))
        lines.append(f"- {title}: {link}\n  {snippet}")
    return "\n".join(lines)


if __name__ == "__main__":
    # Manual check:  python -m perro.web <url> [start]   or   python -m perro.web --search <words>
    sys.stdout.reconfigure(errors="replace")
    try:
        if sys.argv[1:2] == ["--search"]:
            print(search_wikipedia(" ".join(sys.argv[2:])))
        elif len(sys.argv) > 1:
            print(fetch_page(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 0))
        else:
            sys.exit(__doc__)
    except WebError as e:
        sys.exit(f"Refused or failed: {e}")
