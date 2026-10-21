"""GitHub, in Ahab's own account: where the sites the workshop builds are kept.

Requests go through one n8n workflow, "Ahab: GitHub relay"
(n8n/workflows/ahab-github.json). It holds the sign-in (a token with only
the `repo` scope) and passes on only an allowlist of requests in Ahab's
own account: reading, making a private repository, and adding commits. No
deleting, no collaborators, hooks, keys or secrets, no forced pushes, and no
files under .github/, where GitHub Actions workflows would live (the token
can't write those either). This module is the other half.

What Ahab may do here, on purpose:
- push_site: copy a workshop site's source into a private repository of the
  same name (made on the first push), as one commit. The repository mirrors
  the site, so a file removed from the site is removed there too. The
  briefs, the house rules and the build output stay out: briefs can hold
  the owner's details, and the build output is made again from the source.
- list_repos: what's in its account.
- request_make_public: making a repository public reaches the world, so it's
  an approval (see approvals.py). The owner's card names the repository and
  the exact commit; if anything is pushed after the request, the approval
  refuses to run, so what goes public is what the owner said yes to.

Pushing is commits through GitHub's Git Data API (blobs, a tree, a commit,
then moving the branch), not git itself, so nothing here needs git or a
login on this PC.
"""

from __future__ import annotations

import base64
import hashlib
import re
from datetime import datetime

from ahab import approvals, toolkit, workshop
from ahab.connections import n8n
from ahab.memory import Memory

RELAY = "ahab/github"
BASE = "https://api.github.com/"
# Which account is Ahab's is asked of GitHub (whoever the relay's token
# belongs to), once per process; see owner().
_owner: list[str] = []
# The site's own source only. Briefs and house rules are Ahab's working
# papers (and may hold the owner's details); dist/ is rebuilt from source.
LEFT_OUT = {"briefs", "BRIEF.md", "AGENTS.md", "dist", "node_modules", ".git", ".vite", ".cache"}
FILE_BYTE_LIMIT = 5 * 1024 * 1024
TOTAL_BYTE_LIMIT = 20 * 1024 * 1024
FILE_LIMIT = 500
MESSAGE_CHAR_LIMIT = 200
# A 5 MB file is about 6.7 MB as base64 in JSON.
UPLOAD_BYTE_LIMIT = 8 * 1024 * 1024


class GitHubError(Exception):
    """Something Ahab asked for that can't be done; the message says why."""


def _call(path: str, method: str = "GET", query: dict | None = None, body: dict | None = None):
    payload = {"method": method, "url": BASE + path, "query": query or {}}
    if body is not None:
        payload["body"] = body
    answer = n8n.send(RELAY, payload, limit=UPLOAD_BYTE_LIMIT)
    return answer.get("status") or 0, answer.get("body")


def _github(path: str, method: str = "GET", query: dict | None = None, body: dict | None = None):
    status, result = _call(path, method, query, body)
    if not 200 <= status < 300:
        why = result.get("message") if isinstance(result, dict) else result
        raise n8n.N8nError(f"GitHub said no (HTTP {status}): {str(why or 'no explanation')[:300]}")
    return result


def owner() -> str:
    """The GitHub account the relay signs in as: Ahab's own."""
    if not _owner:
        login = (_github("user") or {}).get("login")
        if not login:
            raise GitHubError("GitHub didn't say which account this is; check the relay's sign-in.")
        _owner.append(str(login))
    return _owner[0]


def _repo_info(name: str) -> dict | None:
    status, result = _call(f"repos/{owner()}/{name}")
    if status == 404:
        return None
    if not 200 <= status < 300:
        raise n8n.N8nError(f"GitHub said no (HTTP {status}): {str(result)[:300]}")
    return result


def _name(value) -> str:
    name = str(value or "").strip().lower()
    if not workshop.NAME.fullmatch(name):
        raise ValueError("name must be a site's name, as check_build lists them.")
    return name


def _site_files(name: str) -> dict[str, bytes]:
    """The site's source files, by repository path."""
    site = workshop.site_dir(name)
    files, total = {}, 0
    for path in sorted(site.rglob("*")):
        rel = path.relative_to(site)
        if any(part in LEFT_OUT for part in rel.parts) or not path.is_file() or path.is_symlink():
            continue
        if rel.parts[0] == ".github":
            continue  # the relay refuses these anyway
        data = path.read_bytes()
        if len(data) > FILE_BYTE_LIMIT:
            raise GitHubError(f"{rel.as_posix()} is {len(data):,} bytes; the limit per file is {FILE_BYTE_LIMIT:,}.")
        total += len(data)
        files[rel.as_posix()] = data
    if total > TOTAL_BYTE_LIMIT:
        raise GitHubError(f"The site's source is {total:,} bytes; the limit is {TOTAL_BYTE_LIMIT:,}.")
    if len(files) > FILE_LIMIT:
        raise GitHubError(f"The site has {len(files)} files; the limit is {FILE_LIMIT}.")
    return files


def _blob_sha(data: bytes) -> str:
    """The id Git gives a file's contents, so unchanged files aren't sent again."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def push_site(memory: Memory, name: str, message: str = "") -> str:
    """Commit a workshop site's source to its private repository."""
    name = _name(name)
    if not workshop.site_dir(name).is_dir():
        raise GitHubError(f"There's no site called {name!r}. check_build lists them.")
    workshop._table(memory)
    latest = memory.db.execute("SELECT status FROM builds WHERE name = ? ORDER BY id DESC LIMIT 1",
                               (name,)).fetchone()
    if latest and latest[0] == "running":
        raise GitHubError(f"{name} is being built right now; push it once the build is done.")
    message = " ".join(str(message or "").split())[:MESSAGE_CHAR_LIMIT] or f"Update {name}"
    files = _site_files(name)
    if not files:
        raise GitHubError(f"{name} has no files to push yet.")

    OWNER = owner()
    repo = _repo_info(name)
    made = repo is None
    if made:
        # auto_init gives the repository a first commit, so there's a branch to build on.
        repo = _github("user/repos", "POST", body={
            "name": name, "private": True, "auto_init": True,
            "description": "A site Ahab had built in its workshop."})
    branch = repo.get("default_branch") or "main"
    head = _github(f"repos/{OWNER}/{name}/git/ref/heads/{branch}")["object"]["sha"]
    head_tree = _github(f"repos/{OWNER}/{name}/git/commits/{head}")["tree"]["sha"]
    old = {e["path"]: e["sha"] for e in _github(f"repos/{OWNER}/{name}/git/trees/{head_tree}",
                                                 query={"recursive": "1"}).get("tree", [])
           if e.get("type") == "blob"}

    tree, sent = [], 0
    for path, data in files.items():
        sha = _blob_sha(data)
        if old.get(path) != sha:
            sha = _github(f"repos/{OWNER}/{name}/git/blobs", "POST", body={
                "content": base64.b64encode(data).decode("ascii"), "encoding": "base64"})["sha"]
            sent += 1
        tree.append({"path": path, "mode": "100644", "type": "blob", "sha": sha})
    removed = sorted(set(old) - set(files))
    # No base tree: the new tree is the whole site, so removed files go.
    new_tree = _github(f"repos/{OWNER}/{name}/git/trees", "POST", body={"tree": tree})["sha"]
    url = f"https://github.com/{OWNER}/{name}"
    visibility = "private" if repo.get("private", True) else "PUBLIC"
    if new_tree == head_tree:
        return f"Nothing changed since the last push; {OWNER}/{name} ({visibility}) is up to date. {url}"
    commit = _github(f"repos/{OWNER}/{name}/git/commits", "POST", body={
        "message": message, "tree": new_tree, "parents": [head]})["sha"]
    _github(f"repos/{OWNER}/{name}/git/refs/heads/{branch}", "PATCH", body={"sha": commit, "force": False})
    what = f"{len(files)} files, {sent} new or changed" + (f", {len(removed)} removed" if removed else "")
    first = " Made the repository (private)." if made else ""
    return f"Pushed {name} to {OWNER}/{name} ({visibility}): commit {commit[:7]}, {what}.{first} {url}"


def list_repos(memory: Memory) -> str:
    OWNER = owner()
    repos = _github("user/repos", query={"affiliation": "owner", "sort": "updated", "per_page": 50})
    if not repos:
        return f"No repositories in {OWNER} yet."
    lines = []
    for r in repos:
        when = datetime.fromisoformat(str(r.get("pushed_at") or r.get("updated_at")).replace("Z", "+00:00"))
        lines.append(f"{r.get('name')}: {'private' if r.get('private') else 'PUBLIC'}, last pushed "
                     f"{when.astimezone():%a %d %b %H:%M}. {r.get('html_url')}")
    return f"Your repositories ({OWNER}):\n" + "\n".join(lines)


# --- making one public: an approval ---------------------------------------

def request_make_public(memory: Memory, name: str, note: str = "") -> str:
    name = _name(name)
    OWNER = owner()
    repo = _repo_info(name)
    if repo is None:
        raise GitHubError(f"There's no repository {OWNER}/{name}. push_site makes one.")
    if not repo.get("private"):
        return f"{OWNER}/{name} is already public: {repo.get('html_url')}"
    branch = repo.get("default_branch") or "main"
    head = _github(f"repos/{OWNER}/{name}/git/ref/heads/{branch}")["object"]["sha"]
    for req in approvals.pending(memory):
        if req.kind == "github_public" and req.payload.get("repo") == name:
            raise GitHubError(f"Request #{req.id} to make {name} public is already waiting on your owner.")
    try:
        request_id = approvals.request(memory, "github_public", {"repo": name, "commit": head, "owner": OWNER}, note)
    except ValueError as e:
        raise GitHubError(str(e)) from None
    return (f"Asked your owner (request #{request_id}). Nothing changes until they approve on their "
            f"phone; you'll see the outcome in your log. Don't push to {name} meanwhile: the approval "
            "is for this exact commit, and a newer one makes it refuse.")


def _public_card(payload: dict) -> str:
    repo, commit = str(payload.get("repo", "")), str(payload.get("commit", ""))
    OWNER = str(payload.get("owner") or "your GitHub account")
    if not workshop.NAME.fullmatch(repo) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("A make-public request needs a repository name and a commit.")
    return (f"Make the GitHub repository {OWNER}/{repo} public.\n"
            f"Anyone on the internet will be able to see every file in it and its whole history, "
            f"as of commit {commit[:7]}. https://github.com/{OWNER}/{repo}")


def _make_public(memory: Memory, payload: dict) -> str:
    repo = payload["repo"]
    OWNER = owner()
    if payload.get("owner") not in (None, OWNER):
        raise RuntimeError(f"The approval was for {payload['owner']}/{repo}, but the relay now signs in as {OWNER}.")
    info = _repo_info(repo)
    if info is None:
        raise RuntimeError(f"{OWNER}/{repo} doesn't exist any more.")
    branch = info.get("default_branch") or "main"
    head = _github(f"repos/{OWNER}/{repo}/git/ref/heads/{branch}")["object"]["sha"]
    if head != payload["commit"]:
        raise RuntimeError(f"{OWNER}/{repo} changed after the request (now at {head[:7]}), so it "
                           "stays private; ask again for the new version.")
    _github(f"repos/{OWNER}/{repo}", "PATCH", body={"private": False})
    return f"{OWNER}/{repo} is public now: https://github.com/{OWNER}/{repo}"


approvals.register("github_public", _public_card, _make_public)


# --- tools (tools.py gathers these; see toolkit.py) ----------------------------------

TOOLS = {
    "list_repos": (
        toolkit.wrap(list_repos, (GitHubError, n8n.N8nError)),
        'List the repositories in your GitHub account, private or public. No args.',
    ),
    "push_site": (
        toolkit.wrap(push_site, (GitHubError, n8n.N8nError)),
        "Save a workshop site's source to a private GitHub repository of the same name in "
        'your own account, made on the first push. Each push is one commit of the '
        'whole site; briefs and build output stay out. '
        'Args: {"name": "<site>", "message": "<what changed>"} (message optional)',
    ),
    "request_make_public": (
        toolkit.wrap(request_make_public, (GitHubError, n8n.N8nError)),
        'Ask your owner to make a repository public, so anyone can see its files and history. '
        'They get a card with Approve and Deny buttons; it happens only if they approve, and '
        'only for the commit it is at now. Args: {"name": "<repo>", "note": "<why, for them>"} '
        '(note optional)',
    ),
}

GROUPS = {
    "github": ("your GitHub repositories", ("list_repos", "push_site", "request_make_public"), n8n.available),
}
