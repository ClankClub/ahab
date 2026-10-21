# Anatomy

What Ahab is, built up from the bottom: everything around the model, layer by layer. For where things stand, see [STATUS.md](STATUS.md); for each piece in detail, [BUILD_LOG.md](BUILD_LOG.md).

**Layer zero: the starting point.** A language model is a stateless function. Text goes in, text comes out, and it remembers nothing between calls. It has no clock, no hands, no idea who you are, and it never does anything unless someone calls it. Everything else is scaffolding around that one fact.

**A way to call it.** `model.py` talks to any OpenAI-compatible endpoint, which is where DeepSeek lived. `cli_model.py` came later and runs Codex or Claude Code as nothing but a brain. The rest of the system never knows which one is behind the curtain, and that's on purpose.

**A way for it to act.** The model can't actually do anything; it can only write text. So the protocol is that it answers with plain JSON, either "call this tool with these args" or "I'm done, here's my message, wake me at X." Code reads that JSON and does the thing. Every step is readable in the log. The reply form came later and makes Codex enforce that shape, so answers stop leaking out as prose.

**A pulse.** The heartbeat (`heartbeat.py`) is what makes it an agent and not a chatbot. It wakes, builds a page, asks the model, runs the tools it asks for (up to 20 calls), saves, and sleeps until whenever the model chose. A message from the owner, an email, an alarm, an approval or a finished worker interrupts the sleep within seconds. Watches are checked in that same sleep loop, by code, so a page can be watched for days without a single token until it trips. A lock keeps two heartbeats from running at once.

**A running conversation.** The page isn't rebuilt from scratch each wake. `session.py` keeps every wake-up's turns (what came in, the replies, the tool results cut short) and puts them back on the next page, oldest first, after the system prompt and the identity files; "Right now" (notes, alarms, goals) goes last and isn't kept. So the page's opening is the same from wake to wake, which is what a prompt cache needs. When it grows past its size, the oldest wake-ups are folded into a summary by the model, as OpenClaw compacts.

**Memory, since the model has none.** `memory.db` holds an event log of everything that happened, plus a messages table and the session. `notes.md` is its scratchpad for open tasks. `MEMORY.md` is what lasts, and `memory/` holds the detail: a page per person, its opinions, a note per day written by the dream. `recall` searches all of it, log included, with a full-text index, and `read_conversation` gives back any day word for word. `days/` renders the log as one readable file per day, for the owner. And every wake gets a page: identity files and MEMORY.md, the memory folder, notes, the recent conversation, recent events, the local time, the next two weeks. Each wake, the model reads its own life back from that page. That's the whole trick of persistence: it doesn't remember, it rereads.

**A body with walls.** The `workspace/` folder is the only part of the disk its file tools can touch, and every path goes through `_resolve()`.

**A self.** `SOUL.md`, `USER.md`, `PROACTIVE_PREFERENCES.md`, its own `AGENTS.md` and `MEMORY.md`, all on the page every wake. Then `ABOUT_YOURSELF.md` and its tool, so it describes itself from facts. The prompt holds the rules it can't edit; the soul files hold everything it can.

**A night shift.** The dream, at 7am, consolidates the day into its memory files and a day note, prunes false beliefs and writes skills. That's how it gets better rather than just older.

**Ways to reach the owner and be reached.** The inbox, the Telegram bridge paired to the owner alone, alarms (repeating ones too), standing intents ("when this comes up"), and the mail watch with its brakes for agent-to-agent emails.

**Senses.** For the web: `fetch_page` and Wikipedia, `web_search` as a separate search-only run, and the browser box for pages built by JavaScript. For everything else (`senses.py`): whatever arrives is saved in `workspace/media/`; images ride along on the model call (Codex's `--image`, Claude Code's image blocks, an API's image parts), PDFs and Office files are read as text, and voice notes are transcribed by Whisper before Ahab wakes. And out: files and pictures attached to its messages. And hands: the desktop box (`desktop.py`), a Linux desktop in a container where it looks, clicks, types and runs commands, offline unless the owner lists hosts.

**Hands, with no keys in them.** n8n holds every sign-in, so Ahab never sees a token. Through it: Google (calendar, Gmail read and send to the list, Drive, Docs, Sheets, Tasks, Contacts), its own Notion, its own GitHub, its own Vercel. Each relay is a second fence that refuses what the Python shouldn't be asking for anyway.

**A workshop.** Codex in a sealed Docker box builds sites, they go to private GitHub repos, then to Vercel previews, and from there to production only through an approval.

**Helpers.** Workers (`workers.py`) are separate background processes, each its own small loop with the same JSON protocol, a read-only tool list and a fixed menu of cheap models. They report back to Ahab, never the owner, so one big job doesn't have to fit in a wake's twenty calls. Crew agents (`agents.py`) are workers that last: each listed by the owner in `home.json`, with a folder of its own under `home/agents/<name>/` (a lane, its own log and a workspace with its notes), no heartbeat, and a run per task. Both run the same loop (`runner.py`).

**A conscience in code.** Approvals: anything outward-facing becomes a card built from the exact payload, and only the owner's tap runs it. Add the delete cap, the email allowlist, and the rule that orders come only from the owner.

**Taming the borrowed brain.** Separate CLI profiles so it never reads the owner's own setup, Codex's own tools stripped out, the model-catalog fix for the hidden `exec` box, trimming old tool results, the one-call `message` field, and the retry when a reply comes back malformed.

**Staying alive unattended.** Login tasks, the watchdog (checking that Ahab is alive, and once a day that its model is sane), the six-hourly backup of its home to a private repo, the Mac installer, and tests on every push.

The model is maybe 10% of what Ahab is. The other 90% is deciding what a stateless function needs in order to behave like someone.
