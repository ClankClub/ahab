"""Offline tests for perro/github.py: a fake GitHub (behind a fake relay) stands in for n8n."""
import base64, hashlib, json, os, sys, tempfile
from pathlib import Path
ROOT = str(Path(__file__).resolve().parent.parent)
from pathlib import Path
sys.path.insert(0, ROOT)
os.environ["PERRO_WORKSHOP_HOME"] = tempfile.mkdtemp(prefix="ghws-")
from perro import approvals, github, n8n, tools, workshop
from perro.memory import Memory


def sha(*parts):
    return hashlib.sha1(json.dumps(parts, sort_keys=True).encode()).hexdigest()


def blob_sha(data):
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


repos, blobs, trees, commits, calls = {}, {}, {}, {}, []


def fake(path, payload, limit=None):
    assert path == "perro/github"
    url, method, body = payload["url"][len(github.BASE):], payload["method"], payload.get("body")
    calls.append((method, url))
    if url == "user/repos" and method == "POST":
        assert body["private"] is True and body["auto_init"] is True
        readme = b"# auto\n"
        b = blob_sha(readme)
        blobs[b] = readme
        t = sha("tree", {"README.md": b})
        trees[t] = {"README.md": b}
        c = sha("commit", t, "init")
        commits[c] = t
        repos[body["name"]] = {"name": body["name"], "private": True, "default_branch": "main", "head": c,
                               "html_url": f"https://github.com/perro-agent/{body['name']}",
                               "pushed_at": "2026-10-18T09:00:00Z"}
        return {"status": 201, "body": repos[body["name"]]}
    if url == "user/repos":
        return {"status": 200, "body": list(repos.values())}
    parts = url.split("/")
    assert parts[:2] == ["repos", "perro-agent"], url
    repo = repos.get(parts[2])
    rest = "/".join(parts[3:])
    if repo is None:
        return {"status": 404, "body": {"message": "Not Found"}}
    if rest == "" and method == "GET":
        return {"status": 200, "body": repo}
    if rest == "" and method == "PATCH":
        assert body == {"private": False}
        repo["private"] = False
        return {"status": 200, "body": repo}
    if rest == "git/ref/heads/main":
        return {"status": 200, "body": {"object": {"sha": repo["head"]}}}
    if rest.startswith("git/commits/"):
        return {"status": 200, "body": {"tree": {"sha": commits[parts[-1]]}}}
    if rest.startswith("git/trees/") and method == "GET":
        assert payload["query"] == {"recursive": "1"}
        return {"status": 200, "body": {"tree": [{"path": p, "type": "blob", "sha": s}
                                                 for p, s in trees[parts[-1]].items()]}}
    if rest == "git/blobs":
        data = base64.b64decode(body["content"])
        blobs[blob_sha(data)] = data
        return {"status": 201, "body": {"sha": blob_sha(data)}}
    if rest == "git/trees":
        assert "base_tree" not in body
        entries = {e["path"]: e["sha"] for e in body["tree"]}
        assert all(s in blobs for s in entries.values())
        t = sha("tree", entries)
        trees[t] = entries
        return {"status": 201, "body": {"sha": t}}
    if rest == "git/commits":
        assert body["parents"] == [repo["head"]]
        c = sha("commit", body["tree"], body["message"], body["parents"])
        commits[c] = body["tree"]
        return {"status": 201, "body": {"sha": c}}
    if rest == "git/refs/heads/main":
        assert body["force"] is False and body["sha"] in commits
        repo["head"] = body["sha"]
        return {"status": 200, "body": {}}
    raise AssertionError(f"unexpected {method} {url}")


n8n.send = fake
m = Memory(Path(tempfile.mkdtemp(prefix="ghhome-")))


def run(name, args):
    return tools.run(m, name, args)


# A site, with working papers and build output that must stay out.
site = workshop.site_dir("cafe-site")
for rel, text in {"index.html": "<h1>Cafe</h1>", "src/main.js": "1", "README.md": "# Cafe",
                  "BRIEF.md": "secret brief", "AGENTS.md": "rules", "briefs/001.md": "secret",
                  "dist/index.html": "built", "node_modules/x/i.js": "dep",
                  ".github/workflows/a.yml": "on: push"}.items():
    (site / rel).parent.mkdir(parents=True, exist_ok=True)
    (site / rel).write_text(text, encoding="utf-8")

out = run("push_site", {"name": "cafe-site", "message": "First version"})
assert out.startswith("Pushed cafe-site to perro-agent/cafe-site (private): commit "), out
assert "3 files, 3 new or changed. Made the repository (private)." in out, out
files = trees[commits[repos["cafe-site"]["head"]]]
assert sorted(files) == ["README.md", "index.html", "src/main.js"], files
assert b"secret" not in b"".join(blobs[s] for s in files.values())

calls.clear()
out = run("push_site", {"name": "cafe-site"})
assert out.startswith("Nothing changed since the last push; perro-agent/cafe-site (private)"), out
assert ("POST", "repos/perro-agent/cafe-site/git/blobs") not in calls
assert ("POST", "repos/perro-agent/cafe-site/git/commits") not in calls

(site / "src/main.js").unlink()
(site / "src/app.js").write_text("2")
(site / "index.html").write_text("<h1>Cafe!</h1>")
calls.clear()
out = run("push_site", {"name": "cafe-site", "message": "  Rework\n the   page "})
assert "3 files, 2 new or changed, 1 removed." in out, out
assert calls.count(("POST", "repos/perro-agent/cafe-site/git/blobs")) == 2
assert sorted(trees[commits[repos["cafe-site"]["head"]]]) == ["README.md", "index.html", "src/app.js"]

# Refusals read as refusals.
out = run("push_site", {"name": "Nope!"})
assert out.startswith("Bad args for push_site: name must be"), out
assert run("push_site", {"name": "ghost"}) == "push_site refused: There's no site called 'ghost'. check_build lists them."
workshop._table(m)
m.db.execute("INSERT INTO builds (name, started, brief, status) VALUES ('cafe-site', '2026-10-18T09:00:00', 'b', 'running')")
m.db.commit()
assert run("push_site", {"name": "cafe-site"}) == \
    "push_site refused: cafe-site is being built right now; push it once the build is done."
m.db.execute("UPDATE builds SET status = 'built'")
m.db.commit()
big = site / "big.bin"
big.write_bytes(b"0" * (github.FILE_BYTE_LIMIT + 1))
assert "the limit per file is" in run("push_site", {"name": "cafe-site"})
big.unlink()

out = run("list_repos", {})
assert out.startswith("Your repositories (perro-agent):\ncafe-site: private, last pushed "), out

# Making it public: an approval, for that exact commit.
assert run("request_make_public", {"name": "ghost"}) == \
    "request_make_public refused: There's no repository perro-agent/ghost. push_site makes one."
out = run("request_make_public", {"name": "cafe-site", "note": "Ready to show"})
assert out.startswith("Asked your owner (request #1)."), out
assert "already waiting" in run("request_make_public", {"name": "cafe-site"})
req = approvals.get(m, 1)
head = repos["cafe-site"]["head"]
assert req.card == (f"Make the GitHub repository perro-agent/cafe-site public.\nAnyone on the internet will be able "
                    f"to see every file in it and its whole history, as of commit {head[:7]}. "
                    "https://github.com/perro-agent/cafe-site"), req.card
assert req.payload == {"repo": "cafe-site", "commit": head}

# Something pushed after the request: the approval refuses, the repo stays private.
(site / "README.md").write_text("# Cafe, changed")
run("push_site", {"name": "cafe-site"})
approvals.decide(m, 1, True, "test")
approvals.run_decided(m)
assert repos["cafe-site"]["private"] is True
ev = [e.content for e in m.recent(5) if e.kind == "approval"][-1]
assert "It failed: RuntimeError: perro-agent/cafe-site changed after the request" in ev, ev

# Asked again, approved, nothing pushed between: it goes public.
assert run("request_make_public", {"name": "cafe-site"}).startswith("Asked your owner (request #2).")
approvals.decide(m, 2, True, "test")
approvals.run_decided(m)
assert repos["cafe-site"]["private"] is False
ev = [e.content for e in m.recent(5) if e.kind == "approval"][-1]
assert ev.endswith("Done: perro-agent/cafe-site is public now: https://github.com/perro-agent/cafe-site"), ev
assert run("request_make_public", {"name": "cafe-site"}).startswith("perro-agent/cafe-site is already public")
(site / "README.md").write_text("# Cafe, public")
assert "(PUBLIC)" in run("push_site", {"name": "cafe-site"})

# Bad payloads never make a card.
for bad in ({"repo": "x"}, {"repo": "Bad Name", "commit": "a" * 40}, {"repo": "x", "commit": "zz"}):
    try:
        github._public_card(bad)
        raise SystemExit(f"card accepted {bad}")
    except ValueError:
        pass

# GitHub saying no reads as a refusal.
n8n.send = lambda path, payload, limit=None: {"status": 403, "body": {"message": "The relay refused: nope"}}
assert run("list_repos", {}) == "list_repos refused: GitHub said no (HTTP 403): The relay refused: nope"
assert len(tools.TOOLS) == 73
print("github ok")
