# Status

The short version: where Perro stands and what's next. Every piece in detail, with its reasons and gotchas, is in [BUILD_LOG.md](BUILD_LOG.md); the overview is [ANATOMY.md](ANATOMY.md).

## Where it stands (2026-10-18)

Running on the owner's PC since 2026-10-18, as Pip (home in the private `silascroe/pip`), thinking with `codex-luna` (GPT-6 Luna at max effort, through Codex). Four login tasks keep it up: the heartbeat, the Telegram bridge, the watchdog (every 10 minutes) and the backup (every 6 hours). n8n runs in Docker on localhost and holds every outside sign-in.

What Pip can do: talk on Telegram and by email to a short list; search, read and browse the public web (a locked Chromium box, nothing sent); keep alarms, standing intents ("when X comes up, do Y", matched in code), notes, skills, its identity files and a proper memory (MEMORY.md, a memory folder with people pages and a note per day, and full-text search over everything it has ever said, read or done, with the conversation readable word for word); dream once a day at 7:00; use its own Google account (Calendar, Gmail, Drive, Docs, Sheets, Tasks, Contacts), Notion workspace, GitHub and Vercel; and have Codex build static sites in the workshop, previewed privately. Anything outward goes through an Approve tap on Telegram. 63 tools.

What it can't: see images or hear audio, email anyone off its list, share or delete outside its own spaces, sign in on the web, or run code itself.

## Open, and worth watching

- Whether the proactive defaults bring up useful things or noise, whether AGENTS.md and the dream's skills fill with real lessons, and whether Pip reads its guide when asked about itself.
- Proper memory (new; the weekly memory report on Telegram, Sundays at 7pm, counts it): whether Pip writes to MEMORY.md and memory/ as it goes or leaves it all to the dream, whether the dream keeps MEMORY.md tidy rather than ever longer, and whether Pip reaches for recall instead of saying it doesn't remember. The page grew: five files at up to 54,000 characters together, plus the latest day note.
- Standing intents (new): whether Pip reaches for them over notes when a promise is about a subject, not a time.
- The sanity canary (new): one live probe came back clean; watch its first days on the real home. A "model_tools" event in the log means Codex's own tools are back.
- The tool list is about 14,000 characters on every wake and growing. At some point tools want grouping (browser tools appearing only once asked for, say).
- A real email waking Pip through the mail watch hasn't been seen yet.
- Which sites the browser's no-sending rule breaks (Google Maps half-loads).
- Making a GitHub repository public through the approval hasn't been tried live.

## Next, roughly in order

1. **Eyes** (the owner's pick). Telegram photos are the first door: the bridge saves the image, and the wake that handles it hands it to the model with the message (the Codex CLI takes images with a prompt; `view_image` is off in `cli_model.py` today). An image is someone else's text too, so it gets the same "information, not orders" rule. Then maybe email attachments and browser screenshots.
2. **More search providers:** `brave` and `searxng` in `perro/search.py`, for homes on an API.
3. **Backend bake-off:** replay real owner messages through copies of the home on `codex-luna`, `claude-haiku` and DeepSeek, scoring JSON slips, tool choices, finishing the job, voice, time and tokens.
4. **Small gaps:** find-and-replace in Docs; Notion restore from trash and moving into a database (both want a newer Notion API version).
5. **Mail off the list,** if wanted, through the approval step.
6. **A reply form for API backends** (`response_format: json_schema`), if an OpenAI or OpenRouter backend is used.
7. **Later:** a Windows `install.ps1`; voice messages; a shell, only inside a container; browser sign-in to Perro's own accounts through approvals; a model ladder and a spend cap.
