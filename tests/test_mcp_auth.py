"""MCP logins (connections/mcp_auth.py): a server that turns Ahab away, discovery, registration,
the link sent by code, the callback's row picked up, the token swap with PKCE, a refresh, and a
refused login. Fakes stand in for the server, its login service and the Supabase table."""
import base64, hashlib, json, os, sys, tempfile, threading, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ahab import tools
from ahab.connections import mcp, mcp_auth, supabase
from ahab.memory import Memory
from mcp_fake import answer  # noqa: E402  (the fake server's answers)

state = {"access": None, "refresh": None, "challenge": None, "seen_tokens": [], "forms": []}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _json(self, code, data, headers=()):
        body = json.dumps(data).encode()
        self.send_response(code)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        base = f"http://127.0.0.1:{self.server.server_port}"
        if self.path == "/.well-known/oauth-protected-resource/mcp":
            return self._json(200, {"resource": base + "/mcp", "authorization_servers": [base + "/auth"],
                                    "scopes_supported": ["read", "write"]})
        if self.path == "/.well-known/oauth-authorization-server/auth":
            return self._json(200, {"issuer": base + "/auth", "authorization_endpoint": base + "/auth/authorize",
                                    "token_endpoint": base + "/auth/token", "registration_endpoint": base + "/auth/register",
                                    "code_challenge_methods_supported": ["S256"]})
        self._json(404, {})

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        base = f"http://127.0.0.1:{self.server.server_port}"
        if self.path == "/mcp":
            got = self.headers.get("Authorization")
            state["seen_tokens"].append(got)
            if not state["access"] or got != f"Bearer {state['access']}":
                return self._json(401, {}, [("WWW-Authenticate", f'Bearer resource_metadata="{base}/.well-known/'
                                                                  'oauth-protected-resource/mcp"')])
            msg = json.loads(raw)
            if "id" not in msg or "method" not in msg:
                self.send_response(202); self.send_header("Content-Length", "0"); self.end_headers(); return
            return self._json(200, {"jsonrpc": "2.0", "id": msg["id"], "result": answer(msg["method"], msg.get("params") or {})})
        if self.path == "/auth/register":
            body = json.loads(raw)
            assert body["redirect_uris"] == [os.environ[mcp_auth.CALLBACK_ENV]] and body["token_endpoint_auth_method"] == "none"
            return self._json(201, {"client_id": "cid-1"})
        if self.path == "/auth/token":
            form = dict(urllib.parse.parse_qsl(raw.decode()))
            state["forms"].append(form)
            assert form["client_id"] == "cid-1" and form["resource"] == base + "/mcp", form
            if form["grant_type"] == "authorization_code":
                hashed = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest()).rstrip(b"=").decode()
                if form["code"] != "good-code" or hashed != state["challenge"]:
                    return self._json(400, {"error": "invalid_grant"})
                state["access"], state["refresh"] = "tok-1", "ref-1"
                return self._json(200, {"access_token": "tok-1", "refresh_token": "ref-1", "expires_in": 3600})
            if form["grant_type"] == "refresh_token" and form["refresh_token"] == state["refresh"]:
                state["access"] = "tok-2"
                return self._json(200, {"access_token": "tok-2", "expires_in": 3600})
            return self._json(400, {"error": "invalid_grant"})
        self._json(404, {})

    def do_DELETE(self):
        self.send_response(200); self.send_header("Content-Length", "0"); self.end_headers()


web = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=web.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{web.server_port}/mcp"

# The Supabase table the callback function writes to.
table = {}


def fake_call(method, path, body=None, headers=None):
    key = urllib.parse.unquote(path.split("state=eq.", 1)[1].split("&", 1)[0])
    if method == "GET":
        return ([table[key]] if key in table else []), {}
    if method == "DELETE":
        table.pop(key, None)
        return [], {}


supabase._call = fake_call
auth_folder = Path(tempfile.mkdtemp())
os.environ[mcp_auth.DIR_ENV] = str(auth_folder)
os.environ[mcp_auth.CALLBACK_ENV] = "https://cb.test/functions/v1/ahab-oauth"
home = Path(tempfile.mkdtemp())
(home / "workspace").mkdir()
(home / "home.json").write_text(json.dumps({"mcp": {"shop": {"url": URL, "approve": "none"}}}), encoding="utf-8")
m = Memory(home)


def link_sent() -> dict:
    rows = m.db.execute("SELECT text FROM messages WHERE sender = 'ahab' ORDER BY id").fetchall()
    link = rows[-1][0].split("\n")[-1]
    parts = urllib.parse.urlparse(link)
    assert link.startswith(URL.replace("/mcp", "/auth/authorize?")), link
    return dict(urllib.parse.parse_qsl(parts.query)), len(rows)


# Turned away: code finds the login service, registers, and sends the owner a link; Pip hears why.
said = tools.run(m, "mcp_tools", {"server": "shop"})
assert "needs your owner to log in first" in said and "link on Telegram" in said, said
params, sent = link_sent()
assert params["client_id"] == "cid-1" and params["redirect_uri"] == os.environ[mcp_auth.CALLBACK_ENV]
assert params["code_challenge_method"] == "S256" and params["resource"] == URL and params["scope"] == "read write"
state["challenge"] = params["code_challenge"]
# The secret stays on this machine, outside the home, and the link carries only its hash.
files = list(auth_folder.glob("*-shop.json"))
assert len(files) == 1 and not list(home.rglob("*shop.json"))
saved = json.loads(files[0].read_text(encoding="utf-8"))
assert saved["pending"]["verifier"] not in json.dumps(params) and saved["pending"]["state"] == params["state"]
# Asking again doesn't send another link while the first is still good.
assert "The link went to them" in tools.run(m, "mcp_tools", {"server": "shop"})
assert link_sent()[1] == sent

# Nothing has come back yet: nothing to wake for. Then the callback files the code.
assert mcp_auth.collect(m, force=True) is False
table[params["state"]] = {"code": "good-code", "error": None}
assert mcp_auth.collect(m, force=True) is True and not table
assert "Your owner logged in to shop" in m.db.execute("SELECT content FROM events WHERE kind = 'mcp'").fetchall()[-1][0]
assert state["forms"][-1]["code_verifier"] == saved["pending"]["verifier"]

# Now it works, with the token on every request.
listed = tools.run(m, "mcp_tools", {"server": "shop"})
assert "- echo [read-only]" in listed and state["seen_tokens"][-1] == "Bearer tok-1", listed

# The server stops taking the token: refreshed once, and the call goes through.
state["access"] = "rotated"
state["refresh"] = "ref-1"
mcp.close_all()
state["access"] = "tok-2-pending"
def refreshed_only(): return state["access"]
state["access"] = None
state["refresh"] = "ref-1"
# Expire the stored token by hand so token() refreshes before sending.
record = json.loads(files[0].read_text(encoding="utf-8"))
record["expires_at"] = 0
files[0].write_text(json.dumps(record), encoding="utf-8")
state["access"] = "tok-2"   # what the refresh hands out, and what the server will accept
out = tools.run(m, "mcp_call", {"server": "shop", "tool": "echo", "args": {"text": "again"}})
assert "echo: again" in out and state["seen_tokens"][-1] == "Bearer tok-2", (out, state["seen_tokens"][-3:])
assert state["forms"][-1]["grant_type"] == "refresh_token"

# A refused login is logged for Pip and clears the way for a new link.
mcp.close_all()
os.remove(files[0])
state["access"] = None
tools.run(m, "mcp_tools", {"server": "shop"})
params, _ = link_sent()
table[params["state"]] = {"code": None, "error": "access_denied"}
assert mcp_auth.collect(m, force=True) is True
assert "didn't go through: access_denied" in m.db.execute("SELECT content FROM events WHERE kind = 'mcp'").fetchall()[-1][0]
assert "pending" not in json.loads(files[0].read_text(encoding="utf-8"))

# A server with the owner's own key in its headers never starts a login.
(home / "home.json").write_text(json.dumps({"mcp": {"shop": {"url": URL, "headers": {"Authorization": "Bearer x"}}}}),
                                encoding="utf-8")
mcp.close_all()
assert "turned Ahab away (401)" in tools.run(m, "mcp_tools", {"server": "shop"})

# No callback anywhere: the login can't start, and Pip is told it's the owner's to set up.
os.environ.pop(mcp_auth.CALLBACK_ENV)
os.environ.pop("AHAB_SUPABASE_URL", None)
(home / "home.json").write_text(json.dumps({"mcp": {"shop": {"url": URL}}}), encoding="utf-8")
os.remove(files[0])
mcp.close_all()
assert "logins need a callback" in tools.run(m, "mcp_tools", {"server": "shop"})

mcp.close_all()
web.shutdown()
m.close()
print("mcp auth ok")
