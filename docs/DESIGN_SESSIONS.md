# Design: sessions

Agreed with the owner on 2026-10-21. Phase 1 is built (see BUILD_LOG); phases 2 and 3 are still to come. When it's all built, the story goes into `BUILD_LOG.md`, Pip's side into `ABOUT_YOURSELF.md`, and this file is deleted. The research behind it is OpenClaw's `docs/concepts/` (session, compaction, session-pruning, main-session) and Hermes's memory notes in the owner's reference folder, outside the repo.

## Why

Every wake-up starts from a fresh page. Pip sees the last 30 messages with his owner, the one-line headers of the last 20 events, and his notes. Everything else he did (what a tool returned, why he chose what he chose, what he was halfway through) is gone from his view unless he wrote it down or goes looking with `recall`. Talking to him is a run of fresh starts with a short memory of the chat. That's the biggest thing between Ahab and "good company to talk to".

It also wastes money in a way that's easy to fix. Model providers cache the opening of a prompt they've seen in the last few minutes and charge far less for it. Today the page opens with the current time, right after the system prompt, so every wake's page differs from the last one from that line on. The "Recent conversation" window scrolls with every new message, so even without the time line it would differ near the top. Codex caches about 55% of what Pip sends overall, nearly all of it from later calls inside one wake re-sending the same page. Between wakes, almost nothing.

Measured on the laptop on 2026-10-21, the page was about 42,600 characters (roughly 10,700 tokens): system prompt 10,500, recent conversation 10,300, tool list 8,700, the five identity files 8,600, notes and events 3,900, the rest small.

## What the other agents do

**OpenClaw** keeps one main session for all direct chat. Heartbeats run in it too, and background jobs report back into it. Sessions roll forever by default (`session.reset.mode: "none"`); daily and idle resets are opt-in, and `/new` starts over by hand. When the transcript nears the model's context window, compaction summarizes the older turns into one summary entry and keeps the most recent part (20,000 tokens by default) word for word. Tool calls are never split from their results. Before compacting it gives the agent one quiet turn to save anything worth keeping to memory files. The full transcript stays on disk; the summary only replaces it on the page. Separately, session pruning cuts old tool results down without touching the conversation.

**Hermes** freezes the memory snapshot at session start so the system prompt stays byte-identical and cached. Writes during the session go to disk but don't change the page until the next session.

## The design

**One running session.** Pip's page becomes a conversation that grows between wakes instead of being rebuilt from scratch. Each wake adds its turns: an opening (when he woke and what came in since last time, including the owner's messages in full), then his replies and tool results, as the model actually saw them, with long results cut short the way they already are inside a wake. Rolling, no automatic reset, as in OpenClaw. The owner can start a fresh one by hand.

**The page, stable parts first.**

1. System prompt and tool list. Unchanged except for the wording about the page.
2. The five identity files.
3. The session summary, if the session has been compacted.
4. The transcript, oldest first.
5. This wake's opening, then **Right now**: the coming days, the notes, alarms, intents, watches, goals, what's pending, the memory folder and skills.

Everything through 4 is the same as on the last wake unless Pip edited an identity file or a compaction ran, so a burst of chat (wakes minutes apart) reads most of the page from cache. The "Right now" block is rebuilt every wake and is not stored in the transcript, so the notes aren't repeated once per wake. The memory folder moved to the end because Pip writes memory files often and the folder list would otherwise break the cache for everything after it. Identity files stay live (not frozen as in Hermes): Pip's page is cold after a long sleep anyway, and a stale USER.md is worse than one missed cache.

**What's stored.** A `session` table in `memory.db`: one row per turn, with the wake it belongs to, its role and its text, and a `folded` flag. Pip's replies are stored with a rewritten notes field reduced to its length and long tool arguments (a `write_file`'s content) cut, since both are in the log in full. Tool results are stored cut, the same way a result is cut inside a wake once a newer one arrives. Images aren't stored; the text names the file. Nothing is ever deleted: folded turns stay in the table, and the event log keeps everything as before.

**"Recent conversation" and "Recent events" go.** The transcript holds both, in order, in full. The opening's "since your last wake" block shows the events that happened between wakes (alarms, deliveries, reports, the dream, restarts) with the same header-only rule as before.

**Compaction.** When the session passes its size, the oldest wake-ups are folded into a summary until about 40% of the size is left word for word. A wake is never split, so a tool call and its result stay together. The summary is one plain model call: the previous summary plus the folded turns in, a new summary out, in the second person, capped at 12,000 characters. It runs at the end of a wake, after the reply has gone out, so the owner never waits on it. If it fails it's retried after an hour; if the session reaches one and a half times its size anyway, the oldest turns are folded with a short code-written note in place of a summary, so a broken summarizer can't grow the page without limit.

There's no memory-flush turn before compaction, unlike OpenClaw. The dream already reads the whole day from the event log every morning and saves what lasts, and with a 100,000-token session a turn is usually days old before it's folded, so it has already been dreamed over.

**Size.** `session_tokens` in `home.json`, default 100,000 (estimated at four characters a token). A backend can set its own default in `model.BACKENDS` for a small model (the `local` one gets 16,000). The owner chose 100,000 over a smaller default, knowing the trade: inside a burst of chat most of it is cached, but the first wake after a long sleep reads it all at full price. A bare wake that costs about 12,000 tokens today can cost up to about 110,000 with a full session.

**Background work stays out.** Workers, crew agents, the dream, the digest and searches keep their own short contexts, as OpenClaw isolates its cron runs. Their reports reach the session through the opening's "since your last wake" block, as they reach the page today.

**Restarts stop waking Pip.** Restarting the heartbeat (usually to load new code) woke Pip at once, which cost a full wake each time: 22 of them on 2026-10-20. Now a start keeps the wake-up Pip chose, if it's still ahead. Anything waiting (a knock, an approval, a finished job) still wakes him within seconds.

## Caching, per backend

Codex (OpenAI) caches any repeated prompt opening, so the flattened CLI prompt benefits fully. DeepSeek's API caches the same way. Claude Code's CLI takes the whole prompt as one block, and Anthropic's cache works at block boundaries, so Claude probably caches only the system prompt; that's unverified and worth measuring when Pip runs on Claude.

## Phases

1. **Groundwork (now).** The session table, the page reorder, the transcript, compaction with its fallbacks, the size setting, restarts that keep the schedule, a `py -m ahab.session` command to see the session, compact it or start a new one, and the session's size on the status screen.
2. **Owner controls on Telegram.** `/new` and `/compact` from the paired chat, done by the heartbeat on its next wake.
3. **Measure and tune.** A week of real use: how often it compacts, what the summaries keep and lose, the token bill against the old page, and whether Claude caches more than the system prompt.
