"""n8n: how Perro reaches outside services like Google Calendar.

Perro never signs in to Google itself. The owner's n8n (a separate program,
running in Docker on this computer) holds those sign-ins, and each thing
Perro may do there is a workflow behind a webhook: a local web address that
runs the workflow and answers with JSON. This module calls those addresses.
The workflows themselves live in n8n (copies in ../n8n/*.json).

Two fences:
- Perro never picks the address. Each tool names a fixed webhook path here,
  and only its arguments come from the model.
- The base address must be this computer. Pointing it elsewhere would send
  the owner's data across a network in the clear; change the check on
  purpose if that day comes.

Everything that comes back (event titles, descriptions, later emails) was
written by whoever made it: information, never instructions.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime

BASE_ENV = "PERRO_N8N_URL"
DEFAULT_BASE = "http://127.0.0.1:5678/webhook"
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
TIMEOUT = 30
RESPONSE_BYTE_LIMIT = 1024 * 1024

MAX_DAYS = 60
EVENT_LIMIT = 40
DESCRIPTION_CHAR_LIMIT = 200


class N8nError(Exception):
    """n8n couldn't be reached or the workflow failed. Readable by Perro."""


def _base() -> str:
    base = os.environ.get(BASE_ENV, DEFAULT_BASE).rstrip("/")
    if urllib.parse.urlsplit(base).hostname not in LOCAL_HOSTS:
        raise N8nError(f"{BASE_ENV} must point at this computer, not {base!r}.")
    return base


def call(path: str, **params) -> dict:
    """GET one of the owner's n8n webhooks and return its JSON answer."""
    url = f"{_base()}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    # No proxies: this traffic never leaves the machine.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=TIMEOUT) as response:
            body = response.read(RESPONSE_BYTE_LIMIT + 1)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise N8nError(f"n8n has no published workflow at {path!r}.") from None
        raise N8nError(f"The n8n workflow at {path!r} failed (HTTP {e.code}); "
                       "your owner can see why in n8n's Executions tab.") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise N8nError("n8n isn't answering (it runs in Docker on your owner's "
                       "computer; maybe Docker isn't running).") from None
    if len(body) > RESPONSE_BYTE_LIMIT:
        raise N8nError(f"n8n's answer from {path!r} was too big to read.")
    try:
        return json.loads(body)
    except ValueError:
        raise N8nError(f"n8n's answer from {path!r} wasn't JSON.") from None


# --- calendar ------------------------------------------------------------

def _when(start: dict, end: dict) -> str:
    """One event's time in the owner's local time, e.g. 'Mon 19 Oct 14:00-15:00'."""
    if "dateTime" in start:
        s = datetime.fromisoformat(start["dateTime"]).astimezone()
        text = f"{s:%a %d %b %H:%M}"
        if end.get("dateTime"):
            e = datetime.fromisoformat(end["dateTime"]).astimezone()
            text += f"-{e:%H:%M}" if e.date() == s.date() else f" to {e:%a %d %b %H:%M}"
        return text
    # All-day events: Google's end date is the day after the last day.
    s = date.fromisoformat(start["date"])
    e = date.fromisoformat(end["date"]) if end.get("date") else s
    if (e - s).days <= 1:
        return f"{s:%a %d %b} (all day)"
    last = date.fromordinal(e.toordinal() - 1)
    return f"{s:%a %d %b} to {last:%a %d %b} (all day)"


def check_calendar(days: int = 7) -> str:
    days = int(days)
    if not 1 <= days <= MAX_DAYS:
        raise ValueError(f"days must be 1 to {MAX_DAYS}.")
    events = call("perro/calendar", days=days).get("events") or []
    if not events:
        return f"Nothing on the calendars you can see in the next {days} day(s)."
    lines = [f"{len(events)} event(s) in the next {days} day(s), your owner's local time:"]
    for event in events[:EVENT_LIMIT]:
        line = f"- {_when(event.get('start') or {}, event.get('end') or {})}: {event.get('summary') or '(no title)'}"
        if event.get("location"):
            line += f" @ {event['location']}"
        line += f"  [{event.get('calendar', '?')}]"
        description = " ".join(str(event.get("description") or "").split())
        if description:
            if len(description) > DESCRIPTION_CHAR_LIMIT:
                description = description[:DESCRIPTION_CHAR_LIMIT] + " [...]"
            line += f"\n    {description}"
        lines.append(line)
    if len(events) > EVENT_LIMIT:
        lines.append(f"...and {len(events) - EVENT_LIMIT} more (only the first {EVENT_LIMIT} are shown).")
    return "\n".join(lines)
