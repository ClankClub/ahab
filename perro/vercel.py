"""Vercel, in Perro's own account: where the sites the workshop builds go online.

Requests go through one n8n workflow, "Perro: Vercel relay"
(n8n/workflows/perro-vercel.json). It holds the sign-in and passes on only
an allowlist: reading, making a project, uploading files, making a preview
deployment of static files, and promoting one existing deployment to
production. No deleting, domains, aliases, environment variables or
settings, no builds on Vercel, nothing that would run code there (api/,
middleware, vercel.json), and no deployment aimed at production directly.
This module is the other half.

What Perro may do here, on purpose:
- preview_site: put a workshop site's built files (its dist/ folder, from a
  build that passed the check) online as a preview. Previews sit behind
  Vercel's own sign-in (Deployment Protection), so only someone signed in
  to Perro's Vercel account sees them; if a project's protection is off,
  this refuses rather than put a preview on the open internet.
  Vercel always makes a project's first deployment its production one,
  whatever the request says (found the hard way, 2026-10-18), so a new
  project's first deployment is a fixed placeholder page from this module,
  and the site itself only ever arrives as a preview after it.
- list_websites: the projects, their public addresses and recent deployments.
- request_publish: making a site public is an approval (see approvals.py).
  The request names one preview deployment. Deployments never change once
  made, so what the owner opened is what goes live. The approved action
  promotes exactly that deployment to production.

Files go up the way Vercel's own tools send them: the deployment lists every
file by its SHA-1, Vercel answers with the ones it doesn't have yet, and
only those are uploaded. No Git connection: one would turn every push to
the repository into a production deployment, past the approval step.
"""

from __future__ import annotations

import base64
import hashlib
import re
import time
from datetime import datetime, timedelta, timezone

from perro import approvals, n8n, workshop
from perro.memory import Memory

RELAY = "perro/vercel"
BASE = "https://api.vercel.com/"
FILE_BYTE_LIMIT = 5 * 1024 * 1024
TOTAL_BYTE_LIMIT = 20 * 1024 * 1024
FILE_LIMIT = 500
UPLOAD_BYTE_LIMIT = 8 * 1024 * 1024
DAILY_LIMIT = 10
READY_SECONDS = 60
# What a new project's public address shows until the owner approves a site.
PLACEHOLDER = (b'<!doctype html>\n<html lang="en"><meta charset="utf-8">'
               b'<meta name="viewport" content="width=device-width, initial-scale=1">'
               b'<title>Nothing here yet</title>'
               b'<p style="font-family:system-ui,sans-serif;margin:3rem;color:#555">'
               b'Nothing is published here yet.</p></html>\n')
# Files that would make Vercel run code or change settings; the relay refuses them too.
RISKY = re.compile(r"^(api/|\.vercel/|_?middleware\.|(vercel|now|package)\.json$)")
DEPLOYMENT = re.compile(r"dpl_[A-Za-z0-9]{1,64}")
PROJECT_ID = re.compile(r"prj_[A-Za-z0-9]{1,64}")


class VercelError(Exception):
    """Something Perro asked for that can't be done; the message says why."""


def _call(path: str, method: str = "GET", query: dict | None = None, body: dict | None = None):
    answer = n8n.send(RELAY, {"method": method, "url": BASE + path, "query": query or {}, "body": body or {}},
                      limit=UPLOAD_BYTE_LIMIT)
    return answer.get("status") or 0, answer.get("body")


def _why(result) -> str:
    if isinstance(result, dict):
        error = result.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error.get("code") or "no explanation")[:300]
    return str(result or "no explanation")[:300]


def _vercel(path: str, method: str = "GET", query: dict | None = None, body: dict | None = None):
    status, result = _call(path, method, query, body)
    if not 200 <= status < 300:
        raise n8n.N8nError(f"Vercel said no (HTTP {status}): {_why(result)}")
    return result


def _project(name: str) -> dict | None:
    status, result = _call(f"v9/projects/{name}")
    if status == 404:
        return None
    if not 200 <= status < 300:
        raise n8n.N8nError(f"Vercel said no (HTTP {status}): {_why(result)}")
    return result


def _name(value) -> str:
    name = str(value or "").strip().lower()
    if not workshop.NAME.fullmatch(name):
        raise ValueError("name must be a site's name, as check_build lists them.")
    return name


def _protected(project: dict) -> bool:
    return bool(project.get("ssoProtection") or project.get("passwordProtection"))


def _when(ms) -> str:
    return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).astimezone().strftime("%a %d %b %H:%M")


def _production(project: dict) -> str:
    """The site's public address, once it has one."""
    production = (project.get("targets") or {}).get("production") or {}
    aliases = production.get("alias") or []
    host = min(aliases, key=len) if aliases else ""
    return f"https://{host}" if host else ""


def _dist_files(memory: Memory, name: str) -> dict[str, bytes]:
    """The site's built files, by path, from a build that passed the check."""
    workshop._table(memory)
    latest = memory.db.execute("SELECT status FROM builds WHERE name = ? ORDER BY id DESC LIMIT 1",
                               (name,)).fetchone()
    if latest and latest[0] == "running":
        raise VercelError(f"{name} is being built right now; preview it once the build is done.")
    dist = workshop.site_dir(name) / "dist"
    if not latest or latest[0] != "built" or not (dist / "index.html").is_file():
        raise VercelError(f"{name} has no finished build to put online (check_build shows how its "
                          "last one went).")
    files, total = {}, 0
    for path in sorted(dist.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(dist).as_posix()
        if RISKY.match(rel):
            raise VercelError(f"The build has {rel}, which would make Vercel run code or change "
                              "settings; static files only. A new build without it would do.")
        data = path.read_bytes()
        if len(data) > FILE_BYTE_LIMIT:
            raise VercelError(f"{rel} is {len(data):,} bytes; the limit per file is {FILE_BYTE_LIMIT:,}.")
        total += len(data)
        files[rel] = data
    if total > TOTAL_BYTE_LIMIT:
        raise VercelError(f"The build is {total:,} bytes; the limit is {TOTAL_BYTE_LIMIT:,}.")
    if len(files) > FILE_LIMIT:
        raise VercelError(f"The build has {len(files)} files; the limit is {FILE_LIMIT}.")
    return files


def _deployments_today() -> int:
    since = int((datetime.now(timezone.utc) - timedelta(days=1)).timestamp() * 1000)
    found = _vercel("v6/deployments", query={"since": str(since), "limit": "100"})
    return len(found.get("deployments") or [])


def _placeholders(memory: Memory) -> set[str]:
    """The placeholder deployments this module made, so they're never offered as the site."""
    memory.db.execute("CREATE TABLE IF NOT EXISTS vercel_placeholders ("
                      "deployment TEXT PRIMARY KEY, project TEXT NOT NULL, created TEXT NOT NULL)")
    return {row[0] for row in memory.db.execute("SELECT deployment FROM vercel_placeholders")}


def _live(project: dict) -> str:
    return ((project.get("targets") or {}).get("production") or {}).get("id") or ""


def _deploy(name: str, files: dict[str, bytes]) -> tuple[dict, int]:
    """Make a deployment of these files, uploading only what Vercel lacks.
    Returns the deployment, ready unless it took too long or failed, and how
    many files went up."""
    manifest = [{"file": rel, "sha": hashlib.sha1(data).hexdigest(), "size": len(data)}
                for rel, data in files.items()]
    by_sha = {entry["sha"]: files[entry["file"]] for entry in manifest}
    # No target asks for a preview (and the relay refuses any target).
    body = {"name": name, "project": name, "files": manifest, "projectSettings": {"framework": None}}
    query = {"skipAutoDetectionConfirmation": "1"}
    status, result = _call("v13/deployments", "POST", query, body)
    sent = 0
    if status == 400 and isinstance(result, dict) and (result.get("error") or {}).get("code") == "missing_files":
        for sha in result["error"].get("missing") or list(by_sha):
            if sha not in by_sha:
                raise VercelError(f"Vercel asked for a file that isn't in the build ({sha[:7]}).")
            _vercel("v2/files", "POST", body={"data": base64.b64encode(by_sha[sha]).decode("ascii"),
                                              "digest": sha})
            sent += 1
        status, result = _call("v13/deployments", "POST", query, body)
    if not 200 <= status < 300:
        raise n8n.N8nError(f"Vercel said no (HTTP {status}): {_why(result)}")
    deadline = time.monotonic() + READY_SECONDS
    while result.get("readyState") not in ("READY", "ERROR", "CANCELED") and time.monotonic() < deadline:
        time.sleep(3)
        result = _vercel(f"v13/deployments/{result['id']}")
    return result, sent


def preview_site(memory: Memory, name: str) -> str:
    """Put a workshop site's latest build online as a protected preview."""
    name = _name(name)
    if not workshop.site_dir(name).is_dir():
        raise VercelError(f"There's no site called {name!r}. check_build lists them.")
    files = _dist_files(memory, name)
    if _deployments_today() >= DAILY_LIMIT:
        raise VercelError(f"{DAILY_LIMIT} deployments in the last 24 hours already; try again later.")

    project = _project(name)
    made = project is None
    if made:
        project = _vercel("v11/projects", "POST", body={"name": name})
    if not _protected(project):
        raise VercelError(f"The Vercel project {name} has Deployment Protection off, so a preview would be "
                          "public. Nothing was deployed. Your owner can turn on Vercel Authentication in "
                          "the project's settings (Deployment Protection).")
    known = _placeholders(memory)
    if not _live(project):
        # Vercel makes the first deployment production whatever we ask, so it's the placeholder.
        holder, _ = _deploy(name, {"index.html": PLACEHOLDER})
        memory.db.execute("INSERT OR IGNORE INTO vercel_placeholders VALUES (?, ?, ?)",
                          (holder["id"], name, datetime.now(timezone.utc).isoformat()))
        memory.db.commit()
        project = _project(name) or {}
        if _live(project) != holder["id"]:
            raise VercelError(f"Vercel didn't set up {name} the expected way (the placeholder page isn't "
                              "its production deployment), so none of the site was deployed. Tell your owner.")

    result, sent = _deploy(name, files)
    deployment, state = result["id"], result.get("readyState")
    if result.get("target") == "production":
        return (f"Warning: Vercel made this deployment of {name} ({deployment}) its production one, so it "
                f"may be public at {_production(_project(name) or {})}. That shouldn't happen; tell your "
                "owner now.")
    url = f"https://{result.get('url')}"
    what = f"{len(files)} files, {sent} uploaded"
    if made:
        what += f"; made the project, whose public address {_production(project)} shows a placeholder page"
    if state == "ERROR":
        return f"The preview of {name} failed on Vercel's side (deployment {deployment}; {what})."
    if state != "READY":
        return (f"The preview of {name} ({deployment}; {what}) isn't ready after {READY_SECONDS} s; "
                "list_websites shows when it is.")
    return (f"Preview of {name} is up: {url} (deployment {deployment}; {what}). It's behind Vercel's "
            "sign-in, so only someone signed in to your Vercel account can open it. request_publish "
            "would make exactly this deployment the public site.")


def list_websites(memory: Memory, name: str = "") -> str:
    known = _placeholders(memory)
    if name:
        name = _name(name)
        project = _project(name)
        if project is None:
            return f"There's no Vercel project called {name}. preview_site makes one."
        found = _vercel("v6/deployments", query={"projectId": project["id"], "limit": "10"})
        public, live = _production(project), _live(project)
        showing = "a placeholder page" if live in known else "the published site"
        lines = [f"{name}: " + (f"{public} shows {showing}" if public else "no public address yet")
                 + ("" if _protected(project) else ". Previews are NOT protected.")]
        for d in found.get("deployments") or []:
            uid = d.get("uid")
            kind = "placeholder" if uid in known else ("public" if uid == live else "preview")
            lines.append(f"- {uid}: {kind}, {str(d.get('state') or d.get('readyState')).lower()}, "
                         f"{_when(d.get('created') or d.get('createdAt'))}, https://{d.get('url')}")
        return "\n".join(lines)
    projects = _vercel("v9/projects", query={"limit": "50"}).get("projects") or []
    if not projects:
        return "No websites on Vercel yet. preview_site puts a workshop site online as a preview."
    lines = []
    for p in projects:
        public = _production(p)
        status = (f"{public} shows " + ("a placeholder page" if _live(p) in known else "the published site")
                  if public else "no public address yet")
        lines.append(f"{p.get('name')}: {status}, last changed {_when(p.get('updatedAt') or p.get('createdAt'))}")
    return "Your websites on Vercel:\n" + "\n".join(lines)


# --- making one public: an approval ---------------------------------------

def request_publish(memory: Memory, name: str, deployment: str = "", note: str = "") -> str:
    name = _name(name)
    project = _project(name)
    if project is None:
        raise VercelError(f"There's no Vercel project called {name}. preview_site makes one.")
    deployment = str(deployment or "").strip()
    if deployment:
        if not DEPLOYMENT.fullmatch(deployment):
            raise ValueError("deployment must be a deployment id (dpl_...), as list_websites shows them.")
    else:
        found = _vercel("v6/deployments", query={"projectId": project["id"], "limit": "20"})
        known = _placeholders(memory)
        ready = [d for d in found.get("deployments") or []
                 if (d.get("state") or d.get("readyState")) == "READY" and d.get("uid") not in known]
        if not ready:
            raise VercelError(f"{name} has no ready preview to publish. preview_site makes one.")
        deployment = ready[0]["uid"]
    found = _vercel(f"v13/deployments/{deployment}")
    if found.get("projectId") != project["id"]:
        raise VercelError(f"{deployment} isn't a deployment of {name}.")
    if found.get("readyState") != "READY":
        raise VercelError(f"{deployment} isn't ready ({str(found.get('readyState')).lower()}).")
    if deployment in _placeholders(memory):
        raise VercelError(f"{deployment} is the placeholder page, not the site.")
    if _live(project) == deployment:
        return f"{deployment} is already the public site: {_production(project)}"
    for req in approvals.pending(memory):
        if req.kind == "vercel_publish" and req.payload.get("project") == name:
            raise VercelError(f"Request #{req.id} to publish {name} is already waiting on your owner.")
    payload = {"project": name, "project_id": project["id"], "deployment": deployment,
               "url": f"https://{found.get('url')}", "public": _production(project)}
    try:
        request_id = approvals.request(memory, "vercel_publish", payload, note)
    except ValueError as e:
        raise VercelError(str(e)) from None
    return (f"Asked your owner (request #{request_id}) to publish {deployment}. Nothing changes until "
            "they approve on their phone; you'll see the outcome in your log. New previews meanwhile "
            "don't change what the request publishes.")


def _publish_card(payload: dict) -> str:
    name, deployment = str(payload.get("project", "")), str(payload.get("deployment", ""))
    url, public = str(payload.get("url", "")), str(payload.get("public", ""))
    if (not workshop.NAME.fullmatch(name) or not DEPLOYMENT.fullmatch(deployment)
            or not PROJECT_ID.fullmatch(str(payload.get("project_id", "")))
            or not re.fullmatch(r"https://[a-z0-9.-]+\.vercel\.app", url)
            or (public and not re.fullmatch(r"https://[a-z0-9.-]+", public))):
        raise ValueError("A publish request needs a project, a deployment and its preview address.")
    where = f"at {public}, replacing what's there now" if public else "at its vercel.app address"
    return (f"Publish the website {name} on Vercel, {where}.\n"
            f"Anyone on the internet will be able to visit it. It will show exactly the preview at {url} "
            f"(deployment {deployment}); open that first to see it.")


def _publish(memory: Memory, payload: dict) -> str:
    name, project_id, deployment = payload["project"], payload["project_id"], payload["deployment"]
    found = _vercel(f"v13/deployments/{deployment}")
    if found.get("projectId") != project_id or found.get("readyState") != "READY":
        raise RuntimeError(f"{deployment} is no longer a ready deployment of {name}, so nothing was published.")
    _vercel(f"v10/projects/{project_id}/promote/{deployment}", "POST", body={})
    deadline = time.monotonic() + READY_SECONDS
    while time.monotonic() < deadline:
        project = _project(name) or {}
        if _live(project) == deployment:
            return f"{name} is public now: {_production(project) or payload['url']}"
        time.sleep(3)
    return f"Vercel accepted publishing {deployment}; it's still switching over. list_websites shows the address."


approvals.register("vercel_publish", _publish_card, _publish)
