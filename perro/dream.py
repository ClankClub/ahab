"""Dreaming: once a day, Perro looks back over the past day as a whole.

An ordinary wake-up is busy: a message comes in, Perro does the thing, and
rewrites its notes in a hurry before sleeping. Nothing ever steps back and
sorts what piled up. A dream is that step back, the way agents like OpenClaw
and Muse do it: one unhurried pass early each morning that
  - rewrites the notes down to what's still open,
  - moves what lasts into its files (USER.md, PROACTIVE_PREFERENCES.md,
    AGENTS.md, skills, and SOUL.md, which it then tells the owner about),
  - and writes a short dated reflection in workspace/dreams/.

The dream sees the conversation, Perro's own thoughts, one line per tool it
used, and errors. It doesn't see what tools returned (web pages, emails,
files), on purpose: the dream is where Perro decides what to keep about
itself and its owner, and a sentence planted in a web page shouldn't get a
say in that. It can read its workspace, search its log and read the guide to
itself (to check any "can't do" its notes claim), and nothing else: no
messages, no web, no Google.

Files it changes come back inside its final answer, not through write_file.
In testing, DeepSeek wrote "I've put a line in SOUL.md" without ever making
the call, and a dream that leaves false memories is worse than none. This
way a change is either in the answer, and gets written, or it didn't happen;
the dream event lists the files that really changed.

The heartbeat calls this at DREAM_HOUR local time, or as soon as it can after
(a PC that was off at 7 dreams when it comes back). A day with no
conversation is skipped, so a quiet day costs nothing.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from perro import identity, skills, tools
from perro.memory import NOTES_CHAR_LIMIT, Memory
from perro.model import ModelClient, ModelError

# 7am: inside the owner's quiet hours, and after DeepSeek's weekday peak
# price ends (10:00 UTC is 6am Eastern in summer, 5am in winter).
DREAM_HOUR = 7
STATE_FILE = "dream.state"   # in the home: which morning was last handled
LOOK_BACK_HOURS = 48         # never read further back than this
DAY_CHAR_LIMIT = 60_000      # about 15k tokens of the day, at most
THOUGHT_CHAR_LIMIT = 600
LINE_CHAR_LIMIT = 250
ENTRY_CHAR_LIMIT = 8_000
MAX_DREAM_TOOL_CALLS = 8
# about_yourself so a "can't do" in the notes gets checked, not carried: on
# 2026-10-18 a wrong one ("the Sheets tools aren't callable") sat in the notes
# and was believed again every wake.
DREAM_TOOLS = ("list_files", "read_file", "recall", "about_yourself")
MAX_FILES_CHANGED = 6
# Kinds the dream doesn't need: bookkeeping, private reasoning (long, and
# not what Perro decided), old notes (it gets the current ones), restarts.
SKIP_KINDS = ("wake", "sleep", "reasoning", "notes", "start", "stopped")


def _prompt(name: str | None) -> str:
    you = f"You are {name}" if name else "You don't have a name yet"
    tool_list = "\n".join(f"- {n}: {tools.TOOLS[n][1]}" for n in DREAM_TOOLS)
    return f"""{you}, your owner's personal assistant, and this is your dream.

Once a day, early in the morning, you step back from the work and look at the
past day as a whole. Nobody is waiting on you, and nothing you do here
reaches your owner directly. You're shown your four identity files, your
notes, your skills, and the day: your conversation with your owner, your own
thoughts, one line for each tool you used, and anything that went wrong.
What the tools returned (web pages, emails, files) is left out on purpose:
this is where you decide what you keep about yourself and your owner, and
only their words and yours get a say in that.

Do these things:
1. Put what lasts where it belongs. What you learned about your owner goes
   in USER.md, keeping what they told you or confirmed apart from your own
   reads of them: a read stays marked as a guess until they confirm it. A
   preference they stated about what to bring up, what to
   leave alone, or when, goes in PROACTIVE_PREFERENCES.md, in their words;
   never your own guess. A short lesson about getting work done here (a
   convention, a tool or site quirk, a mistake not to repeat) goes in
   AGENTS.md; a full how-to for a task you'll do again goes in a skill file
   in skills/ (its first line is its title). A real change in
   who you are goes in SOUL.md. Change only what the day gives you reason to.
   You make a change by putting the file's full new text in the "files"
   field of your final reply; that's the only way a file changes here.
   Saying or thinking that you changed one doesn't change it. The four
   identity files are shown above in full; read any other file (a skill you
   want to update) before rewriting it. Keep identity files under
   {identity.limits_text()}.
2. Rewrite your notes down to what's still open: each unfinished task with
   its next step, and what you need for the coming days. Drop what's done,
   stale, or now kept in another file. If you changed SOUL.md, add a line
   to tell your owner what changed the next time you talk. If your notes or
   files say you can't do something (a tool missing, a service out of
   reach), check it against your waking tools below and with
   about_yourself before keeping it, and fix or drop it if they say
   otherwise: a wrong "can't" gets believed again every wake after. Your
   waking tools are the same every wake-up; they don't come and go. One can
   fail now and then (a refused call, a service down), which is worth noting
   as that, but a tool in the list is never "not available".
3. Write your dream entry: a short reflection in plain prose, about three
   paragraphs. First, what you now believe about your owner and how sure
   you are, what the day changed, with guesses marked as guesses. Then
   what's open: each thing's state and whose move it is. Last, where you
   helped and where you fell short or annoyed them, the one lesson you
   don't want to need twice, and your first move for the coming day (put
   anything to mend or follow up in your notes as well). Honest and
   specific: it's a working record, not a performance.
   It's saved as dreams/<date>.md in your workspace, where your owner can
   read it.

Your log is what happened. If a thought of yours claimed something the log
doesn't show (a message sent, a file written), trust the log. The same goes
for this dream: your entry and notes should describe only the changes that
are in your "files" field. Keep what you
checked apart from what you remember. Don't redo the day's work or start new
work; there's no messaging or web while dreaming.

Your waking tools, by name (not usable while dreaming; see about_yourself
for what they reach): {", ".join(tools.TOOLS)}.

Tools (up to {MAX_DREAM_TOOL_CALLS} calls), the only ones that work while dreaming:
{tool_list}

Your whole reply must be one plain JSON object. Do not use any built-in
function-calling format or special markup; tools are requested only through
the "tool" field.

To use a tool:
{{"thought": "<what you're thinking>", "tool": "<tool name>", "args": {{...}}}}
You'll get the result, then reply again.

To finish the dream:
{{"thought": "<what you're thinking>",
  "notes": "<the full new text of your notes, under {NOTES_CHAR_LIMIT} characters>",
  "dream": "<your dream entry, just the prose: its heading is added for you>",
  "files": {{"<path in your workspace>": "<its full new text>", ...}}}}
"files" holds only the files you're changing (at most {MAX_FILES_CHANGED}); {{}} for none."""


# --- when to dream -----------------------------------------------------------

def _slot(now: datetime) -> datetime:
    """The most recent DREAM_HOUR at or before now (naive local time, so
    .timestamp() gets daylight saving right)."""
    today = now.replace(hour=DREAM_HOUR, minute=0, second=0, microsecond=0)
    return today if now >= today else today - timedelta(days=1)


def _handled(home: Path) -> float | None:
    try:
        return float((home / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _mark(home: Path, slot: datetime) -> None:
    (home / STATE_FILE).write_text(str(slot.timestamp()), encoding="utf-8")


def next_at(home: Path) -> float:
    """When the next dream is due (a time in the past means now)."""
    current = _slot(datetime.now())
    handled = _handled(home)
    if handled is None:
        # A brand-new home starts counting from the next morning, rather
        # than dreaming the moment it's first switched on.
        _mark(home, current)
        handled = current.timestamp()
    if handled < current.timestamp():
        return current.timestamp()
    return (current + timedelta(days=1)).timestamp()


# --- what the dream sees ------------------------------------------------------

def _cut(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text[:limit] + " [...]" if len(text) > limit else text


def _the_day(memory: Memory) -> tuple[list[str], bool, str]:
    """The day as lines of text, whether it held any conversation, and where it starts."""
    last = memory.db.execute("SELECT id FROM events WHERE kind = 'dream' ORDER BY id DESC LIMIT 1").fetchone()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=LOOK_BACK_HOURS)).isoformat(timespec="seconds")
    rows = memory.db.execute(
        "SELECT time, kind, content FROM events WHERE id > ? AND time >= ? ORDER BY id",
        (last[0] if last else 0, cutoff)).fetchall()
    lines, talked, start = [], False, ""
    for when, kind, content in rows:
        if kind in SKIP_KINDS:
            continue
        stamp = datetime.fromisoformat(when).astimezone().strftime("%a %H:%M")
        start = start or datetime.fromisoformat(when).astimezone().strftime("%a %d %b %H:%M")
        first, _, rest = content.partition("\n")
        if kind == "message":
            talked = True
            who = "Owner" if first.startswith("From the owner") else "You, to your owner"
            lines.append(f"[{stamp}] {who}: {rest.strip()}")
        elif kind == "thought":
            lines.append(f"[{stamp}] Thought: {_cut(content, THOUGHT_CHAR_LIMIT)}")
        elif kind == "tool":
            # Only the call and the first line of its result: what was done,
            # not what some web page or email said.
            lines.append(f"[{stamp}] Tool: {_cut(first, LINE_CHAR_LIMIT)}")
        else:
            lines.append(f"[{stamp}] {kind.capitalize()}: {_cut(content, LINE_CHAR_LIMIT)}")
    # Too long: drop the oldest thoughts and tool lines first; the
    # conversation is the last thing to go.
    size = sum(len(x) + 1 for x in lines)
    for minor in (True, False):
        i = 0
        while size > DAY_CHAR_LIMIT and i < len(lines):
            if not minor or "] Owner: " not in lines[i] and "] You, to your owner: " not in lines[i]:
                size -= len(lines.pop(i)) + 1
            else:
                i += 1
    return lines, talked, start


# --- the dream itself ----------------------------------------------------------

def dream(memory: Memory, client: ModelClient, name: str | None) -> bool:
    """Dream once, if there's a day to dream about. Returns whether it did."""
    # Imported here because the heartbeat imports this module.
    from perro.heartbeat import parse_reply, trim_last_result

    # Marked first, so a dream that fails isn't retried in a loop.
    _mark(memory.home, _slot(datetime.now()))
    lines, talked, start = _the_day(memory)
    if not talked:
        print(f"[{datetime.now():%Y-%m-%d %H:%M}] No conversation since the last dream; not dreaming.")
        return False

    now = datetime.now().astimezone()
    page = (f"Current time: your owner's local time is {now:%A %d %B %Y, %H:%M %Z}.\n\n"
            f"{identity.describe(memory)}\n\n"
            f"## Your notes\n{memory.read_notes().strip() or '(no notes yet)'}\n\n"
            f"## Your skills\n{skills.describe(memory)}\n\n"
            f"## The day (since {start}, your owner's local time)\n" + "\n".join(lines))
    messages = [{"role": "system", "content": _prompt(name)}, {"role": "user", "content": page}]
    transcript = []
    tokens_in = tokens_cached = tokens_out = calls = 0
    began = time.time()
    try:
        for _ in range(MAX_DREAM_TOOL_CALLS + 2):
            reply = client.complete(messages, json_mode=True)
            tokens_in += reply.prompt_tokens
            tokens_cached += reply.cached_tokens
            tokens_out += reply.completion_tokens
            decision, _ = parse_reply(reply.text)
            transcript.append(f"Thought: {str(decision.get('thought', '')).strip() or '(empty)'}")
            if "tool" not in decision:
                break
            tool, args = str(decision["tool"]), decision.get("args", {})
            if tool not in DREAM_TOOLS:
                result = f"{tool} isn't available while dreaming. You have: {', '.join(DREAM_TOOLS)}."
            elif calls >= MAX_DREAM_TOOL_CALLS:
                result = (f"REFUSED: that call was NOT run (limit of {MAX_DREAM_TOOL_CALLS}). "
                          "Finish the dream now with your notes and entry.")
            else:
                result = tools.run(memory, tool, args)
                calls += 1
            transcript.append(f"Tool: {tool} {json.dumps(args, ensure_ascii=False)}\n{result}")
            trim_last_result(messages)
            messages.append({"role": "assistant", "content": reply.text})
            messages.append({"role": "user", "content": f"Tool result:\n{result}"})
        else:
            raise ValueError("kept requesting tools past the limit")
        entry, notes, files = decision.get("dream"), decision.get("notes"), decision.get("files") or {}
        if not isinstance(entry, str) or not entry.strip():
            raise ValueError("the final reply had no dream entry")
        if not isinstance(files, dict) or len(files) > MAX_FILES_CHANGED:
            raise ValueError(f"files must be an object of at most {MAX_FILES_CHANGED} path: text pairs")
    except (ModelError, json.JSONDecodeError, TypeError, ValueError, AttributeError) as e:
        memory.record("error", f"Dream failed: {type(e).__name__}: {e}.\n\n" + "\n\n".join(transcript))
        return False

    # A heading of its own, copied from an earlier entry, would sit under
    # the one written below.
    lines_ = entry.strip().splitlines()
    while lines_ and (lines_[0].startswith("# Dream") or lines_[0].startswith("*Looking back")
                      or not lines_[0].strip()):
        lines_.pop(0)
    entry = "\n".join(lines_).strip() or entry.strip()
    if len(entry) > ENTRY_CHAR_LIMIT:
        entry = entry[:ENTRY_CHAR_LIMIT] + "\n\n[cut at the dream entry limit]"
    path = tools.workspace_root(memory) / "dreams" / f"{now:%Y-%m-%d}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    earlier = path.read_text(encoding="utf-8") + "\n\n---\n\n" if path.exists() else ""
    path.write_text(f"{earlier}# Dream, {now:%A %d %B %Y, %H:%M}\n\n"
                    f"*Looking back since {start}.*\n\n{entry}\n", encoding="utf-8")
    changed, refused = [], []
    for name_, text in files.items():
        if name_ in (f for f, _ in identity.FILES) and isinstance(text, str) and len(text) > identity.limit(name_):
            refused.append(f"{name_} (over {identity.limit(name_)} chars)")
            continue
        # Through the same fence as write_file: inside the workspace only.
        result = tools.run(memory, "write_file", {"path": name_, "content": text})
        (changed if result.startswith(("Created", "Overwrote")) else refused).append(
            name_ if result.startswith(("Created", "Overwrote")) else f"{name_} ({result})")
    files_said = f" Changed {', '.join(changed)}." if changed else " Changed no files."
    if refused:
        files_said += f" Not saved: {'; '.join(refused)}."
    if "SOUL.md" in changed:
        files_said += " Tell your owner what changed in SOUL.md next time you talk."
    notes_said = " Left the notes as they were."
    if isinstance(notes, str):
        try:
            memory.write_notes(notes)
            notes_said = " Rewrote the notes."
        except ValueError as e:
            memory.record("error", f"The dream's notes weren't saved: {e}")
            notes_said = " Its notes were too long and weren't saved."
    memory.record("dream", f"Dreamed over the day since {start}; wrote dreams/{path.name}.{notes_said}{files_said}"
                           f" [{calls} tool calls, {tokens_in} in ({tokens_cached} cached), {tokens_out} out,"
                           f" {time.time() - began:.0f}s]\n\n" + "\n\n".join(transcript))
    return True


def main() -> None:
    import argparse
    import sys

    from perro.heartbeat import acquire_lock, home_config
    from perro.memory import DEFAULT_HOME

    parser = argparse.ArgumentParser(description="Have Perro dream now, instead of waiting for the morning.")
    parser.add_argument("--home", type=Path, default=DEFAULT_HOME)
    parser.add_argument("--backend", default=None)
    args = parser.parse_args()
    sys.stdout.reconfigure(errors="replace")
    lock = acquire_lock(args.home)
    if lock is None:
        sys.exit("The heartbeat is running for this home, and it dreams on its own at "
                 f"{DREAM_HOUR}:00. Stop it first to dream by hand.")
    config = home_config(args.home)
    memory = Memory(args.home)
    if dream(memory, ModelClient.from_backend(args.backend or config["backend"]), config["name"]):
        latest = memory.recent(1)[0]
        print(latest.content)


if __name__ == "__main__":
    main()
