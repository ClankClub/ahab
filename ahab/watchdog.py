"""The watchdog: a check from outside that Ahab is actually running.

The schedulers already restart a heartbeat or bridge that crashes (Windows
tries three times, launchd on every crash). What they miss is quieter: a
process that's gone and stays gone, a heartbeat that's alive but never wakes,
n8n (or Docker under it) being down. Ahab can't report any of those itself,
so this runs as its own scheduled task every few minutes, outside Ahab, and
tells the owner on Telegram directly through the bot's API, not through
Ahab or the bridge, which may be what's down.

What it looks at:
  - <home>/heartbeat.alive and telegram.alive, which the heartbeat and the
    bridge touch every minute while they run (keep_alive below). Stale means
    the process is gone.
  - The heartbeat's own log: a "sleep" event says when it means to wake, so
    a heartbeat still asleep well past that, or a wake-up that started long
    ago and never finished, is stuck.
  - n8n's /healthz, if Ahab uses n8n.

A problem has to show up on two checks in a row before the owner hears about
it, so a PC just back from sleep doesn't set it off. Each problem is told
once, and once more when it clears; the state is in <home>/watchdog.json.
Every message also goes in Ahab's log, so it can answer for the gap.

  py -m ahab.watchdog            check, and tell the owner what changed
  py -m ahab.watchdog --dry-run  just print what it finds
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from ahab.memory import DEFAULT_HOME, Memory

ALIVE_EVERY = 60
# No touch for this long and the process is taken to be gone.
ALIVE_STALE = 5 * 60
# A heartbeat this far past the wake-up it chose is stuck asleep.
OVERDUE_GRACE = 30 * 60
# A wake-up (up to 20 model calls, a slow model taking several minutes each) running this long is stuck.
WAKE_STUCK = 3 * 3600
CHECKS_BEFORE_TELLING = 2
STATE_FILE = "watchdog.json"
# How each problem is named when it clears.
WHAT = {"heartbeat": "the heartbeat", "heartbeat_stuck": "the heartbeat's schedule",
        "telegram": "the Telegram bridge", "n8n": "n8n", "mind": "the model's check"}
HTTP_TIMEOUT = 5


def keep_alive(home: Path, name: str) -> None:
    """Touch <home>/<name>.alive now and every minute after, for as long as this
    process lives. A daemon thread, so it never keeps a stopping process up."""
    path = home / f"{name}.alive"

    def touch() -> None:
        while True:
            try:
                path.write_text(f"{time.time():.0f}\n", encoding="utf-8")
            except OSError:
                pass
            time.sleep(ALIVE_EVERY)

    threading.Thread(target=touch, daemon=True, name=f"{name}-alive").start()


def _clock(t: float) -> str:
    return f"{datetime.fromtimestamp(t):%a %H:%M}"


def _last_alive(home: Path, name: str) -> float | None:
    try:
        return (home / f"{name}.alive").stat().st_mtime
    except OSError:
        return None


def _gone(home: Path, name: str, what: str, now: float) -> str | None:
    last = _last_alive(home, name)
    if last is not None and now - last < ALIVE_STALE:
        return None
    since = f"no sign of it since {_clock(last)}" if last else "no sign of it yet"
    return f"{what} isn't running ({since})."


def _stuck(memory: Memory, now: float) -> str | None:
    """Is the heartbeat past the wake-up it chose, or caught in one?"""
    row = memory.db.execute("SELECT time, kind, content FROM events WHERE kind IN ('wake', 'sleep') "
                            "ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return None
    at = datetime.fromisoformat(row[0]).timestamp()
    if row[1] == "wake":
        if now - at > WAKE_STUCK:
            return f"The heartbeat started a wake-up at {_clock(at)} and hasn't finished it."
        return None
    try:
        minutes = int(row[2].split()[1])          # "Sleeping 600 min. [...]"
    except (IndexError, ValueError):
        return None
    due = at + minutes * 60
    if now - due > OVERDUE_GRACE:
        return f"The heartbeat is running but didn't wake when it meant to ({_clock(due)})."
    return None


def _n8n_down() -> str | None:
    from ahab import n8n
    if not os.environ.get(n8n.KEY_ENV):
        return None                                # this install doesn't use n8n
    base = urllib.parse.urlsplit(os.environ.get(n8n.BASE_ENV) or n8n.DEFAULT_BASE)
    url = f"{base.scheme}://{base.netloc}/healthz"
    try:
        with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT) as response:
            if response.status == 200:
                return None
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        pass
    return (f"n8n isn't answering at {base.netloc} (is Docker running?). Until it's back, the "
            "Google, Notion, email, GitHub and Vercel tools fail and the mail watch is blind.")


PROBE_SYSTEM = ("You are being checked by a monitor. You have no tools in this conversation, "
                "and none are described here. Answer with one JSON object and nothing else.")
PROBE_ASK = ("List the name of every tool or function you could call right now, in this "
             "conversation, if any. If you have none, give an empty list. "
             'Answer as {"tools": ["name", ...]}.')
PROBE_FORM = {"type": "object", "additionalProperties": False, "required": ["tools"],
              "properties": {"tools": {"type": "array", "items": {"type": "string"}}}}
PROBE_STATE = "watchdog-probe.json"
# A clean probe holds for a day; a bad one is tried again within the hour.
PROBE_EVERY = 24 * 3600
PROBE_RETRY = 3600


def probe_mind(home: Path, now: float, client=None) -> str | None:
    """The sanity half of the watchdog. Being alive isn't enough: on
    2026-10-18 Ahab was awake and well and sure its tools were gone, because
    Codex had handed its model tools of its own. Once a day this asks the
    running CLI backend, with nothing on the table, what tools it has; any
    answer but none means the fixes in cli_model.py stopped working (a Codex
    update, say). It also notices a model that can't answer at all. Costs
    one short model call a day; API backends have no built-in tools and are
    skipped."""
    path = home / PROBE_STATE
    try:
        last = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        last = {}
    wait = PROBE_RETRY if last.get("problem") else PROBE_EVERY
    if client is None and now - last.get("at", 0) < wait:
        return last.get("problem")
    from ahab.model import BACKENDS, ModelClient, ModelError
    try:
        backend = json.loads((home / "home.json").read_text(encoding="utf-8")).get("backend") or "deepseek"
    except (OSError, ValueError, AttributeError):
        backend = "deepseek"
    if client is None and "cli" not in BACKENDS.get(backend, {}):
        return None
    problem = None
    try:
        client = client or ModelClient.from_backend(backend)
        reply = client.complete([{"role": "system", "content": PROBE_SYSTEM},
                                 {"role": "user", "content": PROBE_ASK}], json_mode=PROBE_FORM)
        text = reply.text.strip()
        tools = json.loads(text[text.find("{"):text.rfind("}") + 1]).get("tools")
        if not isinstance(tools, list):
            raise ValueError(text[:200])
        used = getattr(client, "foreign", None) or []
        if tools or used:
            problem = (f"{_name(home)}'s model ({backend}) has tools of its own again: "
                       f"{', '.join(map(str, tools + used))}. Its CLI was probably updated past the "
                       "fixes in cli_model.py; until that's looked at it may say its real tools are gone.")
    except ModelError as e:
        problem = f"{_name(home)}'s model ({backend}) isn't answering: {str(e)[:300]}"
    except (ValueError, AttributeError) as e:
        problem = (f"{_name(home)}'s model ({backend}) answered a simple check with something "
                   f"unreadable: {str(e)[:200]}")
    try:
        path.write_text(json.dumps({"at": now, "problem": problem}), encoding="utf-8")
    except OSError:
        pass
    return problem


def _model_tools_used(memory: Memory, now: float) -> str | None:
    """A wake-up in the last day whose model used Codex's own tools (heartbeat.py
    logs it as it happens, so this catches it between probes)."""
    since = datetime.fromtimestamp(now - 24 * 3600, timezone.utc).isoformat(timespec="seconds")
    row = memory.db.execute("SELECT time FROM events WHERE kind = 'model_tools' AND time > ? "
                            "ORDER BY id DESC LIMIT 1", (since,)).fetchone()
    if not row:
        return None
    return (f"In a wake-up at {_clock(datetime.fromisoformat(row[0]).timestamp())}, Codex let the model "
            "use its own built-in tools. The fixes in cli_model.py may have stopped working.")


def problems(home: Path, memory: Memory, now: float) -> dict[str, str]:
    """What's wrong right now, by a short key."""
    found = {}
    mind = probe_mind(home, now) or _model_tools_used(memory, now)
    if mind:
        found["mind"] = mind
    gone = _gone(home, "heartbeat", "The heartbeat", now)
    if gone:
        found["heartbeat"] = gone
    else:
        stuck = _stuck(memory, now)
        if stuck:
            found["heartbeat_stuck"] = stuck
    if (home / "telegram.json").is_file():
        gone = _gone(home, "telegram", "The Telegram bridge", now)
        if gone:
            found["telegram"] = gone + " Your messages to the bot aren't reaching anyone."
    down = _n8n_down()
    if down:
        found["n8n"] = down
    return found


def _name(home: Path) -> str:
    try:
        name = json.loads((home / "home.json").read_text(encoding="utf-8")).get("name", "Ahab")
    except (OSError, ValueError, AttributeError):
        name = "Ahab"
    return name or "your agent"


def check(home: Path, now: float | None = None, send=None) -> list[str]:
    """One round: compare with the last, tell the owner what's new or fixed.
    send(text) delivers a message; returns the messages sent."""
    now = time.time() if now is None else now
    path = home / STATE_FILE
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    memory = Memory(home)
    try:
        found = problems(home, memory, now)
        new, fixed = [], []
        for key, text in found.items():
            entry = state.setdefault(key, {"since": now, "seen": 0, "told": False})
            entry["seen"] += 1
            entry["text"] = text
            if entry["seen"] >= CHECKS_BEFORE_TELLING and not entry["told"]:
                new.append(key)
        for key in [k for k in state if k not in found]:
            if state[key]["told"]:
                fixed.append(key)
            else:
                del state[key]                     # a blip that cleared before anyone heard
        sent = []
        if new or fixed:
            lines = [f"[watchdog] {_name(home)}:"]
            lines += [state[k]["text"] for k in new]
            lines += [f"Back to normal: {WHAT.get(k, k)} (out since {_clock(state[k]['since'])})."
                      for k in fixed]
            if new:
                lines.append("If you stopped it on purpose, ignore this; I won't repeat it.")
            text = "\n".join(lines)
            if send is None or send(text):
                for k in new:
                    state[k]["told"] = True
                for k in fixed:
                    del state[k]
                sent.append(text)
                memory.record("watchdog", f"The watchdog told your owner: {text}")
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(path)
        return sent
    finally:
        memory.close()


def telegram_sender(home: Path):
    """send(text) through the bot's API straight to the paired owner, or None if
    there's no bot. Returns whether it went."""
    from ahab import telegram
    token = os.environ.get(telegram.TOKEN_ENV)
    chat = telegram.load_state(home).get("owner_chat_id")
    if not token or not chat:
        return None
    bot = telegram.Bot(token)

    def send(text: str) -> bool:
        try:
            bot.send(chat, text)
            return True
        except telegram.TelegramError as e:
            print(f"[{datetime.now():%Y-%m-%d %H:%M}] Couldn't tell the owner: {e}")
            return False

    return send


def main() -> None:
    parser = argparse.ArgumentParser(description="Check that Ahab is running; tell the owner if not.")
    parser.add_argument("--home", type=Path, default=DEFAULT_HOME)
    parser.add_argument("--dry-run", action="store_true", help="print what's wrong; send and save nothing")
    parser.add_argument("--log", action="store_true", help="write output to <home>/watchdog.log")
    args = parser.parse_args()
    if args.log:
        sys.stdout = sys.stderr = open(args.home / "watchdog.log", "a", encoding="utf-8",
                                       errors="replace", buffering=1)
    if args.dry_run:
        memory = Memory(args.home)
        try:
            found = problems(args.home, memory, time.time())
        finally:
            memory.close()
        print("\n".join(found.values()) or "All well.")
        return
    send = telegram_sender(args.home)
    if send is None:
        print(f"[{datetime.now():%Y-%m-%d %H:%M}] No paired Telegram bot; can't tell the owner anything.")
    for text in check(args.home, send=send or (lambda text: False)):
        print(f"[{datetime.now():%Y-%m-%d %H:%M}] Sent: {text}")
    # Once a week, the memory report rides along (see report.py).
    from ahab import report
    try:
        if text := report.send_if_due(args.home, datetime.now().astimezone(), send or (lambda text: False)):
            print(f"[{datetime.now():%Y-%m-%d %H:%M}] Sent the memory report.")
    except Exception as e:      # a broken report must never stop the watchdog
        print(f"[{datetime.now():%Y-%m-%d %H:%M}] Memory report failed: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
