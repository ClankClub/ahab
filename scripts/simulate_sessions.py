"""Simulate months of Ahab's running session in minutes, without calling a model.

The real heartbeat runs every wake-up: the real page, the real session table,
the real folding. Only four things are fake:

  the clock     `datetime.now` and `time.time` in the ahab modules jump ahead
                to the next thing that happens instead of waiting for it
  Ahab          answers each call from a wake-up shape drawn at random from
                PROFILE, measured from a real agent's log: how many tools it
                used, how long its thoughts, results and messages ran
  the owner     chats in bursts, a few times a day, and now and then goes
                quiet for days (OWNERS)
  the summary   comes back at a size that drifts with what was folded, too
                long now and then, and fails or goes down for hours
                (--fail, --outages), so the retry and the hard fold get used

After every wake-up it checks what must always hold (no wake-up split by a
fold, the newest never folded, one summary at most, the summary under its cap,
turns alternating), and for every call it estimates the page size and how much
of it a prompt cache would reuse. It prints the spread across all runs.

What it can't tell you is whether the summaries are any good: the fake writes
filler of the right length. That needs a real model.

  py scripts/simulate_sessions.py                         50 runs of 90 days
  py scripts/simulate_sessions.py --runs 5 --days 365 --owner quiet
  py scripts/simulate_sessions.py --limit 16000 --fail 0.2 --outages 0.1
  py scripts/simulate_sessions.py --profile path/to/memory.db --show-profile
"""

from __future__ import annotations

import argparse
import json
import math
import multiprocessing
import os
import random
import shutil
import statistics
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime as _datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from sim_owner import Owner, score

# Measured from a real log on 2026-10-21 (296 wake-ups); only sizes and counts, no content.
# Lists of numbers are 20 quantiles: a draw picks one and wobbles it a little.
PROFILE = {
    "chat_tools": [[0, 124], [1, 35], [2, 20], [3, 10], [4, 4], [5, 5], [6, 3], [7, 4], [8, 4], [9, 2],
                   [10, 2], [11, 2], [14, 2], [18, 1]],
    "alone_tools": [[0, 54], [1, 9], [2, 6], [3, 2], [5, 2], [6, 1], [12, 1], [14, 1], [20, 2]],
    "result": [23, 37, 49, 84, 152, 226, 286, 341, 532, 906, 1377, 2080, 3166, 5154, 6212, 8047, 8090, 8092,
               8150, 8685],
    "args": [2, 11, 17, 20, 25, 28, 33, 38, 40, 41, 43, 46, 56, 78, 80, 98, 134, 334, 423, 1872],
    "thought": [14, 67, 74, 89, 97, 109, 117, 131, 150, 165, 178, 196, 209, 226, 246, 258, 277, 302, 325, 394],
    "sent": [16, 69, 100, 110, 126, 139, 153, 173, 186, 209, 228, 246, 265, 280, 310, 371, 420, 511, 833, 1196],
    "owner": [1, 9, 15, 19, 24, 27, 33, 37, 43, 48, 52, 58, 73, 83, 99, 121, 149, 176, 230, 531],
    "notes_share": 0.35,
    "notes": [25, 255, 582, 1093, 1318, 1714, 2037, 2525, 2798, 3015],
}

# How the owner talks. busy: the build days the profile came from (about 60 messages a day);
# quiet: an ordinary week, a chat or two a day.
OWNERS = {
    "busy": {"chats_per_day": 7, "messages_per_chat": 8, "away_chance": 0.02, "away_days": (2, 5)},
    "quiet": {"chats_per_day": 1.5, "messages_per_chat": 3, "away_chance": 0.06, "away_days": (2, 10)},
}

CHECK_EVERY = 10             # wake-ups between rule checks (and one more at the end)
OLD_PAGE_TOKENS = 10_700     # the page before sessions, measured on 2026-10-21
OLD_STABLE_TOKENS = 4_800    # its part that read the same from wake to wake (system prompt, tool list)
START = _datetime(2027, 1, 4, 13, 0, tzinfo=timezone.utc)
WORDS = ("the owner asked about whales tide build file note result page session check fold summary "
         "today later because which then works failed again path line yes no maybe soon").split()


# --- the fake clock -------------------------------------------------------------------

class Clock:
    t = START


class SimDateTime(_datetime):
    @classmethod
    def now(cls, tz=None):
        return Clock.t.astimezone(tz) if tz else Clock.t.astimezone().replace(tzinfo=None)


def patch_clock() -> None:
    """Point every ahab module's `datetime` and the process's time.time at the fake clock."""
    for name, module in list(sys.modules.items()):
        if name.startswith("ahab") and getattr(module, "datetime", None) is _datetime:
            module.datetime = SimDateTime
    time.time = lambda: Clock.t.timestamp()


# --- drawing sizes --------------------------------------------------------------------

def draw(rng: random.Random, quantiles: list[int]) -> int:
    return max(1, int(rng.choice(quantiles) * rng.uniform(0.8, 1.2)))


def weighted(rng: random.Random, pairs: list[list[int]]) -> int:
    return rng.choices([v for v, _ in pairs], weights=[w for _, w in pairs])[0]


def filler(rng: random.Random, chars: int) -> str:
    words, size = [], 0
    while size < chars:
        w = rng.choice(WORDS)
        words.append(w)
        size += len(w) + 1
    return " ".join(words)[:chars]


def common_prefix(a: str, b: str) -> int:
    """Length of the shared opening of two strings, fast for long ones."""
    n, i, step = min(len(a), len(b)), 0, 65_536
    while step:
        while i + step <= n and a[i:i + step] == b[i:i + step]:
            i += step
        step //= 4
    return i


# --- the fake model -------------------------------------------------------------------

class FakeModel:
    """Plays the agent for wake-up calls and the summarizer for the rest."""

    backend, can_see = "sim", False

    def __init__(self, rng: random.Random, fail: float, cache_minutes: float, stats: dict):
        self.rng, self.fail, self.cache = rng, fail, timedelta(minutes=cache_minutes)
        self.stats, self.plan, self.chatting = stats, [], False
        self.down_until = START
        self.last_prompt, self.last_call = "", None
        self.wake_first = 0          # flattened length at this wake's first call (for the old-page estimate)
        self.wake_call = 0

    def start_wake(self, chatting: bool) -> None:
        p = PROFILE
        self.chatting = chatting
        tools = weighted(self.rng, p["chat_tools" if chatting else "alone_tools"])
        self.plan = [{"thought": filler(self.rng, draw(self.rng, p["thought"])), "tool": "read_file",
                      "args": {"path": filler(self.rng, draw(self.rng, p["args"]))}} for _ in range(tools)]
        self.wake_call = 0

    def complete(self, messages, temperature=0.7, json_mode=False):
        from ahab import cli_model
        from ahab.model import ModelError, Reply
        if not json_mode:
            return self.summarize(messages)
        roles = [m["role"] for m in messages]
        if roles[0] != "system" or any(a == b for a, b in zip(roles[1:], roles[2:])) or roles[-1] != "user":
            self.stats["violations"].append(f"turns don't alternate: {roles[:6]}...")
        system, prompt, _ = cli_model.flatten(messages)
        text = system + "\n" + prompt
        tokens = len(text) // 4
        fresh = self.last_call is not None and Clock.t - self.last_call <= self.cache
        cached = common_prefix(self.last_prompt, text) // 4 if fresh else 0
        if self.wake_call == 0:
            self.wake_first = len(text)
        # The old page: about OLD_PAGE_TOKENS plus this wake's own turns so far; between wakes
        # only its stable top was ever reused, within a wake everything before the last turn.
        old = OLD_PAGE_TOKENS + (len(text) - self.wake_first) // 4
        old_cached = (self.stats["_old_prev"] if self.wake_call else (OLD_STABLE_TOKENS if fresh else 0))
        self.stats["_old_prev"] = old
        s = self.stats
        s["calls"] += 1
        s["tokens_in"] += tokens
        s["tokens_cached"] += cached
        s["old_in"] += old
        s["old_cached"] += old_cached
        s["page_peak"] = max(s["page_peak"], tokens)
        s["first_call_cached"].append(cached / tokens if self.wake_call == 0 else None)
        self.last_prompt, self.last_call = text, Clock.t
        self.wake_call += 1
        Clock.t += timedelta(seconds=self.rng.uniform(5, 40))       # a call takes a while
        if self.plan:
            answer = self.plan.pop(0)
        else:
            p, rng = PROFILE, self.rng
            if self.chatting:
                minutes = rng.choice([10, 30, 60, 240, 600, 1440])
            else:
                minutes = 1440 if rng.random() < 0.85 else rng.choice([30, 60, 240])
            answer = {"thought": filler(rng, draw(rng, p["thought"])),
                      "message": filler(rng, draw(rng, p["sent"])) if self.chatting or rng.random() < 0.2 else None,
                      "notes": filler(rng, draw(rng, p["notes"])) if rng.random() < p["notes_share"] else None,
                      "next_wake_minutes": minutes}
        return Reply(json.dumps(answer), "sim", tokens, 200, cached_tokens=cached)

    def summarize(self, messages):
        from ahab.model import ModelError, Reply
        from ahab.session import SUMMARY_CHAR_LIMIT
        s = self.stats
        s["summary_calls"] += 1
        page = messages[1]["content"]
        before, _, folded = page.partition("## The older part of the conversation")
        s["summary_in"] += len(page) // 4
        if Clock.t < self.down_until or self.rng.random() < self.fail:
            s["summary_failures"] += 1
            raise ModelError("simulated: the summarizer is down")
        previous = len(before.split("## The summary so far\n", 1)[-1])
        # A summary grows with what it already holds and what's folded in, and now and then
        # runs past its cap, as real ones do.
        size = int(previous * 0.75 + len(folded) * 0.03 + 1500 + self.rng.gauss(0, 600))
        if self.rng.random() < 0.05:
            size = int(SUMMARY_CHAR_LIMIT * 1.3)
        return Reply(filler(self.rng, max(200, size)), "sim", len(page) // 4, size // 4)


SENTENCES = [
    "The old town is quietest before nine in the morning, when most day trippers are still at breakfast.",
    "A small bakery near the river sells custard tarts that are still warm until about ten.",
    "Trams in the hill neighborhoods get very full by midday; walking the back streets is faster.",
    "The museum is closed on Mondays and opens at ten the rest of the week.",
    "Last updated in the spring, this file lists the places worth a second visit.",
    "Weather in March is mild, with showers likely, so a light rain jacket is enough.",
    "Trains to the coast leave from the main station every thirty minutes and take about forty.",
    "Notes: confirm the booking by email, keep a paper copy of the confirmation, and check the check-in time.",
    "The essay draft has four sections; the second one still needs a clearer opening paragraph.",
    "A reminder was set for the end of the month; nothing else is pending on this list.",
    "Prices listed here were checked recently, but hotels change them often, so check again before booking.",
    "No errors were found. The file contains three entries, each with a date and a short description.",
]


def readable(rng: random.Random, chars: int) -> str:
    """Plain sentences up to a length: what a tool result looks like to a real model."""
    out, size = [], 0
    while size < chars:
        s = rng.choice(SENTENCES)
        out.append(s)
        size += len(s) + 1
    return " ".join(out)[:max(chars, 40)]


class BudgetSpent(BaseException):
    """The run's spending cap was reached. Not an Exception on purpose: the heartbeat's own
    error handling must not swallow it and carry on spending."""


def prices(model: str) -> tuple[float, float, float]:
    """(dollars per input token, per output token, per cached input token) from OpenRouter's
    public list; zeros if unknown."""
    import urllib.request
    try:
        data = json.load(urllib.request.urlopen("https://openrouter.ai/api/v1/models", timeout=30))["data"]
        for m in data:
            if m["id"] == model:
                p = m["pricing"]
                return float(p["prompt"]), float(p["completion"]), float(p.get("input_cache_read") or p["prompt"])
    except Exception:
        pass
    return 0.0, 0.0, 0.0


def parse_balance(text: str) -> float | None:
    """The USD balance out of DeepSeek's /user/balance reply; None if it isn't there."""
    try:
        infos = json.loads(text)["balance_infos"]
        for info in infos:
            if info.get("currency") == "USD":
                return float(info["total_balance"])
    except (ValueError, KeyError, TypeError, AttributeError):
        pass
    return None


def deepseek_balance() -> float | None:
    """Read-only look at the DeepSeek account's balance, to the cent; None if it can't be read."""
    import urllib.request
    from ahab.model import BACKENDS
    config = BACKENDS["deepseek"]
    key = os.environ.get(config["api_key_env"])
    if not key:
        return None
    request = urllib.request.Request(config["base_url"].rstrip("/") + "/user/balance",
                                     headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=20) as reply:
            return parse_balance(reply.read().decode("utf-8"))
    except Exception:
        return None


class BalanceMeter:
    """Spend measured as the drop in the account balance, since DeepSeek's replies carry tokens
    but no price. The balance is only good to the cent and one account serves every run, so it
    is read every EVERY calls, not each one, and only one real run at a time is meaningful."""

    EVERY = 40

    def __init__(self, read=deepseek_balance):
        self.read, self.calls = read, 0
        self.start = read()

    def tick(self, stats: dict, force: bool = False) -> None:
        if self.start is None:
            return
        self.calls += 1
        if force or self.calls % self.EVERY == 0:
            now = self.read()
            if now is not None:
                stats["cost"] = max(0.0, self.start - now)
                stats["balance_now"] = now


class RealModel:
    """A real model behind the same seam: its own words, its own token counts. The sim's
    estimate (a quarter of the characters) is kept beside the real count, to calibrate it."""

    can_see = False

    def __init__(self, client, stats: dict, price=(0.0, 0.0, 0.0), max_cost: float | None = None, meter=None):
        self.client, self.stats = client, stats
        self.price, self.max_cost, self.meter = price, max_cost, meter
        self.backend = client.backend
        self.last_call = None
        self.wake_first = None
        self.phase = "wake"     # "dream" while the real dream runs, so its calls are counted apart

    def start_wake(self, chatting: bool) -> None:
        self.first = True

    @property
    def failovers(self):
        return getattr(self.client, "failovers", [])

    def complete(self, messages, temperature=0.7, json_mode=False):
        from ahab import cli_model
        began = time.perf_counter()
        reply = self.client.complete(messages, temperature=temperature, json_mode=json_mode)
        took = time.perf_counter() - began
        s = self.stats
        key = "summary" if not json_mode else "calls"
        system, prompt, _ = cli_model.flatten(messages)
        s["est_in"] += (len(system) + len(prompt)) // 4
        sizes = [len(m["content"]) if isinstance(m["content"], str) else len(str(m["content"])) for m in messages]
        entry = {"t": Clock.t.isoformat(timespec="seconds"), "phase": self.phase, "json": json_mode,
                 "in": reply.prompt_tokens, "cached": reply.cached_tokens, "out": reply.completion_tokens,
                 "est": sum(sizes) // 4, "n": len(messages), "max_chars": max(sizes), "max_at": sizes.index(max(sizes))}
        if reply.prompt_tokens > 3 * max(1, entry["est"]) and len(s["spikes"]) < 5:
            s["spikes"].append({**entry, "head": str(messages[entry["max_at"]]["content"])[:300]})
        s["call_log"].append(entry)
        s["real_in"] += reply.prompt_tokens
        s["api_seconds"] += took
        s["api_calls"] += 1
        if self.phase == "dream":
            s["dream_calls"] += 1
            s["dream_in"] += reply.prompt_tokens
            s["dream_out"] += reply.completion_tokens
        elif json_mode:
            if getattr(self, "first", False):
                s["first_call_cached"].append(reply.cached_tokens / max(1, reply.prompt_tokens))
                self.first = False
            s["calls"] += 1
            s["tokens_in"] += reply.prompt_tokens
            s["tokens_cached"] += reply.cached_tokens
            s["tokens_out"] += reply.completion_tokens
            s["page_peak"] = max(s["page_peak"], reply.prompt_tokens)
        else:
            s["summary_calls"] += 1
            s["summary_in"] += reply.prompt_tokens
        s["cost"] += ((reply.prompt_tokens - reply.cached_tokens) * self.price[0]
                      + reply.cached_tokens * self.price[2] + reply.completion_tokens * self.price[1])
        s["tokens_out"] += 0 if json_mode or self.phase == "dream" else reply.completion_tokens
        Clock.t += timedelta(seconds=10)
        if self.meter:
            self.meter.tick(s)
        if self.max_cost is not None and s["cost"] > self.max_cost:
            raise BudgetSpent(f"${s['cost']:.3f} spent, over the ${self.max_cost:.2f} cap")
        return reply


# Tools that only touch the throwaway home (its files, log, alarms): safe to run for real when a real
# model is playing, so what it writes down is actually there to read back. Everything that reaches
# outward (web, mail, shell, boxes, Google) stays a stand-in.
LOCAL_TOOLS = frozenset({"message_owner", "about_yourself", "recall", "read_conversation", "list_files",
                         "read_file", "write_file", "edit_file", "set_alarm", "cancel_alarm",
                         "set_intent", "cancel_intent"})


def fake_tools(rng: random.Random, readable_results: bool = False, real_local: bool = False):
    """tools.run with the real message_owner (it only writes to memory.db) and junk of real sizes otherwise.
    With real_local, the home's own file, recall and alarm tools run for real too."""
    from ahab import tools
    real = tools.__dict__.setdefault("_real_run", tools.run)    # not a fake from an earlier run
    local = LOCAL_TOOLS if real_local else {"message_owner"}

    def run(memory, name, args):
        if name in local:
            return real(memory, name, args)
        size = draw(rng, PROFILE["result"])
        return readable(rng, size) if readable_results else filler(rng, size)
    return run


# --- probes and the real dream ---------------------------------------------------------

LOOKUPS = ("recall", "read_file", "read_conversation", "about_yourself")


def count_folds(memory) -> int:
    return memory.db.execute("SELECT COUNT(*) FROM events WHERE kind = 'session' AND content LIKE 'Folded%'"
                             ).fetchone()[0]


def open_probe(memory, meta: dict, stats: dict) -> dict:
    """A question about an older fact has just been put to Pip; its answer is looked for in the next few wake-ups."""
    last = memory.db.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]
    probe = {"fact": meta["fact"], "age_folds": meta["age_folds"], "updated": meta["updated"],
             "expect": meta["expect"], "replied": False, "passed": False, "looked_up": False, "answer": "",
             "_after": last, "_left": 3, "_text": ""}
    stats.setdefault("_open", []).append(probe)
    return probe


def close_probes(memory, stats: dict, final: bool = False) -> None:
    """Gather what Pip has said to the owner since each open probe, and score it once it has had three wake-ups."""
    still = []
    for p in stats.get("_open", []):
        rows = memory.db.execute("SELECT id, kind, content FROM events WHERE id > ? AND kind IN ('message', 'tool') "
                                 "ORDER BY id", (p["_after"],)).fetchall()
        for id_, kind, content in rows:
            p["_after"] = id_
            if kind == "message" and content.startswith("To the owner"):
                p["_text"] += " " + content
                p["replied"] = True
            elif kind == "tool" and content.split(" ", 1)[0] in LOOKUPS:
                p["looked_up"] = True
        p["_left"] -= 1
        if p["_left"] > 0 and not final:
            still.append(p)
            continue
        p["passed"] = p["replied"] and score(p["expect"], p["_text"])
        p["answer"] = p.pop("_text").strip()[:400]
        for key in ("_after", "_left"):
            p.pop(key)
    stats["_open"] = still


def dream_for_real(memory, model, stats: dict) -> bool:
    """The morning dream with the real model; its calls are counted apart. True if the money ran out."""
    from ahab import dream
    model.phase = "dream"
    try:
        if dream.dream(memory, model, "Ahab"):
            stats["dreams"] += 1
    except BudgetSpent as e:
        stats["stopped"] = str(e)
        return True
    except Exception as e:      # the heartbeat shrugs a bad dream off too
        stats["dream_failures"] += 1
        memory.record("error", f"Dream crashed: {type(e).__name__}: {e}")
    finally:
        model.phase = "wake"
    return False


# --- one run ----------------------------------------------------------------------------

def check(memory, stats: dict, limit: int) -> None:
    """What must hold after every wake-up."""
    from ahab import session
    db, bad = memory.db, stats["violations"]
    split = db.execute("SELECT wake FROM session WHERE role != 'summary' GROUP BY wake "
                       "HAVING COUNT(DISTINCT folded) > 1").fetchall()
    if split:
        bad.append(f"wake-up(s) split by a fold: {split[:3]}")
    newest = db.execute("SELECT MAX(wake) FROM session WHERE role != 'summary'").fetchone()[0]
    if newest and db.execute("SELECT COUNT(*) FROM session WHERE wake = ? AND folded = 1 AND role != 'summary'",
                             (newest,)).fetchone()[0]:
        bad.append(f"the newest wake-up ({newest}) was folded")
    summaries = db.execute("SELECT COUNT(*) FROM session WHERE role = 'summary' AND folded = 0").fetchone()[0]
    if summaries > 1:
        bad.append(f"{summaries} summaries on the page")
    text = session.summary(memory) or ""
    if len(text) > session.SUMMARY_CHAR_LIMIT + 40:
        bad.append(f"summary of {len(text)} chars, over its cap")
    size = session.size(memory)
    stats["session_peak"] = max(stats["session_peak"], size)
    newest_chars = db.execute("SELECT COALESCE(SUM(LENGTH(content)), 0) FROM session WHERE wake = ? "
                              "AND role != 'summary'", (newest or 0,)).fetchone()[0]
    if size > limit * session.HARD_SHARE + newest_chars // 4 + 3_500:
        bad.append(f"session at {size} tokens after a wake-up, past {session.HARD_SHARE}x its size")
    stats["summary_sizes"].append(len(text))


def snapshot(memory, stats: dict, week: int) -> dict:
    """Where things stand at a week's end; the differences between rows are the trend."""
    from ahab import session
    done = len(stats["weekly"])
    walls = stats["wall"][stats.get("_wall_mark", 0):]
    stats["_wall_mark"] = len(stats["wall"])
    folds = memory.db.execute("SELECT COUNT(*) FROM events WHERE kind = 'session' AND content LIKE 'Folded%'"
                              ).fetchone()[0]
    return {"week": week, "wakes": stats["wakes"], "calls": stats["calls"], "tokens_in": stats["tokens_in"],
            "tokens_cached": stats["tokens_cached"], "summary_in": stats["summary_in"],
            "session_tokens": session.size(memory), "summary_chars": len(session.summary(memory) or ""),
            "session_rows": memory.db.execute("SELECT COUNT(*) FROM session").fetchone()[0],
            "events": memory.db.execute("SELECT COUNT(*) FROM events").fetchone()[0],
            "folds": folds, "summary_failures": stats["summary_failures"],
            "db_mb": (Path(memory.home) / "memory.db").stat().st_size / 1e6 if hasattr(memory, "home") else None,
            "ms_a_wake": 1000 * statistics.mean(walls) if walls else None,
            "page_peak": stats["page_peak"]}


def simulate(seed: int, days: float, owner: str, limit: int, fail: float, outages: float,
             cache_minutes: float, keep_home: bool = False, backend: str | None = None,
             model_name: str | None = None, max_cost: float | None = None,
             effort: str | None = None, client=None, meter=None) -> dict:
    from ahab import heartbeat, inbox, session, tools
    from ahab.memory import Memory
    patch_clock()
    rng = random.Random(seed)
    Clock.t = START
    end = START + timedelta(days=days)
    habits = OWNERS[owner]
    stats = {"seed": seed, "wakes": 0, "calls": 0, "tokens_in": 0, "tokens_cached": 0, "old_in": 0,
             "old_cached": 0, "_old_prev": 0, "page_peak": 0, "session_peak": 0, "summary_calls": 0,
             "summary_in": 0, "summary_failures": 0, "owner_messages": 0, "violations": [],
             "summary_sizes": [], "first_call_cached": [], "wall": [], "weekly": [],
             "est_in": 0, "real_in": 0, "api_seconds": 0.0, "api_calls": 0, "tokens_out": 0, "cost": 0.0,
             "call_log": [], "spikes": [], "dream_calls": 0, "dream_in": 0, "dream_out": 0, "dreams": 0, "dream_failures": 0, "probes": [],
             "stopped": None}
    real = bool(backend or client)
    if real:
        from ahab.model import BACKENDS, ModelClient
        overrides = {"model": model_name} if model_name else {}
        if effort and backend == "openrouter":
            overrides["extra"] = {**BACKENDS["openrouter"]["extra"], "reasoning": {"effort": effort}}
        elif effort and "cli" in BACKENDS.get(backend, {}):
            overrides["effort"] = effort
        elif effort:        # a plain API backend (DeepSeek): the effort rides in the request body
            overrides["extra"] = {**BACKENDS[backend].get("extra", {}), "reasoning_effort": effort}
        if client is None:
            client = ModelClient.from_backend(backend, timeout=1200, **overrides)
            if backend == "deepseek" and meter is None:
                meter = BalanceMeter()
        stats["model"] = client.model
        stats["effort"] = effort or "default"
        model = RealModel(client, stats, prices(client.model) if backend == "openrouter" else (0.0, 0.0, 0.0),
                          max_cost, meter)
        owner = Owner(seed)
    else:
        model = FakeModel(rng, fail, cache_minutes, stats)
    sent = 0
    tools.run = fake_tools(rng, readable_results=real, real_local=real)
    home = Path(tempfile.mkdtemp(prefix="ahab-sim-"))
    memory = Memory(home)
    # A throwaway home needn't survive a power cut; flushing every commit to disk was 80% of the time.
    memory.db.execute("PRAGMA synchronous = OFF")
    memory.db.execute("PRAGMA journal_mode = MEMORY")
    prompt = heartbeat.system_prompt("Ahab")

    def next_chat(after):
        gap = rng.expovariate(habits["chats_per_day"]) * 86_400
        if rng.random() < habits["away_chance"] * max(1.0, gap / 86_400):
            gap += rng.uniform(*habits["away_days"]) * 86_400
        t = after + timedelta(seconds=gap)
        if 4 <= t.hour < 12:                   # the owner sleeps (UTC night for an American owner)
            t = t.replace(hour=12) + timedelta(minutes=rng.uniform(0, 120))
        return t

    pip_at, owner_at, left_in_chat, dreamed = START, next_chat(START), 0, START.date()
    seen_folds = 0
    try:
        while Clock.t < end:
            Clock.t = min(pip_at, owner_at)
            if Clock.t.date() != dreamed and Clock.t.hour >= 9:      # the morning dream, into the log
                dreamed = Clock.t.date()
                if real:
                    if dream_for_real(memory, model, stats):
                        break
                else:
                    memory.record("dream", f"Dreamed over yesterday.\n{filler(rng, 1500)}")
            chatting = Clock.t == owner_at
            if chatting:
                if left_in_chat == 0:
                    left_in_chat = max(1, int(rng.expovariate(1 / habits["messages_per_chat"])) + 1)
                if real:
                    text, meta = owner.next(count_folds(memory))
                    if meta["kind"] == "probe":
                        stats["probes"].append(open_probe(memory, meta, stats))
                else:
                    text = filler(rng, draw(rng, PROFILE["owner"]))
                sent += 1
                inbox.owner_sends(memory, text, knock=True)
                stats["owner_messages"] += 1
                left_in_chat -= 1
                Clock.t += timedelta(seconds=rng.uniform(3, 15))        # the knock wakes it
            if outages and rng.random() < outages / 24:
                model.down_until = Clock.t + timedelta(hours=rng.uniform(2, 12))
            model.start_wake(chatting)
            began = time.perf_counter()
            try:
                minutes = heartbeat.beat(memory, model, prompt, "Ahab", limit)
            except BudgetSpent as e:
                stats["stopped"] = str(e)
                break
            stats["wall"].append(time.perf_counter() - began)
            stats["wakes"] += 1
            if real:
                folds_now = count_folds(memory)
                if folds_now > seen_folds:         # a fold just happened: the owner will ask about older things
                    seen_folds = folds_now
                    owner.on_fold(folds_now)
                close_probes(memory, stats)
            if stats["wakes"] % CHECK_EVERY == 0:    # the check scans the whole session table: not every wake
                check(memory, stats, limit)
            week = int((Clock.t - START).days // 7)
            if week > len(stats["weekly"]):          # one snapshot a week, for trends over time
                stats["weekly"].append(snapshot(memory, stats, week))
            pip_at = Clock.t + timedelta(minutes=minutes)
            if chatting:
                owner_at = (Clock.t + timedelta(seconds=rng.lognormvariate(math.log(150), 0.8))
                            if left_in_chat else next_chat(Clock.t))
            elif owner_at < Clock.t:
                owner_at = Clock.t
        check(memory, stats, limit)
        if real:
            close_probes(memory, stats, final=True)
            if model.meter:
                model.meter.tick(stats, force=True)
        rows =memory.db.execute("SELECT kind, content FROM events WHERE kind IN ('session', 'error')").fetchall()
        stats["folds"] = sum(1 for k, c in rows if k == "session" and c.startswith("Folded") and "without" not in c)
        stats["hard_folds"] = sum(1 for k, c in rows if k == "session" and "without a summary" in c)
        stats["fold_errors"] = sum(1 for k, c in rows if k == "error" and "Folding your session crashed" in c)
        stats["session_rows"] = memory.db.execute("SELECT COUNT(*) FROM session").fetchone()[0]
        stats["db_mb"] = (home / "memory.db").stat().st_size / 1e6
    finally:
        memory.close()
        if not keep_home:
            shutil.rmtree(home, ignore_errors=True)
        else:
            stats["home"] = str(home)
    stats["days"] = days
    return stats


def _run(args):
    return simulate(*args)


# --- the report ---------------------------------------------------------------------------

def summarize_runs(runs: list[dict], limit: int) -> str:
    def spread(values, fmt="{:,.0f}"):
        values = sorted(values)
        p95 = values[min(len(values) - 1, int(0.95 * len(values)))]
        return f"{fmt.format(statistics.median(values)):>12} {fmt.format(p95):>12} {fmt.format(values[-1]):>12}"

    per_day = lambda r, key: r[key] / r["days"]
    lines = [f"{'':40} {'median':>12} {'p95':>12} {'worst':>12}"]
    row = lambda label, values, fmt="{:,.0f}": lines.append(f"{label:40} {spread(values, fmt)}")
    row("wake-ups a day", [r["wakes"] / r["days"] for r in runs], "{:,.1f}")
    row("owner messages a day", [r["owner_messages"] / r["days"] for r in runs], "{:,.1f}")
    row("folds a week", [r["folds"] / r["days"] * 7 for r in runs], "{:,.2f}")
    row("folds without a summary (whole run)", [r["hard_folds"] for r in runs])
    row("failed summaries (whole run)", [r["summary_failures"] for r in runs])
    row("biggest page, tokens", [r["page_peak"] for r in runs])
    row(f"biggest session, tokens (size {limit:,})", [r["session_peak"] for r in runs])
    row("summary length at the end, chars", [r["summary_sizes"][-1] for r in runs])
    row("longest summary, chars", [max(r["summary_sizes"]) for r in runs])
    row("input tokens a day", [per_day(r, "tokens_in") for r in runs])
    row("  of them not cached", [per_day(r, "tokens_in") - per_day(r, "tokens_cached") for r in runs])
    row("  plus summary calls", [per_day(r, "summary_in") for r in runs])
    row("old page: input tokens a day (est.)", [per_day(r, "old_in") for r in runs])
    row("old page:   not cached (est.)", [per_day(r, "old_in") - per_day(r, "old_cached") for r in runs])
    row("first call of a wake: share cached", [statistics.mean([x for x in r["first_call_cached"] if x is not None] or [0])
                                              for r in runs], "{:.0%}")
    row("session rows at the end", [r["session_rows"] for r in runs])
    row("memory.db at the end, MB", [r["db_mb"] for r in runs], "{:.1f}")
    row("ms a wake-up, first tenth", [1000 * statistics.mean(r["wall"][:max(1, len(r["wall"]) // 10)])
                                      for r in runs], "{:,.1f}")
    row("ms a wake-up, last tenth", [1000 * statistics.mean(r["wall"][-max(1, len(r["wall"]) // 10):])
                                     for r in runs], "{:,.1f}")
    row("fold crashes", [r["fold_errors"] for r in runs])
    if any(r["api_calls"] for r in runs):
        row("real model: cost, $ (OpenRouter est.)", [r["cost"] for r in runs], "{:.3f}")
        row("real model: seconds a call", [r["api_seconds"] / max(1, r["api_calls"]) for r in runs], "{:,.1f}")
        row("real model: tokens counted / estimated", [r["real_in"] / max(1, r["est_in"]) for r in runs], "{:.2f}")
    probes = [p for r in runs for p in r.get("probes", [])]
    if probes:
        lines.append("")
        pct = lambda ps: f"{sum(p['passed'] for p in ps)}/{len(ps)}" + (f" ({sum(p['passed'] for p in ps) / len(ps):.0%})" if ps else "")
        lines.append(f"Questions asked after a fold, answered right: {pct(probes)}; "
                     f"no reply at all: {sum(not p['replied'] for p in probes)}")
        for label, ps in (("  told since the last fold", [p for p in probes if p["age_folds"] <= 1]),
                          ("  told 2 or more folds back", [p for p in probes if p["age_folds"] >= 2]),
                          ("  facts the owner later changed", [p for p in probes if p["updated"]])):
            lines.append(f"{label:40} {pct(ps)}")
        lines.append(f"{'  right after looking it up':40} {pct([p for p in probes if p['looked_up']])}")
        lines.append(f"{'  right from the session alone':40} {pct([p for p in probes if not p['looked_up']])}")
    if any(r.get("dreams") or r.get("dream_failures") for r in runs):
        lines.append(f"Dreams done: {sum(r['dreams'] for r in runs)}, crashed: {sum(r['dream_failures'] for r in runs)}; "
                     f"{sum(r['dream_calls'] for r in runs):,} calls, {sum(r['dream_in'] for r in runs):,} tokens in, "
                     f"{sum(r['dream_out'] for r in runs):,} out")
    bad = [(r["seed"], v) for r in runs for v in r["violations"]]
    lines.append("")
    lines.append(f"Broken rules: {len(bad)}" + ("" if not bad else ", first ones:"))
    for seed, v in Counter(bad).most_common(8):
        lines.append(f"  seed {seed[0]}: {seed[1]} (x{v})")
    return "\n".join(lines)


def measure(db_path: Path) -> dict:
    """A PROFILE from a real memory.db: sizes and counts only."""
    import sqlite3
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    wakes, current = [], None
    for kind, content in db.execute("SELECT kind, content FROM events ORDER BY id"):
        if kind == "wake":
            current = {"tools": [], "thought": 0, "sent": 0, "owner": 0, "notes": 0}
            wakes.append(current)
        elif current is None:
            continue
        elif kind == "tool":
            head, _, rest = content.partition("\n")
            args = rest.split("\n", 1)[0][6:] if rest.startswith("args:") else ""
            body = rest.split("\n", 1)[1] if "\n" in rest else ""
            if head.split(" ", 1)[0] == "message_owner":
                try:
                    current["sent"] += len(json.loads(args).get("text", ""))
                except ValueError:
                    pass
            else:
                current["tools"].append((len(args), len(body)))
        elif kind == "thought":
            current["thought"] = max(current["thought"], len(content))
        elif kind == "notes":
            current["notes"] = len(content)
        elif kind == "message" and content.startswith("From the owner"):
            current["owner"] += 1
    owner = [len(c.split("\n", 1)[-1]) for (c,) in db.execute(
        "SELECT content FROM events WHERE kind = 'message' AND content LIKE 'From the owner%'")]
    db.close()

    def q(values, n=20):
        values = sorted(values) or [0]
        return [values[min(len(values) - 1, i * len(values) // n)] for i in range(n)]

    counts = lambda ws: sorted([list(x) for x in Counter(len(w["tools"]) for w in ws).items()]) or [[0, 1]]
    return {"chat_tools": counts([w for w in wakes if w["owner"]]),
            "alone_tools": counts([w for w in wakes if not w["owner"]]),
            "result": q([b for w in wakes for _, b in w["tools"]]),
            "args": q([a for w in wakes for a, _ in w["tools"]]),
            "thought": q([w["thought"] for w in wakes if w["thought"]]),
            "sent": q([w["sent"] for w in wakes if w["sent"]]),
            "owner": q(owner),
            "notes_share": round(sum(1 for w in wakes if w["notes"]) / max(1, len(wakes)), 2),
            "notes": q([w["notes"] for w in wakes if w["notes"]], 10)}


def save(args, limit: int, runs: list[dict]) -> Path:
    """Every run's raw numbers, with the settings and the profile that made them, gzipped JSON.
    Sizes and counts only: nothing of any real conversation is in it."""
    import gzip
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    label = args.label or f"{args.owner}-{args.days:g}d-{limit // 1000}k"
    path = out / f"{_datetime.now():%Y%m%d-%H%M}-{label}.json.gz"
    clean = [{k: v for k, v in r.items() if not k.startswith("_")} for r in runs]
    settings = {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()}
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump({"settings": settings, "limit": limit, "profile": PROFILE, "runs": clean}, f)
    return path


def main() -> None:
    sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description="Simulate Ahab's running session over months, offline.")
    parser.add_argument("--runs", type=int, default=50)
    parser.add_argument("--days", type=float, default=90)
    parser.add_argument("--owner", choices=sorted(OWNERS), default="busy")
    parser.add_argument("--limit", type=int, default=None, help="session size in tokens (default: Ahab's)")
    parser.add_argument("--fail", type=float, default=0.03, help="chance a summary call fails")
    parser.add_argument("--outages", type=float, default=0.05, help="chance a day that the summarizer goes down for hours")
    parser.add_argument("--cache-minutes", type=float, default=10, help="how long a provider keeps a prompt cached")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--workers", type=int, default=max(1, (multiprocessing.cpu_count() or 2) - 1))
    parser.add_argument("--out", type=Path, default=ROOT / "sim_results", help="where the raw data of the runs is saved")
    parser.add_argument("--backend", help="a real model from ahab/model.py's BACKENDS (local, openrouter, deepseek) "
                                          "instead of the fake; the owner then speaks from a script")
    parser.add_argument("--model", help="with --backend openrouter: which model, e.g. openai/gpt-oss-20b")
    parser.add_argument("--effort", help="reasoning effort: medium for a CLI backend (codex-luna, claude-*), low/high/max for deepseek; "
                        "unset leaves the backend's own default (deepseek: thinking on, high)")
    parser.add_argument("--max-cost", type=float, default=0.50,
                        help="stop a real-model run when its estimated cost passes this many dollars")
    parser.add_argument("--keep-homes", action="store_true",
                        help="keep each run's throwaway home (in the temp folder) so you can open its memory.db")
    parser.add_argument("--label", help="a name for this batch in the saved file (default: owner, days, size)")
    parser.add_argument("--profile", type=Path, help="measure wake-up sizes from this memory.db instead")
    parser.add_argument("--show-profile", action="store_true", help="print the profile and stop")
    args = parser.parse_args()
    from ahab import session
    limit = args.limit or session.DEFAULT_TOKENS
    if args.profile:
        PROFILE.update(measure(args.profile))
    if args.show_profile:
        print(json.dumps(PROFILE))
        return
    jobs = [(args.seed + i, args.days, args.owner, limit, args.fail, args.outages, args.cache_minutes,
             args.keep_homes, args.backend, args.model, args.max_cost, args.effort)
            for i in range(args.runs)]
    began = time.perf_counter()
    if args.workers > 1 and args.runs > 1:
        with multiprocessing.Pool(args.workers, initializer=PROFILE.update, initargs=(dict(PROFILE),)) as pool:
            runs = []
            for run in pool.imap_unordered(_run, jobs):
                runs.append(run)
                print(f"  run {len(runs)}/{len(jobs)} done (seed {run['seed']}, {run['wakes']} wake-ups, "
                      f"{run['folds']} folds, {len(run['violations'])} broken rules)", file=sys.stderr, flush=True)
    else:
        runs = [_run(job) for job in jobs]
    saved = save(args, limit, runs)
    print(f"Raw data: {saved}")
    print(f"{args.runs} run(s) of {args.days:g} days, owner '{args.owner}', session size {limit:,} tokens, "
          f"{args.fail:.0%} of summaries failing, outages {args.outages:.0%} a day "
          f"({time.perf_counter() - began:,.0f}s)\n")
    print(summarize_runs(runs, limit))
    print("\nThe old page's numbers are an estimate (about 10.7k tokens a call, only its top reused between "
          "wake-ups). The summaries are filler of a real size: what they keep isn't tested here.")


if __name__ == "__main__":
    main()
