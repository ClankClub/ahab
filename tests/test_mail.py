"""Test perro.mail (send_email) against a fake n8n relay on localhost."""
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, ROOT)
from perro import mail, tools
from perro.memory import Memory

SENT = []
ORIGINAL = {"id": "abc123def", "threadId": "18f2a3b4c5d6e7f8", "payload": {"headers": [
    {"name": "From", "value": "Friend Bot <Friend@Example.com>"},
    {"name": "Subject", "value": "Hello from the other agent"},
    {"name": "Message-ID", "value": "<m1@mail.example.com>"}]}}
STRANGER = {"id": "zzz999zzz", "threadId": "aaaa1111", "payload": {"headers": [
    {"name": "From", "value": "friend@example.com"},
    {"name": "Reply-To", "value": "stranger@example.com"},
    {"name": "Subject", "value": "Re: hi"}]}}


class Fake(BaseHTTPRequestHandler):
    def _answer(self, body):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.headers.get("X-Perro-Key") != "test-key" or not self.path.startswith("/webhook/perro/email/read?"):
            self.send_response(403); self.end_headers(); return
        found = {m["id"]: m for m in (ORIGINAL, STRANGER)}.get(self.path.split("id=")[1])
        self._answer({"message": found} if found else {"error": "No message with that id."})

    def do_POST(self):
        if self.headers.get("X-Perro-Key") != "test-key" or self.path != "/webhook/perro/email/send":
            self.send_response(403); self.end_headers(); return
        SENT.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        self._answer({"id": f"sent{len(SENT):04d}", "threadId": "t1"})

    def log_message(self, *a):
        pass


server = HTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=server.serve_forever, daemon=True).start()
os.environ["PERRO_N8N_URL"] = f"http://127.0.0.1:{server.server_port}/webhook"
os.environ["PERRO_N8N_KEY"] = "test-key"
home = Path(tempfile.mkdtemp())
m = Memory(home)
send = lambda **a: tools.run(m, "send_email", a)

# No list yet: nothing goes out.
assert "hasn't set up who you may write to" in send(to="friend@example.com", subject="Hi", body="x"), SENT
(home / "email.json").write_text(json.dumps({"owner": "Owner@Example.com", "allowed": ["friend@example.com"]}))
assert not SENT

# A plain send: one recipient, owner BCC'd, logged.
out = send(to="Friend <FRIEND@example.com>", subject="  Hi\nthere ", body="Hello.\r\nBye.")
assert "Sent to friend@example.com" in out and "blind copy" in out, out
assert SENT[-1] == {"to": "friend@example.com", "bcc": "owner@example.com", "subject": "Hi there",
                    "body": "Hello.\nBye."}, SENT[-1]
logged = [e for e in m.recent(5) if e.kind == "email_sent"]
assert logged and logged[-1].content.startswith("To friend@example.com: Hi there (id sent0001)\nHello."), logged

# To the owner: no BCC.
send(to="owner@example.com", subject="Note", body="x")
assert "bcc" not in SENT[-1], SENT[-1]

# Strangers, empty fields, too long.
n = len(SENT)
assert "isn't on the list" in send(to="stranger@example.com", subject="Hi", body="x")
assert "isn't one email address" in send(to="friend@example.com, stranger@example.com", subject="Hi", body="x")
assert "needs a subject and a body" in send(to="friend@example.com", subject="Hi", body=" ")
assert "Too long" in send(to="friend@example.com", subject="Hi", body="x" * 20001)
assert "Say who to write to" in send(subject="Hi", body="x")
assert len(SENT) == n

# A reply goes to the sender, in the thread, with Re:.
out = send(reply_to="abc123def", body="Nice to meet you.")
assert SENT[-1] == {"threadId": "18f2a3b4c5d6e7f8", "inReplyTo": "<m1@mail.example.com>", "to": "friend@example.com",
                    "subject": "Re: Hello from the other agent", "body": "Nice to meet you.",
                    "bcc": "owner@example.com"}, SENT[-1]
# A Reply-To pointing at a stranger is refused, as is a bad id.
n = len(SENT)
assert "stranger@example.com isn't on the list" in send(reply_to="zzz999zzz", body="x")
assert "No email with that id" in send(reply_to="nope12345", body="x")
assert "reply_to must be" in send(reply_to="../x", body="x")
assert len(SENT) == n

# The daily limit.
for i in range(mail.DAILY_LIMIT - mail.sent_today(m)):
    assert "Sent to" in send(to="friend@example.com", subject=f"n{i}", body="x")
assert "daily limit" in send(to="friend@example.com", subject="one more", body="x")

# The list lives outside the workspace, so Perro's own file tools can't touch it.
tools.run(m, "write_file", {"path": "../email.json", "content": "{}"})
assert json.loads((home / "email.json").read_text())["allowed"] == ["friend@example.com"]
print("mail ok")
