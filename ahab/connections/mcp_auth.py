"""MCP logins: OAuth for http servers that want the owner to sign in (mcp.py uses this).

A server that wants a login turns the first request away (401). Then, all in code:

  1. Find its login service. The 401 names the server's "protected resource metadata", which
     names an authorization server, whose own metadata gives the addresses to use
     (RFC 9728, RFC 8414). Older servers skip the first step; their own address is the service.
  2. Register Ahab there as an app, if this server hasn't seen it yet (dynamic client
     registration, RFC 7591), or use a client_id the owner put in home.json.
  3. Make a one-time secret (the PKCE verifier) and a random state, keep both here, and send
     the owner a link on Telegram. The link carries only a hash of the secret.
  4. The owner taps it, signs in and allows. The login page sends their browser to the
     callback, a small public function in Ahab's Supabase project (supabase/functions/
     ahab-oauth), which files the code and state in the ahab_oauth table.
  5. The heartbeat, while it sleeps, notices the code within seconds, swaps it (with the
     secret) for a token, and wakes Ahab to say it's connected. A code without the secret is
     worthless, so passing it through a public page gives nobody anything.
  6. Each request carries the token; an expired one is renewed with its refresh token.

The link is written by code, never by Ahab's model, so a page Ahab read can't get a login link
of its choosing sent to the owner. Tokens are kept outside the home and outside the workspace,
in AUTH_DIR (the folder only Ahab's user can read), never in the repo.

The callback is AHAB_MCP_CALLBACK, or the ahab-oauth function in the project AHAB_SUPABASE_URL
names. SETUP_SQL makes its table; deploy the function with JWT verification off.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from ahab.web import USER_AGENT

CALLBACK_ENV = "AHAB_MCP_CALLBACK"
DIR_ENV = "AHAB_MCP_AUTH_DIR"
TABLE = "ahab_oauth"
LOGIN_MINUTES = 15        # how long a link stays good
POLL_SECONDS = 5          # how often a waiting login looks for its code
TIMEOUT = 30
CLIENT_NAME = "Ahab"

SETUP_SQL = f"""create table if not exists public.{TABLE} (
  state text primary key, code text, error text, received_at timestamptz not null default now());
alter table public.{TABLE} enable row level security;
grant select, insert, update, delete on public.{TABLE} to service_role;
revoke all on public.{TABLE} from anon, authenticated;"""


class AuthError(Exception):
    """A login couldn't start or finish. The message goes back to Ahab (mcp.py makes it an MCPError)."""


# --- where things are kept ---------------------------------------------------------------------

def auth_dir() -> Path:
    if os.environ.get(DIR_ENV, "").strip():
        return Path(os.environ[DIR_ENV].strip())
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Ahab" / "mcp-auth"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "ahab" / "mcp-auth"


def _file(memory, name: str) -> Path:
    home = hashlib.sha256(str(Path(memory.home).resolve()).encode()).hexdigest()[:10]
    return auth_dir() / f"{home}-{name}.json"


def _load(memory, name: str, url: str) -> dict:
    try:
        record = json.loads(_file(memory, name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return record if isinstance(record, dict) and record.get("url") == url else {}   # a new url starts over


def _save(memory, name: str, record: dict) -> None:
    folder = auth_dir()
    folder.mkdir(parents=True, exist_ok=True)
    try:
        folder.chmod(0o700)
    except OSError:
        pass
    path = _file(memory, name)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(record, indent=1), encoding="utf-8")
    try:
        temp.chmod(0o600)
    except OSError:
        pass
    temp.replace(path)


def callback() -> str | None:
    if os.environ.get(CALLBACK_ENV, "").strip():
        return os.environ[CALLBACK_ENV].strip()
    base = os.environ.get("AHAB_SUPABASE_URL", "").strip().rstrip("/")
    return f"{base}/functions/v1/ahab-oauth" if base else None


# --- talking to login services ------------------------------------------------------------------

def _fetch(url: str, form: dict | None = None, body: dict | None = None, headers: dict | None = None):
    """(status, json or None). The seam the tests replace."""
    data = None
    sent = {"Accept": "application/json", "User-Agent": USER_AGENT, **(headers or {})}
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        sent["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        sent["Content-Type"] = "application/json"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=sent), timeout=TIMEOUT) as r:
            status, raw = r.status, r.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    except (urllib.error.URLError, OSError) as e:
        raise AuthError(f"Couldn't reach {urllib.parse.urlparse(url).hostname}: {getattr(e, 'reason', e)}.") from None
    try:
        return status, json.loads(raw or b"null")
    except ValueError:
        return status, None


def _secure(url: str) -> str:
    host = urllib.parse.urlparse(url).hostname or ""
    if not (url.startswith("https://") or (url.startswith("http://") and host in ("localhost", "127.0.0.1", "::1"))):
        raise AuthError(f"A login address has to be https: {url[:100]}")
    return url


def _well_known(url: str, name: str) -> list[str]:
    """RFC 8414's places to look: the name before the path, then at the root."""
    parts = urllib.parse.urlparse(url)
    origin, path = f"{parts.scheme}://{parts.netloc}", parts.path.rstrip("/")
    return [f"{origin}/.well-known/{name}{path}"] + ([f"{origin}/.well-known/{name}"] if path else [])


def _challenge_param(header: str, key: str) -> str | None:
    match = re.search(rf'{key}="([^"]*)"', header or "")
    return match.group(1) if match else None


def discover(url: str, challenge: str = "") -> dict:
    """The login service's addresses for this server, and the scope to ask for."""
    resource, scope = {}, _challenge_param(challenge, "scope")
    places = ([_challenge_param(challenge, "resource_metadata")] if _challenge_param(challenge, "resource_metadata")
              else []) + _well_known(url, "oauth-protected-resource")
    for place in places:
        status, found = _fetch(_secure(place))
        if status == 200 and isinstance(found, dict) and found.get("authorization_servers"):
            resource = found
            break
    parts = urllib.parse.urlparse(url)
    issuer = (resource.get("authorization_servers") or [f"{parts.scheme}://{parts.netloc}"])[0]
    meta = {}
    for place in _well_known(issuer, "oauth-authorization-server") + _well_known(issuer, "openid-configuration"):
        status, found = _fetch(_secure(place))
        if status == 200 and isinstance(found, dict) and found.get("authorization_endpoint"):
            meta = found
            break
    if not meta:
        if resource:
            raise AuthError(f"Couldn't find the login service at {issuer}.")
        base = f"{parts.scheme}://{parts.netloc}"   # the oldest servers: fixed addresses at their root
        meta = {"authorization_endpoint": base + "/authorize", "token_endpoint": base + "/token",
                "registration_endpoint": base + "/register"}
    if "S256" not in meta.get("code_challenge_methods_supported", ["S256"]):
        raise AuthError("That server's login doesn't support PKCE, which Ahab needs for a safe login.")
    supported = resource.get("scopes_supported")
    # The token is named for the server as it names itself, which leaves off any query in the owner's
    # url (?project_ref=...). Only a name covering this same address counts, so a stray metadata file
    # can't point the token at somewhere else.
    plain = urllib.parse.urlunparse(parts._replace(query="", fragment=""))
    named = resource.get("resource")
    if not (isinstance(named, str) and urllib.parse.urlparse(named).netloc == parts.netloc
            and plain.startswith(named.rstrip("/"))):
        named = plain
    return {"resource": named, "authorization_endpoint": _secure(meta["authorization_endpoint"]),
            "token_endpoint": _secure(meta["token_endpoint"]),
            "registration_endpoint": meta.get("registration_endpoint"),
            "scope": scope or (" ".join(supported) if isinstance(supported, list) and supported else None)}


def _register(found: dict, redirect: str, oauth: dict) -> dict:
    from ahab.connections.mcp import _fill
    if oauth.get("client_id"):
        return {"client_id": _fill(oauth["client_id"], "the oauth client_id"),
                "client_secret": _fill(oauth["client_secret"], "the oauth client_secret")
                if oauth.get("client_secret") else None}
    if not found.get("registration_endpoint"):
        raise AuthError("That server doesn't let apps register themselves. Your owner can make an OAuth app "
                        "there and put its client_id (and client_secret) under the server's \"oauth\" in home.json.")
    status, made = _fetch(_secure(found["registration_endpoint"]), body={
        "client_name": CLIENT_NAME, "redirect_uris": [redirect], "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"], "token_endpoint_auth_method": "none"})
    if status not in (200, 201) or not isinstance(made, dict) or not made.get("client_id"):
        raise AuthError(f"Registering Ahab with that server's login didn't work ({status}).")
    return {"client_id": made["client_id"], "client_secret": made.get("client_secret"),
            "auth_method": made.get("token_endpoint_auth_method")}


def _token_request(record: dict, form: dict) -> dict:
    form = {**form, "client_id": record["client_id"], "resource": record.get("resource") or record["url"]}
    headers = {}
    if record.get("client_secret"):
        if record.get("auth_method") == "client_secret_basic":
            pair = f"{urllib.parse.quote(record['client_id'])}:{urllib.parse.quote(record['client_secret'])}"
            headers["Authorization"] = "Basic " + base64.b64encode(pair.encode()).decode()
        else:
            form["client_secret"] = record["client_secret"]
    status, got = _fetch(record["token_endpoint"], form=form, headers=headers)
    if status != 200 or not isinstance(got, dict) or not got.get("access_token"):
        detail = (got or {}).get("error_description") or (got or {}).get("error") if isinstance(got, dict) else None
        raise AuthError(f"The login service refused the token request ({status}{': ' + str(detail) if detail else ''}).")
    return got


def _keep_tokens(record: dict, got: dict) -> dict:
    record["access_token"] = got["access_token"]
    record["refresh_token"] = got.get("refresh_token") or record.get("refresh_token")
    try:
        record["expires_at"] = time.time() + int(got["expires_in"]) if got.get("expires_in") else None
    except (TypeError, ValueError):
        record["expires_at"] = None
    return record


# --- what mcp.py asks -----------------------------------------------------------------------

def token(memory, name: str, url: str) -> str | None:
    """A usable access token for this server, renewed if it expired; None if there's none."""
    record = _load(memory, name, url)
    if record.get("pending"):
        collect(memory, force=True)
        record = _load(memory, name, url)
    if not record.get("access_token"):
        return None
    if record.get("expires_at") and record["expires_at"] < time.time() + 60:
        return refresh(memory, name, url)
    return record["access_token"]


def refresh(memory, name: str, url: str) -> str | None:
    """Renew the token, or forget it if that fails (the next call starts a login)."""
    record = _load(memory, name, url)
    if record.get("refresh_token"):
        try:
            _keep_tokens(record, _token_request(record, {"grant_type": "refresh_token",
                                                         "refresh_token": record["refresh_token"]}))
            _save(memory, name, record)
            return record["access_token"]
        except AuthError:
            pass
    if record:
        record.pop("access_token", None)
        record.pop("refresh_token", None)
        _save(memory, name, record)
    return None


def start_login(memory, name: str, url: str, challenge: str, oauth: dict | None = None) -> str:
    """Send the owner a login link (once per LOGIN_MINUTES). Returns what to tell Ahab."""
    from ahab import inbox
    record = _load(memory, name, url)
    pending = record.get("pending") or {}
    if pending and time.time() - pending.get("created", 0) < LOGIN_MINUTES * 60:
        return (f"{name} needs your owner to log in. The link went to them "
                f"{(time.time() - pending['created']) // 60:.0f} min ago; you'll be woken when they've done it.")
    redirect = callback()
    if not redirect:
        raise AuthError(f"{name} wants a login, and logins need a callback: AHAB_SUPABASE_URL (with the ahab-oauth "
                        f"function deployed) or {CALLBACK_ENV}. That's for your owner to set up.")
    found = discover(url, challenge)
    oauth = oauth or {}
    if not record.get("client_id") or record.get("redirect") != redirect:
        record = {"url": url, "redirect": redirect, **_register(found, redirect, oauth)}
    record.update(token_endpoint=found["token_endpoint"], resource=found["resource"])
    verifier, state = secrets.token_urlsafe(48), secrets.token_urlsafe(24)
    challenge_hash = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    params = {"response_type": "code", "client_id": record["client_id"], "redirect_uri": redirect,
              "code_challenge": challenge_hash, "code_challenge_method": "S256", "state": state,
              "resource": found["resource"]}
    scope = oauth.get("scope") or found.get("scope")
    if scope:
        params["scope"] = scope
    link = found["authorization_endpoint"] + ("&" if "?" in found["authorization_endpoint"] else "?") \
        + urllib.parse.urlencode(params)
    record["pending"] = {"state": state, "verifier": verifier, "created": time.time()}
    _save(memory, name, record)
    inbox.ahab_sends(memory, f"To use {name} (an MCP server you added), I need you to log in to it. "
                             f"This link is good for {LOGIN_MINUTES} minutes:\n{link}")
    memory.record("mcp", f"Sent your owner a link to log in to {name}.")
    return (f"{name} needs your owner to log in first. Code has sent them a link on Telegram; "
            "you'll be woken when they've done it, and then its tools work.")


_last_poll = [0.0]


def collect(memory, force: bool = False) -> bool:
    """Finish any login whose code has arrived. True when one finished (or failed), to wake Ahab.
    Checked while the heartbeat sleeps; never raises."""
    try:
        waiting = []
        folder = auth_dir()
        if folder.is_dir():
            prefix = _file(memory, "x").name[:-len("x.json")]
            for path in folder.glob(f"{prefix}*.json"):
                try:
                    record = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if isinstance(record, dict) and record.get("pending"):
                    waiting.append((path.name[len(prefix):-len(".json")], record))
        if not waiting or (not force and time.monotonic() - _last_poll[0] < POLL_SECONDS):
            return False
        _last_poll[0] = time.monotonic()
        from ahab.connections import supabase
        woke = False
        for name, record in waiting:
            pending = record["pending"]
            if time.time() - pending.get("created", 0) > LOGIN_MINUTES * 60:
                record.pop("pending")
                _save(memory, name, record)
                memory.record("mcp", f"The login link for {name} expired unused. The next call to it sends a new one.")
                continue
            where = f"{TABLE}?state=eq.{urllib.parse.quote(pending['state'])}"
            try:
                rows, _ = supabase._call("GET", where + "&select=code,error")
                if rows:
                    supabase._call("DELETE", where, headers={"Prefer": "return=minimal"})
            except supabase.SupabaseError:
                continue   # Supabase unreachable for a moment: try again next time
            if not rows:
                continue
            record.pop("pending")
            row = rows[0]
            if row.get("error") or not row.get("code"):
                _save(memory, name, record)
                memory.record("mcp", f"Your owner's login to {name} didn't go through: {row.get('error') or 'no code'}.")
            else:
                try:
                    _keep_tokens(record, _token_request(record, {
                        "grant_type": "authorization_code", "code": row["code"],
                        "redirect_uri": record["redirect"], "code_verifier": pending["verifier"]}))
                    memory.record("mcp", f"Your owner logged in to {name}. Its tools work now (mcp_tools shows them).")
                except AuthError as e:
                    memory.record("mcp", f"Your owner logged in to {name}, but getting its token failed: {e}")
                _save(memory, name, record)
            woke = True
        return woke
    except Exception as e:   # never stop the heartbeat over a login
        try:
            memory.record("error", f"Checking MCP logins crashed: {type(e).__name__}: {e}")
        except Exception:
            pass
        return False
