# Status

The short version: where Perro stands and what's next. Every piece in detail, with its reasons and gotchas, is in [BUILD_LOG.md](BUILD_LOG.md); the overview is [ANATOMY.md](ANATOMY.md).

## Where it stands (2026-10-18)

Running on the owner's PC since 2026-10-18, as Pip (home in the private `silascroe/pip`), thinking with `codex-luna` (GPT-6 Luna at max effort, through Codex). Four login tasks keep it up: the heartbeat, the Telegram bridge, the watchdog (every 10 minutes) and the backup (every 6 hours). n8n runs in Docker on localhost and holds every outside sign-in.

What Pip can do: talk on Telegram and by email to a short list; search, read and browse the public web (a locked Chromium box, nothing sent); keep alarms, standing intents ("when X comes up, do Y", matched in code), watches on web pages (checked in code while it sleeps, no tokens until one trips), notes, skills, its identity files and a proper memory (MEMORY.md, a memory folder with people pages and a note per day, and full-text search over everything it has ever said, read or done, with the conversation readable word for word); dream once a day at 7:00; use its own Google account (Calendar, Gmail, Drive, Docs, Sheets, Tasks, Contacts), Notion workspace, GitHub and Vercel; have Codex build static sites in the workshop, previewed privately; and hand read-only jobs to background workers (on its own model, Haiku or DeepSeek, never pricier), which report back to it, not the owner; see photos, screenshots and scanned pages, read PDFs and Office files, hear voice notes (Whisper on this PC), save email attachments and Drive files, and send the owner files and pictures. Anything outward goes through an Approve tap on Telegram. 73 tools.

What it can't: watch video (it gets a still and the sound), email anyone off its list, share or delete outside its own spaces, sign in on the web, or run code itself.

## Open, and worth watching

- Whether the proactive defaults bring up useful things or noise, whether AGENTS.md and the dream's skills fill with real lessons, and whether Pip reads its guide when asked about itself.
- Proper memory (new; the weekly memory report on Telegram, Sundays at 7pm, counts it): whether Pip writes to MEMORY.md and memory/ as it goes or leaves it all to the dream, whether the dream keeps MEMORY.md tidy rather than ever longer, and whether Pip reaches for recall instead of saying it doesn't remember. The page grew: five files at up to 54,000 characters together, plus the latest day note.
- FEEDBACK.md (new): whether Pip uses it, and whether it stays specific. Builders read it before planning.
- Standing intents (new): whether Pip reaches for them over notes when a promise is about a subject, not a time.
- Workers and watches (new): whether Pip hands big reads to workers or keeps cramming them into ten calls, whether its task briefs stand on their own, which model it picks, and whether "changes" watches turn out noisy on real pages. The worker reports' token counts are the first per-job cost numbers.
- Senses (new): whether Pip reaches for look_at and read_file on what arrives, what an owner's photo costs per wake on Codex, and how the PC copes with Whisper on longer voice notes. Saving a real email attachment or Drive file hasn't been seen live yet. `workspace/media/` goes into the backup repo with everything else, binaries included.
- The sanity canary (new): one live probe came back clean; watch its first days on the real home. A "model_tools" event in the log means Codex's own tools are back.
- The tool list is about 20,000 characters on every wake and growing (a third of the page). At some point tools want grouping (browser tools appearing only once asked for, say).
- A real email waking Pip through the mail watch hasn't been seen yet.
- Which sites the browser's no-sending rule breaks (Google Maps half-loads).
- Making a GitHub repository public through the approval hasn't been tried live.

## Next, roughly in order

1. **Goals:** the next genre in the agent comparison: something longer-lived than an alarm or intent that Pip works toward across wakes and reports on.
2. **Model fallback:** when the running backend fails (a lapsed login, an outage), a wake falls back to a second one instead of not happening.
3. **Tool grouping:** the tool list is about 20,000 characters on every wake; show some groups (browser, Notion, GitHub) only once asked for.
4. **Skill upkeep:** the dream reviews skills it wrote for ones that went stale or never get used.
5. **More search providers:** `brave` and `searxng` in `perro/search.py`, for homes on an API.
6. **Backend bake-off:** replay real owner messages through copies of the home on `codex-luna`, `claude-haiku` and DeepSeek, scoring JSON slips, tool choices, finishing the job, voice, time and tokens.
7. **Small gaps:** find-and-replace in Docs; Notion restore from trash and moving into a database (both want a newer Notion API version).
8. **Mail off the list,** if wanted, through the approval step.
9. **A reply form for API backends** (`response_format: json_schema`), if an OpenAI or OpenRouter backend is used.
10. **Later:** a Windows `install.ps1`; a shell, only inside a container; browser sign-in to Perro's own accounts through approvals; a model ladder and a spend cap.
