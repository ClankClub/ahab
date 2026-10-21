"""A spare model behind the usual one, and plain words when a sign-in lapses."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, ROOT)
from ahab import cli_model, heartbeat
from ahab.memory import Memory
from ahab.model import AuthError, FallbackClient, ModelClient, ModelError, Reply, with_fallback


class Fake:
    """A model that answers from a list; a ModelError in the list is raised."""

    def __init__(self, backend, replies, can_see=True):
        self.backend, self.replies, self.calls, self.can_see = backend, list(replies), 0, can_see

    def complete(self, messages, temperature=0.7, json_mode=False):
        self.calls += 1
        item = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(item, Exception):
            raise item
        return Reply(item, self.backend, 10, 5)


class Clock:
    now = 1000.0

    def __call__(self):
        return self.now


ask = [{"role": "user", "content": "hi"}]

# 1. The usual model answers: the spare is never touched.
clock = Clock()
usual, spare = Fake("usual", ["ok"]), Fake("spare", ["spare ok"])
c = FallbackClient(usual, spare, retry_seconds=600, clock=clock)
assert c.complete(ask).text == "ok" and spare.calls == 0 and c.failovers == [] and c.backend == "usual"

# 2. It fails: the spare answers, one note is kept, and the client now reads as the spare.
usual.replies = [ModelError("codex took longer than 1800 seconds.")]
assert c.complete(ask).text == "spare ok"
assert len(c.failovers) == 1 and "usual" in c.failovers[0] and "spare" in c.failovers[0], c.failovers
assert "took longer" in c.failovers[0]
assert c.backend == "spare" and c.can_see is True and "usual" in c.failover_note

# 3. While it's cooling off the usual model isn't asked again (a dead login would cost every call).
before = usual.calls
clock.now += 599
c.complete(ask)
assert usual.calls == before and len(c.failovers) == 1

# 4. After the pause it's tried again, and when it answers the client is back to normal.
clock.now += 2
usual.replies = ["back"]
assert c.complete(ask).text == "back" and c.backend == "usual" and c.failover_note is None

# 5. Fails again and the spare fails too: one error that names both.
usual.replies, spare.replies = [AuthError("signed out")], [ModelError("rate limited")]
clock.now += 10_000
try:
    c.complete(ask)
    raise SystemExit("both failing should raise")
except ModelError as e:
    assert "usual: signed out" in str(e) and "spare: rate limited" in str(e), e

# 6. Only a failed backend counts. A bug in the reply handling isn't the model being down.
usual.replies = [ValueError("not a backend problem")]
clock.now += 10_000
try:
    c.complete(ask)
    raise SystemExit("a ValueError should pass straight through")
except ValueError:
    pass

# 7. with_fallback: nothing named, the same one named, an unknown name, a real spare.
primary = Fake("usual", ["x"])
assert with_fallback(primary, None) is primary and with_fallback(primary, "usual") is primary
out = io.StringIO()
with contextlib.redirect_stdout(out):
    assert with_fallback(primary, "no-such-backend") is primary
assert "Running without a fallback" in out.getvalue()
both = with_fallback(primary, "local")
assert isinstance(both, FallbackClient) and both.fallback.backend == "local"

# 8. An API that refuses the key says so as an AuthError; a bad request is still a plain ModelError.
def refuse(code):
    def urlopen(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, code, "no", {}, io.BytesIO(b'{"error":"nope"}'))
    return urlopen


real_urlopen = urllib.request.urlopen
api = ModelClient("http://x.invalid/v1", "m", "key", max_attempts=1)
try:
    urllib.request.urlopen = refuse(401)
    try:
        api.complete(ask)
        raise SystemExit("401 should raise")
    except AuthError as e:
        assert "401" in str(e)
    urllib.request.urlopen = refuse(400)
    try:
        api.complete(ask)
        raise SystemExit("400 should raise")
    except ModelError as e:
        assert not isinstance(e, AuthError)
finally:
    urllib.request.urlopen = real_urlopen

# 9. The CLIs' sign-in wording is recognised, and the error says how to renew it.
assert cli_model.is_auth_failure("OAuth token has expired. Please run /login")
assert cli_model.is_auth_failure("Invalid API key · Please run /login")
assert cli_model.is_auth_failure("refresh token was already used")
assert not cli_model.is_auth_failure("overloaded_error: try again later")

profile = Path(tempfile.mkdtemp())
os.environ["AHAB_CLI_HOME"] = str(profile)
os.environ["AHAB_CLAUDE_BIN"] = os.environ["AHAB_CODEX_BIN"] = "fake-cli"
real_run = subprocess.run


def cli_says(stdout="", stderr="", code=0):
    def run(*args, **kwargs):
        return subprocess.CompletedProcess(args, code, stdout, stderr)
    return run


def result(text, is_error=False):
    return json.dumps({"type": "result", "is_error": is_error, "result": text, "usage": {}})


claude = cli_model.CliClient("claude", "haiku")
codex = cli_model.CliClient("codex", "gpt-x")
try:
    # A failed run with the expired-login wording on stdout.
    subprocess.run = cli_says(stdout="OAuth token has expired. Please run /login", code=1)
    for client, command, env in ((claude, "claude auth login", "CLAUDE_CONFIG_DIR"),
                                 (codex, "codex login", "CODEX_HOME")):
        try:
            client.complete(ask)
            raise SystemExit("an expired login should raise")
        except AuthError as e:
            assert command in str(e) and env in str(e) and str(profile) in str(e), e
    # Claude reports it as an error result with exit 0.
    subprocess.run = cli_says(stdout=result("Invalid API key · Please run /login", True))
    try:
        claude.complete(ask)
        raise SystemExit("an auth error result should raise")
    except AuthError:
        pass
    # Another error result is just a failure, not a sign-in problem.
    subprocess.run = cli_says(stdout=result("overloaded_error", True))
    try:
        claude.complete(ask)
        raise SystemExit("an error result should raise")
    except ModelError as e:
        assert not isinstance(e, AuthError)
    # Codex's own error event for a rejected token.
    subprocess.run = cli_says(stdout=json.dumps({"type": "error", "message": "401 Unauthorized: token expired"}))
    try:
        codex.complete(ask)
        raise SystemExit("a rejected token should raise")
    except AuthError:
        pass
    # An answer that happens to talk about oauth is an answer.
    subprocess.run = cli_says(stdout=result("You could use OAuth for that."))
    assert claude.complete(ask).text == "You could use OAuth for that."
finally:
    subprocess.run = real_run

# 10. In a wake-up: the usual model fails, the spare carries the wake, the log and the page say so.
home = Path(tempfile.mkdtemp())
m = Memory(home)
m.write_notes("Open: nothing.")
prompt = heartbeat.system_prompt("Ahab")
done = json.dumps({"thought": "-", "message": None, "notes": None, "next_wake_minutes": 600})
usual = Fake("usual", [AuthError("Ahab's codex sign-in has expired or is missing.")])
spare = Fake("spare", [done])
seen = []
spare_complete = spare.complete
spare.complete = lambda messages, *a, **k: (seen.append(messages[-1]["content"]), spare_complete(messages, *a, **k))[1]
c = FallbackClient(usual, spare, retry_seconds=600, clock=Clock())
assert heartbeat.beat(m, c, prompt) == 600
events = [e for e in m.recent(200) if e.kind == "model_fallback"]
assert len(events) == 1 and "sign-in has expired" in events[0].content and "spare" in events[0].content
assert not [e for e in m.recent(200) if e.kind == "error"], "the wake itself should have worked"
assert c.failovers == [], "logged notes are taken off the client"
heartbeat.beat(m, c, prompt)
assert "Your usual model (usual) isn't answering" in seen[-1], seen[-1][:400]
assert len([e for e in m.recent(300) if e.kind == "model_fallback"]) == 1, "no second note while it cools off"

print("fallback ok")
