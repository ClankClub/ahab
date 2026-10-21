"""The loop a helper runs: one model, one job, one tool at a time, until it finishes.

Workers (workers.py) and crew agents (agents.py) both work this way. The
caller builds the page (the system prompt and the job) and says which tools
are allowed, how many calls, until when, where the steps get logged and how
to tell it was cancelled. This runs the loop: the JSON reply form, one retry
for a reply that isn't JSON, the call budget, the clock, the trimming of old
results, and pictures attached by code (senses.py). It never decides what a
helper may do; the caller's list does, and a tool not on it is refused here.

A helper finishes by replying without a "tool": that last reply is the
Outcome's `final`, and the caller reads its own fields from it (a worker's
report, a crew agent's report and notes).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from ahab import senses

TIME_UP = ("\n\nTime's up: no more tools. Write your final reply now, with your report on what "
           "you have so far and what you didn't get to.")
OUT_OF_CALLS = ("REFUSED: that call was NOT run: you've used all {n} of your tool calls. Write your "
                "final reply now, with your report on what you have and what you didn't get to.")


@dataclass
class Outcome:
    status: str                       # done, failed, timed_out, cancelled
    final: dict = field(default_factory=dict)
    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    summary: str = ""                 # what went wrong, in a line ("" when done)


def _first_line(text: str, limit: int) -> str:
    line = (text.strip().splitlines() or [""])[0]
    return line if len(line) <= limit else line[:limit] + "..."


def run(memory, client, messages: list[dict], form: dict, allowed, max_calls: int,
        deadline: datetime, step: Callable[[str, str], object],
        cancelled: Callable[[], bool] = lambda: False, minutes: int = 0) -> Outcome:
    """Run until the helper finishes, runs out, is cancelled or breaks the form.

    memory is what the tools see (a worker's view of Ahab's memory, or a crew
    agent's own). step(kind, content) logs each thought, tool call and error.
    """
    from ahab import heartbeat, tools
    allowed = tuple(allowed)
    out = Outcome("failed")
    asked_again = warned = False
    for _ in range(max_calls + 4):
        if cancelled():
            out.status, out.summary = "cancelled", "Cancelled by you."
            return out
        if datetime.now(timezone.utc) > deadline:
            if warned:
                out.status = "timed_out"
                out.summary = f"It ran out of its {minutes} minutes without a report."
                return out
            warned = True
            messages[-1]["content"] += TIME_UP
        reply = client.complete(messages, json_mode=form)
        out.tokens_in += reply.prompt_tokens
        out.tokens_out += reply.completion_tokens
        if reply.reasoning:
            step("reasoning", reply.reasoning)
        try:
            decision, dropped = heartbeat.parse_reply(reply.text)
        except ValueError:
            if asked_again or not reply.text.strip():
                raise
            asked_again = True
            step("error", f"Reply wasn't one JSON object; asked again: {reply.text[:2000]}")
            messages.append({"role": "assistant", "content": reply.text})
            messages.append({"role": "user", "content": heartbeat.NOT_JSON})
            continue
        step("thought", str(decision.get("thought", "")).strip() or "(empty)")
        if "tool" not in decision:
            out.final = decision
            if not str(decision.get("report") or "").strip():
                out.status, out.summary = "failed", "It finished without writing a report."
            else:
                out.status = "done"
            return out
        tool, args = str(decision["tool"]), decision.get("args", {})
        if warned:
            result = TIME_UP.strip()
        elif tool not in allowed:
            result = f"{tool!r} isn't one of your tools. Yours: {', '.join(allowed)}."
        elif out.calls >= max_calls:
            result = OUT_OF_CALLS.format(n=max_calls)
        else:
            result = tools.run(memory, tool, args)
            out.calls += 1
        args_json = json.dumps(args, ensure_ascii=False)
        step("tool", f"{tool} {args_json[:200]} -> {_first_line(result, 200)}\nargs: {args_json}\n{result}")
        heartbeat.trim_last_result(messages)
        messages.append({"role": "assistant", "content": reply.text})
        messages.append({"role": "user", "content": f"Tool result:\n{result}"})
        senses.attach(messages[-1], senses.take_shown(memory))
    out.status, out.summary = "failed", "It kept asking for tools past its limit without finishing."
    return out
