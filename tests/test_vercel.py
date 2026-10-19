"""Offline tests for perro/vercel.py: a fake Vercel, behind a fake relay, that behaves like the real one
(including making a project's first deployment production, whatever is asked)."""
import base64, hashlib, os, sys, tempfile
from pathlib import Path
ROOT = str(Path(__file__).resolve().parent.parent)
from pathlib import Path
sys.path.insert(0, ROOT)
os.environ["PERRO_WORKSHOP_HOME"] = tempfile.mkdtemp(prefix="vcws-")
from perro import approvals, n8n, tools, vercel, workshop
from perro.memory import Memory

vercel.time.sleep = lambda s: None
projects, deployments, stored, calls = {}, {}, {}, []
protect = {"on": True}
fail_next = []
counter = [0]


def fake(path, payload, limit=None):
    assert path == "perro/vercel" and limit == vercel.UPLOAD_BYTE_LIMIT
    url, method, body, query = payload["url"][len(vercel.BASE):], payload["method"], payload["body"], payload["query"]
    calls.append((method, url))
    if url == "v9/projects" and method == "GET":
        return {"status": 200, "body": {"projects": list(projects.values())}}
    if url == "v11/projects":
        assert set(body) == {"name"}
        projects[body["name"]] = {"id": f"prj_{body['name'].replace('-', '')}", "name": body["name"],
                                  "ssoProtection": {"deploymentType": "all_except_custom_domains"} if protect["on"] else None,
                                  "passwordProtection": None, "createdAt": 1792325985634, "updatedAt": 1792325985634}
        return {"status": 200, "body": projects[body["name"]]}
    if url.startswith("v9/projects/"):
        p = projects.get(url.split("/")[2])
        return {"status": 200, "body": p} if p else {"status": 404, "body": {"error": {"code": "not_found", "message": "Project not found"}}}
    if url == "v2/files":
        data = base64.b64decode(body["data"])
        assert hashlib.sha1(data).hexdigest() == body["digest"]
        stored[body["digest"]] = data
        return {"status": 200, "body": {"urls": []}}
    if url == "v13/deployments" and method == "POST" and "deploymentId" in body:
        # Publishing: the same files as an existing deployment, as production. Like the
        # real one, Vercel moves the public address to it once it's ready.
        assert set(body) == {"name", "project", "deploymentId", "target", "projectSettings"}, body
        assert body["target"] == "production" and body["projectSettings"] == {"framework": None}
        src = deployments[body["deploymentId"]]
        counter[0] += 1
        did = f"dpl_D{counter[0]}"
        deployments[did] = dict(src, id=did, uid=did, url=f"{body['name']}-h{counter[0]}-perro-agent.vercel.app",
                                readyState="QUEUED", target="production", created=1792325985634 + counter[0])
        return {"status": 200, "body": {k: v for k, v in deployments[did].items() if k != "files"}}
    if url == "v13/deployments" and method == "POST":
        assert "target" not in body and body["projectSettings"] == {"framework": None} and body["project"] == body["name"]
        missing = [f["sha"] for f in body["files"] if f["sha"] not in stored]
        if missing:
            return {"status": 400, "body": {"error": {"code": "missing_files", "message": "Missing files", "missing": missing}}}
        p = projects[body["name"]]
        counter[0] += 1
        did = f"dpl_D{counter[0]}"
        first = "targets" not in p
        d = {"id": did, "uid": did, "projectId": p["id"], "url": f"{body['name']}-h{counter[0]}-perro-agent.vercel.app",
             "readyState": "QUEUED", "target": "production" if first else None, "created": 1792325985634 + counter[0],
             "files": {f["file"]: stored[f["sha"]] for f in body["files"]}}
        deployments[did] = d
        if first:
            p["targets"] = {"production": {"id": did, "alias": [f"{body['name']}.vercel.app",
                                                                f"{body['name']}-perro-agent.vercel.app"]}}
        return {"status": 200, "body": dict(d)}
    if url.startswith("v13/deployments/"):
        d = deployments[url.split("/")[2]]
        if d["readyState"] == "QUEUED":
            d["readyState"] = fail_next.pop() if fail_next else "READY"
            if d["readyState"] == "READY" and d["target"] == "production":
                next(p for p in projects.values() if p["id"] == d["projectId"])["targets"]["production"]["id"] = d["id"]
        return {"status": 200, "body": dict(d)}
    if url == "v6/deployments":
        found = [d for d in deployments.values() if "projectId" not in query or d["projectId"] == query["projectId"]]
        return {"status": 200, "body": {"deployments": [dict(d, state=d["readyState"]) for d in reversed(found)]}}
    if "/promote/" in url:
        # The real Vercel refuses to promote a preview, with nothing more to say.
        _, _, pid, _, did = url.split("/")
        if deployments[did]["target"] != "production":
            return {"status": 422, "body": {"error": {"message": "Resource cannot be processed"}}}
        raise AssertionError("promote isn't used")
    raise AssertionError(f"unexpected {method} {url}")


n8n.send = fake
m = Memory(Path(tempfile.mkdtemp(prefix="vchome-")))


def run(name, args):
    return tools.run(m, name, args)


def built(name, files, status="built"):
    site = workshop.site_dir(name)
    for rel, text in files.items():
        (site / rel).parent.mkdir(parents=True, exist_ok=True)
        (site / rel).write_text(text, encoding="utf-8")
    workshop._table(m)
    m.db.execute("INSERT INTO builds (name, started, brief, status) VALUES (?, '2026-10-18T09:00:00', 'b', ?)", (name, status))
    m.db.commit()


def public_page(name):
    p = projects[name]
    return deployments[p["targets"]["production"]["id"]]["files"]


# Refusals before anything reaches Vercel.
assert run("preview_site", {"name": "ghost"}) == "preview_site refused: There's no site called 'ghost'. check_build lists them."
built("cafe", {"index.html": "<h1>draft</h1>", "BRIEF.md": "brief"}, status="failed")
assert "has no finished build" in run("preview_site", {"name": "cafe"})
built("cafe", {"dist/index.html": "<h1>Cafe</h1>", "dist/assets/a.js": "1"}, status="running")
assert "being built right now" in run("preview_site", {"name": "cafe"})
m.db.execute("UPDATE builds SET status = 'built' WHERE status = 'running'")
m.db.commit()
assert calls == [], calls

# First preview: the project, its placeholder as production, then the site as a preview.
out = run("preview_site", {"name": "cafe"})
assert out.startswith("Preview of cafe is up: https://cafe-h2-perro-agent.vercel.app (deployment dpl_D2; 2 files, 2 uploaded; "
                      "made the project, whose public address https://cafe.vercel.app shows a placeholder page)."), out
assert public_page("cafe") == {"index.html": vercel.PLACEHOLDER}, "the public page must be the placeholder"
assert deployments["dpl_D2"]["target"] is None and set(deployments["dpl_D2"]["files"]) == {"index.html", "assets/a.js"}
assert b"brief" not in b"".join(public_page("cafe").values())

# Again, unchanged: nothing uploaded, still a preview.
calls.clear()
out = run("preview_site", {"name": "cafe"})
assert "(deployment dpl_D3; 2 files, 0 uploaded)" in out, out
assert ("POST", "v2/files") not in calls
assert public_page("cafe") == {"index.html": vercel.PLACEHOLDER}

out = run("list_websites", {})
assert out.startswith("Your websites on Vercel:\ncafe: https://cafe.vercel.app shows a placeholder page, last changed "), out
out = run("list_websites", {"name": "cafe"})
assert out.splitlines()[0] == "cafe: https://cafe.vercel.app shows a placeholder page", out
assert "- dpl_D1: placeholder, ready" in out and "- dpl_D3: preview, ready" in out, out

# Things that would run code on Vercel stay off it.
(workshop.site_dir("cafe") / "dist/api").mkdir()
(workshop.site_dir("cafe") / "dist/api/x.js").write_text("export default () => 1")
assert "api/x.js, which would make Vercel run code" in run("preview_site", {"name": "cafe"})
(workshop.site_dir("cafe") / "dist/api/x.js").unlink()

# Protection off: refused before anything is deployed.
protect["on"] = False
built("open-site", {"dist/index.html": "<h1>Open</h1>"})
before = len(deployments)
assert "Deployment Protection off, so a preview would be public. Nothing was deployed." in run("preview_site", {"name": "open-site"})
assert len(deployments) == before
protect["on"] = True

# Publishing: an approval for the latest real preview, never the placeholder.
assert "placeholder page, not the site" in run("request_publish", {"name": "cafe", "deployment": "dpl_D1"})
assert run("request_publish", {"name": "cafe", "deployment": "nope"}).startswith("Bad args for request_publish")
out = run("request_publish", {"name": "cafe", "note": "Looks done"})
assert out.startswith("Asked your owner (request #1) to publish dpl_D3."), out
assert "already waiting" in run("request_publish", {"name": "cafe"})
req = approvals.get(m, 1)
assert req.payload == {"project": "cafe", "project_id": "prj_cafe", "deployment": "dpl_D3",
                       "url": "https://cafe-h3-perro-agent.vercel.app", "public": "https://cafe.vercel.app"}, req.payload
assert req.card == ("Publish the website cafe on Vercel, at https://cafe.vercel.app, replacing what's there now.\n"
                    "Anyone on the internet will be able to visit it. It will show exactly the preview at "
                    "https://cafe-h3-perro-agent.vercel.app (deployment dpl_D3); open that first to see it."), req.card

# A newer preview meanwhile doesn't change what gets published.
(workshop.site_dir("cafe") / "dist/index.html").write_text("<h1>Cafe v2</h1>")
run("preview_site", {"name": "cafe"})
approvals.decide(m, 1, True, "test")
approvals.run_decided(m)
ev = [e.content for e in m.recent(5) if e.kind == "approval"][-1]
assert ev.endswith("Done: cafe is public now: https://cafe.vercel.app (deployment dpl_D5, from dpl_D3)"), ev
assert projects["cafe"]["targets"]["production"]["id"] == "dpl_D5"
assert public_page("cafe")["index.html"] == b"<h1>Cafe</h1>", "the approved preview's files, not the newer one's"
assert ("POST", "v10/projects/prj_cafe/promote/dpl_D3") not in calls
assert run("request_publish", {"name": "cafe", "deployment": "dpl_D5"}).startswith("dpl_D5 is already the public site")
out = run("list_websites", {"name": "cafe"})
assert out.splitlines()[0] == "cafe: https://cafe.vercel.app shows the published site", out
assert "- dpl_D5: public, ready" in out and "- dpl_D3: preview, ready" in out, out

# Bad payloads never make a card.
for bad in ({"project": "cafe"}, {"project": "cafe", "project_id": "prj_x", "deployment": "dpl_1", "url": "https://evil.com"},
            {"project": "Bad Name", "project_id": "prj_x", "deployment": "dpl_1", "url": "https://a.vercel.app"}):
    try:
        vercel._publish_card(bad)
        raise SystemExit(f"card accepted {bad}")
    except ValueError:
        pass

# A deployment gone bad before the run: nothing published.
run("preview_site", {"name": "cafe"})
assert run("request_publish", {"name": "cafe"}).startswith("Asked your owner (request #2)")
deployments[approvals.get(m, 2).payload["deployment"]]["projectId"] = "prj_other"
approvals.decide(m, 2, True, "test")
approvals.run_decided(m)
ev = [e.content for e in m.recent(5) if e.kind == "approval"][-1]
assert "It failed: RuntimeError:" in ev and "nothing was published" in ev, ev
assert projects["cafe"]["targets"]["production"]["id"] == "dpl_D5"

# Vercel's build of the production copy failing: said plainly, public site unchanged.
run("preview_site", {"name": "cafe"})
assert run("request_publish", {"name": "cafe"}).startswith("Asked your owner (request #3)")
fail_next.append("ERROR")
approvals.decide(m, 3, True, "test")
approvals.run_decided(m)
ev = [e.content for e in m.recent(5) if e.kind == "approval"][-1]
assert "It failed: RuntimeError:" in ev and "ended error" in ev and "public site is unchanged" in ev, ev
assert projects["cafe"]["targets"]["production"]["id"] == "dpl_D5"
out = run("list_websites", {"name": "cafe"})
assert "- dpl_D8: publish attempt, error" in out and "- dpl_D5: public, ready" in out, out

# Vercel saying no reads as a refusal.
n8n.send = lambda path, payload, limit=None: {"status": 403, "body": {"error": {"message": "The relay refused: nope"}}}
assert run("list_websites", {}) == "list_websites refused: Vercel said no (HTTP 403): The relay refused: nope"
assert len(tools.TOOLS) == 63
print("vercel ok")
