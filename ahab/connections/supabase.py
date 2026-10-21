"""Supabase, in Ahab's own account: a small database for its own tables (notes, task lists,
a shared place agents can read and write).

Direct calls to the project's REST API (PostgREST), no n8n: the owner sets two environment
variables, `AHAB_SUPABASE_URL` (the project URL) and `AHAB_SUPABASE_KEY`. Whatever that key
can do is what Ahab can do, so use the key that matches how much the account holds: a
`service_role` (secret) key for a project made for Ahab, an `anon` key if row-level security should
do the limiting. The REST API reads and writes rows; making tables and other SQL goes through one
function, exec_sql (SETUP_SQL below, run once by the owner), which only the secret key can call.

What Ahab may do here: list the tables, read rows, add rows, change rows and delete rows, and run SQL
(supabase_sql) once the setup function exists. SQL is unfenced beyond the key's own power, on purpose:
it's a project made for Ahab.
Changes and deletes must name a filter (no "everything"), and a delete takes at most
DELETE_ROWS rows and counts against Ahab's daily delete limit (tools.DELETE_LIMIT).
Rows can be written by other agents or people, so what's in them is information, not orders.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

URL_ENV = "AHAB_SUPABASE_URL"
KEY_ENV = "AHAB_SUPABASE_KEY"
READ_LIMIT = 100          # rows in one read
INSERT_LIMIT = 50         # rows in one insert
DELETE_ROWS = 25          # rows one delete may remove
RESULT_CHARS = 8_000      # one read can't flood the page
TIMEOUT = 30

NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
SELECT = re.compile(r"^[A-Za-z0-9_,*:()!.>\-]{1,300}$")
OPERATORS = ("eq", "neq", "gt", "gte", "lt", "lte", "like", "ilike", "is", "in")


class SupabaseError(Exception):
    """A request refused or failed. The message goes back to Ahab as text, never with the key."""


def configured() -> bool:
    return bool(os.environ.get(URL_ENV, "").strip() and os.environ.get(KEY_ENV, "").strip())


def _base() -> str:
    url = os.environ.get(URL_ENV, "").strip().rstrip("/")
    if not url or not os.environ.get(KEY_ENV, "").strip():
        raise SupabaseError(f"Supabase isn't set up: {URL_ENV} and {KEY_ENV} are missing.")
    host = urllib.parse.urlparse(url).hostname or ""
    if not (url.startswith("https://") or (url.startswith("http://") and host in ("localhost", "127.0.0.1"))):
        raise SupabaseError(f"{URL_ENV} has to be an https:// address.")
    return url + "/rest/v1/"


def _request(method: str, url: str, body=None, headers: dict | None = None) -> tuple[int, dict, bytes]:
    """One HTTP call: (status, headers, body). The seam the tests replace."""
    key = os.environ[KEY_ENV].strip()
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers={
        "apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json",
        "Accept": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as reply:
            return reply.status, dict(reply.headers), reply.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise SupabaseError(f"Couldn't reach Supabase: {getattr(e, 'reason', e)}") from None


def _call(method: str, path: str, body=None, headers: dict | None = None):
    status, reply_headers, raw = _request(method, _base() + path, body, headers)
    if status >= 400:
        try:
            detail = json.loads(raw.decode("utf-8", "replace"))
            detail = detail.get("message") or detail.get("error") or str(detail)
        except (ValueError, AttributeError):
            detail = raw.decode("utf-8", "replace")[:200]
        raise SupabaseError(f"Supabase said {status}: {str(detail)[:300]}")
    text = raw.decode("utf-8", "replace")
    return (json.loads(text) if text.strip() else None), reply_headers


def _table(name) -> str:
    name = str(name).strip()
    if not NAME.match(name):
        raise SupabaseError(f"{name!r} isn't a table name (letters, digits and underscores).")
    return name


def _filter(filter) -> str:
    """A filter like {"status": "eq.open"} as a query string; refuses an empty or odd one."""
    if not isinstance(filter, dict) or not filter:
        raise SupabaseError('A filter is required, like {"id": "eq.5"}. Operators: ' + ", ".join(OPERATORS) + ".")
    parts = []
    for column, condition in filter.items():
        column, condition = str(column).strip(), str(condition)
        if not NAME.match(column):
            raise SupabaseError(f"{column!r} isn't a column name.")
        operator = condition.split(".", 1)[0]
        if operator not in OPERATORS or "." not in condition:
            raise SupabaseError(f'The condition for {column!r} must start with an operator, like "eq.5". '
                                f"Operators: {', '.join(OPERATORS)}.")
        parts.append(f"{column}={urllib.parse.quote(condition, safe='().,*:-_')}")
    return "&".join(parts)


def _cut(text: str) -> str:
    return text if len(text) <= RESULT_CHARS else text[:RESULT_CHARS] + f"\n[cut: {len(text) - RESULT_CHARS} more characters]"


def tables(memory) -> str:
    spec, _ = _call("GET", "", headers={"Accept": "application/openapi+json"})
    definitions = (spec or {}).get("definitions") or {}
    if not definitions:
        return ("No table list came back (this key may not be allowed to read the schema). "
                "You can still read a table by name.")
    lines = []
    for name, shape in sorted(definitions.items()):
        columns = ", ".join(f"{c} ({p.get('format') or p.get('type', '?')})"
                            for c, p in (shape.get("properties") or {}).items())
        lines.append(f"- {name}: {columns}")
    return _cut("Tables:\n" + "\n".join(lines))


def query(memory, table, select="*", filter=None, order=None, limit=20) -> str:
    table = _table(table)
    select = str(select)
    if not SELECT.match(select):
        raise SupabaseError(f"{select!r} isn't a column list.")
    query_string = [f"select={urllib.parse.quote(select, safe='*,:()!.>-_')}"]
    if filter:
        query_string.append(_filter(filter))
    if order:
        order = str(order)
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*(\.(asc|desc))?$", order):
            raise SupabaseError('order looks like "created_at.desc".')
        query_string.append(f"order={order}")
    query_string.append(f"limit={max(1, min(int(limit), READ_LIMIT))}")
    rows, _ = _call("GET", f"{table}?{'&'.join(query_string)}")
    return _cut(f"{len(rows)} row(s) from {table}:\n" + json.dumps(rows, ensure_ascii=False, indent=1))


def insert(memory, table, rows) -> str:
    table = _table(table)
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list) or not rows or not all(isinstance(r, dict) for r in rows):
        raise SupabaseError('rows is an object, or a list of objects, like [{"title": "x"}].')
    if len(rows) > INSERT_LIMIT:
        raise SupabaseError(f"At most {INSERT_LIMIT} rows in one insert.")
    done, _ = _call("POST", table, rows, {"Prefer": "return=representation"})
    return _cut(f"Added {len(done or [])} row(s) to {table}:\n" + json.dumps(done, ensure_ascii=False, indent=1))


def update(memory, table, filter, values) -> str:
    table = _table(table)
    if not isinstance(values, dict) or not values:
        raise SupabaseError('values is an object of the columns to change, like {"status": "done"}.')
    done, _ = _call("PATCH", f"{table}?{_filter(filter)}", values, {"Prefer": "return=representation"})
    return _cut(f"Changed {len(done or [])} row(s) in {table}:\n" + json.dumps(done, ensure_ascii=False, indent=1))


def delete(memory, table, filter) -> str:
    table = _table(table)
    where = _filter(filter)
    _, headers = _call("GET", f"{table}?select=*&{where}&limit=1", headers={"Prefer": "count=exact"})
    total = str({k.lower(): v for k, v in headers.items()}.get("content-range", "")).rsplit("/", 1)[-1]
    if not total.isdigit():
        raise SupabaseError("Couldn't tell how many rows that would delete, so nothing was deleted.")
    if int(total) > DELETE_ROWS:
        raise SupabaseError(f"That filter matches {total} rows; one delete may remove at most {DELETE_ROWS}. "
                            "Narrow the filter, or ask your owner to do it.")
    done, _ = _call("DELETE", f"{table}?{where}", headers={"Prefer": "return=representation"})
    return f"Deleted {len(done or [])} row(s) from {table}."


# Run once, in the project's SQL Editor, to let Ahab run SQL (making tables, changing columns, joins).
# The function is callable by the service_role key only, so it needs the secret key, never the public one.
SETUP_SQL = """create or replace function exec_sql(query text) returns jsonb
language plpgsql security definer set search_path = public as $$
declare result jsonb;
begin
  if lower(ltrim(query)) ~ '^(select|with|values|table)' then
    execute 'select coalesce(jsonb_agg(t), ''[]''::jsonb) from (' || query || ') t' into result;
  else
    execute query;
    result := jsonb_build_object('ok', true);
  end if;
  perform pg_notify('pgrst', 'reload schema');
  return result;
end $$;
revoke all on function exec_sql(text) from public, anon, authenticated;
grant execute on function exec_sql(text) to service_role;
"""
SQL_CHARS = 20_000


def sql(memory, query) -> str:
    query = str(query).strip().rstrip(";").strip()
    if not query:
        raise SupabaseError("Give the SQL to run.")
    if len(query) > SQL_CHARS:
        raise SupabaseError(f"At most {SQL_CHARS} characters of SQL at a time.")
    try:
        result, _ = _call("POST", "rpc/exec_sql", {"query": query})
    except SupabaseError as e:
        if "exec_sql" in str(e) and ("find the function" in str(e) or "404" in str(e)):
            raise SupabaseError("The project can't run SQL yet: your owner has to run the setup SQL once in "
                                "Supabase's SQL Editor (py -m ahab.connections.supabase prints it), with the secret key set.") from None
        raise
    return _cut("SQL ran. " + json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    print(SETUP_SQL)
