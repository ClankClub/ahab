# Anatomy

What Perro is, built up from the bottom: everything around the model, layer by layer. For what each piece does in detail and what's next, see [STATUS.md](STATUS.md).

**Layer zero: the starting point.** A language model is a stateless function. Text goes in, text comes out, and it remembers nothing between calls. It has no clock, no hands, no idea who you are, and it never does anything unless someone calls it. Everything else is scaffolding around that one fact.

**A way to call it.** `model.py` talks to any OpenAI-compatible endpoint, which is where DeepSeek lived. `cli_model.py` came later and runs Codex or Claude Code as nothing but a brain. The rest of the system never knows which one is behind the curtain, and that's on purpose.

**A way for it to act.** The model can't actually do anything; it can only write text. So the protocol is that it answers with plain JSON, either "call this tool with these args" or "I'm done, here's my message, wake me at X." Code reads that JSON and does the thing. Every step is readable in the log. The reply form came later and makes Codex enforce that shape, so answers stop leaking out as prose.

**A pulse.** The heartbeat (`heartbeat.py`) is what makes it an agent and not a chatbot. It wakes, builds a page, asks the model, runs the tools it asks for (up to 10 calls), saves, and sleeps until whenever the model chose. A message from the owner, an email, an alarm or an approval interrupts the sleep within seconds. A lock keeps two heartbeats from running at once.

**Memory, since the model has none.** `memory.db` holds an event log of everything that happened, plus a messages table. `notes.md` is its scratchpad for open tasks. `recall` searches the log. `days/` renders the log as one readable file per day. And every wake gets a page: identity files, notes, the recent conversation, recent events, the local time, the next two weeks. Each wake, the model reads its own life back from that page. That's the whole trick of persistence: it doesn't remember, it rereads.

**A body with walls.** The `workspace/` folder is the only part of the disk its file tools can touch, and every path goes through `_resolve()`.

**A self.** `SOUL.md`, `USER.md`, `PROACTIVE_PREFERENCES.md` and its own `AGENTS.md`, all on the page every wake. Then `ABOUT_YOURSELF.md` and its tool, so it describes itself from facts. The prompt holds the rules it can't edit; the soul files hold everything it can.

**A night shift.** The dream, at 7am, consolidates the day, prunes false beliefs and writes skills. That's how it gets better rather than just older.

**Ways to reach the owner and be reached.** The inbox, the Telegram bridge paired to the owner alone, alarms (repeating ones too), and the mail watch with its brakes for agent-to-agent emails.

**Senses.** `fetch_page` and Wikipedia, `web_search` as a separate search-only run, and the browser box for pages built by JavaScript.

**Hands, with no keys in them.** n8n holds every sign-in, so Perro never sees a token. Through it: Google (calendar, Gmail read and send to the list, Drive, Docs, Sheets, Tasks, Contacts), its own Notion, its own GitHub, its own Vercel. Each relay is a second fence that refuses what the Python shouldn't be asking for anyway.

**A workshop.** Codex in a sealed Docker box builds sites, they go to private GitHub repos, then to Vercel previews, and from there to production only through an approval.

**A conscience in code.** Approvals: anything outward-facing becomes a card built from the exact payload, and only the owner's tap runs it. Add the delete cap, the email allowlist, and the rule that orders come only from the owner.

**Taming the borrowed brain.** Separate CLI profiles so it never reads the owner's own setup, Codex's own tools stripped out, the model-catalog fix for the hidden `exec` box, trimming old tool results, the one-call `message` field, and the retry when a reply comes back malformed.

**Staying alive unattended.** Login tasks, the watchdog, the six-hourly backup of its home to a private repo, the Mac installer, and tests on every push.

The model is maybe 10% of what Perro is. The other 90% is deciding what a stateless function needs in order to behave like someone.
