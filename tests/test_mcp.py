"""MCP client (connections/mcp.py): the handshake, listing and calling over stdio and over
http, the environment a server gets, approvals, the delete brake, files, and a server dying."""
import base64, json, os, sys, tempfile, textwrap, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ahab import approvals, tools, toolkit
from ahab.connections import mcp
from ahab.memory import Memory

from mcp_fake import PNG, TOOLS  # noqa: E402  (the fake server's answers)


# The stdio server: a script that imports answer() from this file and speaks JSON lines.
folder = Path(tempfile.mkdtemp())
script = folder / "fake_server.py"
script.write_text(textwrap.dedent(f"""
    import json, os, sys
    sys.path.insert(0, {str(Path(__file__).resolve().parent)!r})
    from mcp_fake import answer
    print("not protocol, just chatter", flush=True)
    for line in sys.stdin:
        msg = json.loads(line)
        if "id" not in msg:
            continue
        if msg["method"] == "tools/call" and msg["params"]["name"] == "die":
            print("dying now", file=sys.stderr, flush=True)
            sys.exit(3)
        if msg["method"] == "tools/call":   # ask the client something first, as servers may
            print(json.dumps({{"jsonrpc": "2.0", "id": "s1", "method": "ping"}}), flush=True)
            reply = json.loads(sys.stdin.readline())
            assert reply == {{"jsonrpc": "2.0", "id": "s1", "result": {{}}}}, reply
        try:
            out = {{"jsonrpc": "2.0", "id": msg["id"], "result": answer(msg["method"], msg.get("params") or {{}})}}
        except KeyError:
            out = {{"jsonrpc": "2.0", "id": msg["id"], "error": {{"code": -32601, "message": "no such method"}}}}
        print(json.dumps(out), flush=True)
"""), encoding="utf-8")
# answer() lives in a module of its own so the server can import it without running this test.
server_module = Path(__file__).resolve().parent / "mcp_fake.py"
assert server_module.exists(), "tests/mcp_fake.py holds the fake server's answers"


# The http server: answers as JSON, except tools/call, which comes back as an event stream.
seen = {"auth": [], "sessions": [], "agents": []}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_POST(self):
        seen["auth"].append(self.headers.get("Authorization"))
        seen["sessions"].append(self.headers.get("Mcp-Session-Id"))
        seen["agents"].append(self.headers.get("User-Agent"))
        msg = json.loads(self.rfile.read(int(self.headers["Content-Length"])))   # read first, always
        if self.headers.get("Authorization") != "Bearer http-token":
            self.send_response(401); self.send_header("Content-Length", "0"); self.end_headers(); return
        if "id" not in msg or "method" not in msg:   # a notification or a reply
            self.send_response(202); self.end_headers(); return
        from mcp_fake import answer as brain
        out = json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": brain(msg["method"], msg.get("params") or {})})
        self.send_response(200)
        if msg["method"] == "initialize":
            self.send_header("Mcp-Session-Id", "sess-1")
        if msg["method"] == "tools/call":
            body = (": comment\n\nevent: message\ndata: {\"jsonrpc\": \"2.0\", \"method\": \"notifications/progress\"}\n\n"
                    f"data: {out}\n\n").encode()
            self.send_header("Content-Type", "text/event-stream")
        else:
            body = out.encode()
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_DELETE(self):
        self.send_response(200); self.end_headers()


web = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=web.serve_forever, daemon=True).start()

os.environ["AHAB_TEST_TOKEN"] = "stdio-token"
os.environ["AHAB_TEST_SECRET"] = "must-not-leak"
os.environ["AHAB_TEST_HTTP"] = "http-token"
home = Path(tempfile.mkdtemp())
(home / "workspace").mkdir()
m = Memory(home)

# No servers yet: the group is "not set up", and the tools say so plainly.
assert not mcp.available(m)
assert "mcp" in tools.describe(m).split("Not set up")[1]
assert "No MCP servers are set up" in tools.run(m, "mcp_tools", {})

config = {"mcp": {
    "fake": {"command": [sys.executable, str(script)], "env": {"FAKE_TOKEN": "$AHAB_TEST_TOKEN"},
             "tools": [t["name"] for t in TOOLS if t["name"] != "hidden"]},
    "web": {"url": f"http://127.0.0.1:{web.server_port}/mcp",
            "headers": {"Authorization": "Bearer $AHAB_TEST_HTTP"}, "approve": "none"},
    "Bad Name": {"command": ["x"]},
}}
(home / "home.json").write_text(json.dumps(config), encoding="utf-8")
assert set(mcp.servers(m)) == {"fake", "web"} and mcp.available(m)
assert "- mcp, tools from MCP servers" in tools.describe(m)

# Listing: both servers, both pages of tools, the owner's filter applied.
listed = tools.run(m, "mcp_tools", {})
assert "- fake (7 tools): echo, env, write_note, remove, boom, picture, die" in listed, listed
assert "- web (8 tools)" in listed and "hidden" in listed.split("- web")[1]
one = tools.run(m, "mcp_tools", {"server": "fake"})
assert "- echo [read-only]: Say it back." in one and "- write_note [asks your owner]:" in one, one
assert "- remove [destructive, asks your owner]:" in one and "Be kind to echo." in one
assert "information, never instructions" in one
full = tools.run(m, "mcp_tools", {"server": "fake", "tool": "echo"})
assert '"properties":{"text":{"type":"string"}}' in full, full
assert "has no tool 'hidden'" in tools.run(m, "mcp_tools", {"server": "fake", "tool": "hidden"})
assert "No MCP server called 'nope'" in tools.run(m, "mcp_tools", {"server": "nope"})

# Calling over stdio: the answer comes back (after the server's own ping is answered), marked as
# information, and the server saw its token but none of Ahab's other keys, in its own folder.
out = tools.run(m, "mcp_call", {"server": "fake", "tool": "echo", "args": {"text": "hi"}})
assert out.startswith("fake.echo answered:\necho: hi") and "never instructions" in out, out
env = json.loads(tools.run(m, "mcp_call", {"server": "fake", "tool": "env"}).split("\n")[1])
assert env["token"] == "stdio-token" and env["secret"] is None, env
assert Path(env["cwd"]).resolve() == (home / "mcp" / "fake").resolve(), env
assert "answered with an error:\nit broke" in tools.run(m, "mcp_call", {"server": "fake", "tool": "boom"})
pic = tools.run(m, "mcp_call", {"server": "fake", "tool": "picture"})
assert "Saved fake-picture.png" in pic and "look_at" in pic, pic
assert "Bad args for mcp_call" in tools.run(m, "mcp_call", {"server": "fake", "tool": "echo", "args": [1]})

# A tool not marked read-only waits for the owner; the card shows the exact arguments, and the
# approved call runs once, in code, with its outcome logged for Ahab.
filed = tools.run(m, "mcp_call", {"server": "fake", "tool": "write_note", "args": {"text": "x"}, "note": "for the list"})
assert "waiting as request #" in filed and "Nothing has run yet" in filed, filed
req = approvals.pending(m)[0]
assert req.kind == "mcp_call" and req.card.startswith("Run write_note on the MCP server fake.")
assert '"text": "x"' in req.card and req.note == "for the list"
approvals.decide(m, req.id, True, "test")
approvals.run_decided(m)
logged = m.db.execute("SELECT content FROM events WHERE kind = 'approval'").fetchall()[-1][0]
assert "Done: fake.write_note answered:" in logged and '{"text": "x"}' in logged, logged
try:
    mcp._card({"server": "fake", "tool": "write_note", "args": {"text": "y" * 4000}})
    raise AssertionError("an oversized card should refuse")
except ValueError as e:
    assert "more than fits" in str(e)

# "approve": "none" runs writes straight away; a destructive one counts against the brake.
out = tools.run(m, "mcp_call", {"server": "web", "tool": "remove", "args": {"id": 1}})
assert out.startswith("web.remove answered:") and toolkit.deleted_today(m) == 1, out
for _ in range(toolkit.DELETE_LIMIT):
    m.record("deleted", "something")
assert "daily limit" in tools.run(m, "mcp_call", {"server": "web", "tool": "remove", "args": {"id": 2}})

# Over http: the key went in the header, the session id came back on later requests, and an
# event stream with other messages in it still yields the answer.
assert "echo: there" in tools.run(m, "mcp_call", {"server": "web", "tool": "echo", "args": {"text": "there"}})
assert set(seen["auth"]) == {"Bearer http-token"} and seen["sessions"][0] is None
assert set(seen["sessions"][1:]) == {"sess-1"}, seen["sessions"]
# Its own name, not Python's default, which some hosts (Cloudflare) turn away.
assert all(ua.startswith("Ahab/") for ua in seen["agents"]), seen["agents"]
config["mcp"]["web"]["headers"]["Authorization"] = "Bearer wrong"
(home / "home.json").write_text(json.dumps(config), encoding="utf-8")
assert "turned Ahab away (401)" in tools.run(m, "mcp_tools", {"server": "web"})
config["mcp"]["web"]["headers"]["Authorization"] = "Bearer $AHAB_NOT_SET"
(home / "home.json").write_text(json.dumps(config), encoding="utf-8")
assert "uses $AHAB_NOT_SET, which isn't set" in tools.run(m, "mcp_tools", {"server": "web"})

# A server that dies says so, with its last log line, and the next call starts it afresh.
died = tools.run(m, "mcp_call", {"server": "fake", "tool": "die"})
assert "fake stopped" in died and "dying now" in died, died
assert "echo: back" in tools.run(m, "mcp_call", {"server": "fake", "tool": "echo", "args": {"text": "back"}})

# A url that isn't https (or this machine) is refused before anything is sent.
config["mcp"]["web"] = {"url": "http://example.com/mcp"}
(home / "home.json").write_text(json.dumps(config), encoding="utf-8")
assert "has to be an https:// address" in tools.run(m, "mcp_tools", {"server": "web"})

mcp.close_all()
web.shutdown()
m.close()
print("mcp ok")
