"""Supabase tools (supabase.py): the requests they build, the fences, and the group's setup check."""
import json, os, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ahab import supabase, tools
from ahab.memory import Memory

KEY = "sb-secret-test-key"
os.environ[supabase.URL_ENV] = "https://abc.supabase.co/"
os.environ[supabase.KEY_ENV] = KEY
home = Path(tempfile.mkdtemp())
(home / "workspace").mkdir()
m = Memory(home)

calls = []
state = {"rows": [{"id": 1, "title": "hi"}], "count": 3}


def fake(method, url, body=None, headers=None):
    calls.append((method, url, body, headers or {}))
    path = url.split("/rest/v1/", 1)[1]
    if path == "":
        spec = {"definitions": {"notes": {"properties": {"id": {"format": "bigint", "type": "integer"},
                                                         "title": {"type": "string"}}}}}
        return 200, {}, json.dumps(spec).encode()
    if method == "GET" and "limit=1" in path and (headers or {}).get("Prefer") == "count=exact":
        return 200, {"Content-Range": f"0-0/{state['count']}"}, b"[]"
    if method == "GET":
        return 200, {}, json.dumps(state["rows"]).encode()
    if method in ("POST", "PATCH", "DELETE"):
        return 200, {}, json.dumps(state["rows"]).encode()


supabase._request = fake

# Reading: the table list, and a query whose filter and order end up in the address, quoted.
assert "- notes: id (bigint), title (string)" in tools.run(m, "supabase_tables", {})
out = tools.run(m, "supabase_query", {"table": "notes", "filter": {"title": "ilike.*a b*"}, "order": "id.desc", "limit": 500})
method, url, _, _ = calls[-1]
assert method == "GET" and url.startswith("https://abc.supabase.co/rest/v1/notes?select=*&title=ilike.*a%20b*&order=id.desc&limit=100"), url
assert "1 row(s) from notes" in out and '"title": "hi"' in out

# Writing.
assert "Added 1 row(s)" in tools.run(m, "supabase_insert", {"table": "notes", "rows": {"title": "x"}})
assert calls[-1][0] == "POST" and calls[-1][2] == [{"title": "x"}] and calls[-1][3]["Prefer"] == "return=representation"
assert "Changed 1 row(s)" in tools.run(m, "supabase_update", {"table": "notes", "filter": {"id": "eq.1"}, "values": {"title": "y"}})
assert calls[-1][0] == "PATCH" and calls[-1][1].endswith("notes?id=eq.1")

# Fences: no filter, bad names, bad operators, too many rows.
for bad in ({"table": "notes", "values": {"a": 1}}, {"table": "notes", "filter": {}, "values": {"a": 1}},
            {"table": "notes;drop", "filter": {"id": "eq.1"}, "values": {"a": 1}},
            {"table": "notes", "filter": {"id": "5"}, "values": {"a": 1}},
            {"table": "notes", "filter": {"id": "foo.5"}, "values": {"a": 1}},
            {"table": "../auth", "filter": {"id": "eq.1"}, "values": {"a": 1}}):
    n = len(calls)
    assert tools.run(m, "supabase_update", bad)       # refused with a message
    assert len(calls) == n, bad            # nothing was sent
assert len(calls) == n
assert "At most 50" in tools.run(m, "supabase_insert", {"table": "notes", "rows": [{"a": 1}] * 51})

# Deleting: needs a filter, counts first, refuses a big match, logs one deleted event, and is braked.
n = len(calls)
assert "Deleted 1 row(s)" in tools.run(m, "supabase_delete", {"table": "notes", "filter": {"id": "eq.1"}})
assert [c[0] for c in calls[n:]] == ["GET", "DELETE"] and calls[-1][1].endswith("notes?id=eq.1")
assert tools.deleted_today(m) == 1
state["count"] = 26
n = len(calls)
assert "at most 25" in tools.run(m, "supabase_delete", {"table": "notes", "filter": {"id": "gt.0"}})
assert [c[0] for c in calls[n:]] == ["GET"]
state["count"] = 1
for _ in range(tools.DELETE_LIMIT):
    m.record("deleted", "x")
assert "daily limit" in tools.run(m, "supabase_delete", {"table": "notes", "filter": {"id": "eq.2"}})

# An HTTP error comes back as a plain message, and the key never appears in it.
supabase._request = lambda *a, **k: (401, {}, json.dumps({"message": "Invalid API key"}).encode())
err = tools.run(m, "supabase_query", {"table": "notes"})
assert "401" in err and "Invalid API key" in err and KEY not in err

# Setup: the group shows up only with both variables, and a plain-http address is refused.
assert tools._available(m, "supabase")
os.environ.pop(supabase.KEY_ENV)
assert not tools._available(m, "supabase")
assert "isn't set up" in tools.run(m, "supabase_tables", {})
os.environ[supabase.KEY_ENV] = KEY
os.environ[supabase.URL_ENV] = "http://example.com"
assert "https://" in tools.run(m, "supabase_tables", {})
print("supabase ok")
