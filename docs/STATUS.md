# Status

The short version: where Ahab stands and what's next. Every piece in detail, with its reasons and gotchas, is in [BUILD_LOG.md](BUILD_LOG.md); the overview is [ANATOMY.md](ANATOMY.md).

## Where it stands (2026-10-18)

Running on the owner's PC since 2026-10-18, as Pip (home in the private `silascroe/pip`), thinking with `codex-luna` (GPT-6 Luna at max effort, through Codex). Four login tasks keep it up: the heartbeat, the Telegram bridge, the watchdog (every 10 minutes) and the backup (every 6 hours). n8n runs in Docker on localhost and holds every outside sign-in.

What Pip can do: talk on Telegram and by email to a short list; search, read and browse the public web (a locked Chromium box, nothing sent); keep alarms, standing intents ("when X comes up, do Y", matched in code), watches on web pages (checked in code while it sleeps, no tokens until one trips), notes, skills, its identity files and a proper memory (MEMORY.md, a memory folder with people pages and a note per day, and full-text search over everything it has ever said, read or done, with the conversation readable word for word); dream once a day at 7:00; use its own Google account (Calendar, Gmail, Drive, Docs, Sheets, Tasks, Contacts), Notion workspace, GitHub and Vercel; have Codex build static sites in the workshop, previewed privately; and hand read-only jobs to background workers (on its own model, Haiku or DeepSeek, never pricier), which report back to it, not the owner; see photos, screenshots and scanned pages, read PDFs and Office files, hear voice notes (Whisper on this PC), save email attachments and Drive files, send the owner files and pictures, and keep an Obsidian vault (its own, or one the owner points it at). Anything outward goes through an Approve tap on Telegram. And a Linux desktop of its own in a locked box (offline unless the owner lists hosts), to see, click, type and run commands in, and on Linux a shell of its own on the machine itself, as its own user with nothing of Ahab's to reach. And copies of what the owner did with other agents (Hermes first), delivered daily, to search and read. 86 tools (one, window screenshots, built but switched off until the owner allows an app).

What it can't: watch video (it gets a still and the sound), email anyone off its list, share or delete outside its own spaces, sign in on the web, or run code anywhere but its desktop box and, on Linux, its own shell user.

Moving to a Linux laptop (2026-10-20): the code is now Ahab throughout (`ahab` package, `AHAB_*` variables, `ahab-*` Docker and n8n names), so the laptop is a fresh install from `main`, with Pip's home cloned from `silascroe/pip`. The PC keeps running its old Perro-named copy until then. `scripts/install_linux.py` is written and tested offline, not yet run on the laptop.

## Open, and worth watching

- Whether the proactive defaults bring up useful things or noise, whether AGENTS.md and the dream's skills fill with real lessons, and whether Pip reads its guide when asked about itself.
- Proper memory (new; the weekly memory report on Telegram, Sundays at 7pm, counts it): whether Pip writes to MEMORY.md and memory/ as it goes or leaves it all to the dream, whether the dream keeps MEMORY.md tidy rather than ever longer, and whether Pip reaches for recall instead of saying it doesn't remember. The page grew: five files at up to 54,000 characters together, plus the latest day note.
- FEEDBACK.md (new): whether Pip uses it, and whether it stays specific. Builders read it before planning.
- Standing intents (new): whether Pip reaches for them over notes when a promise is about a subject, not a time.
- Workers and watches (new): whether Pip hands big reads to workers or keeps cramming them into its own calls, whether its task briefs stand on their own, which model it picks, and whether "changes" watches turn out noisy on real pages. The worker reports' token counts are the first per-job cost numbers.
- Senses (new): whether Pip reaches for look_at and read_file on what arrives, what an owner's photo costs per wake on Codex, and how the PC copes with Whisper on longer voice notes. Saving a real email attachment or Drive file hasn't been seen live yet. `workspace/media/` goes into the backup repo with everything else, binaries included.
- The Obsidian vault (new): whether Pip uses it as a real notebook (links, daily notes) or ignores it next to its own memory folder. Obsidian is installed on the PC, with Pip's vault at `home/workspace/vault`.
- The desktop (new): whether Pip reaches for it, how well Luna clicks from screenshots (no number yet; calibration is phase 2), what a desktop wake costs with a picture on every call, and whether the home volume grows.
- The sanity canary (new): one live probe came back clean; watch its first days on the real home. A "model_tools" event in the log means Codex's own tools are back.
- Codex's audit (2026-10-19, in its scratch fork's `docs/audit/`): three fixes are in; 23 findings remain, the worst being EX-02 (desktop and workshop output buffered whole on the PC before any limit), then interrupted approvals left `running`, the mail watch missing a burst past 25, and worker cancel and usage accounting.
- Sources (new): whether Pip looks in the Hermes copy when the owner mentions it, and how fast the archive grows.
- A real email waking Pip through the mail watch hasn't been seen yet.
- Which sites the browser's no-sending rule breaks (Google Maps half-loads).
- Making a GitHub repository public through the approval hasn't been tried live.

## Next, roughly in order

1. **Agents (designed 2026-10-20, building next):** Pip at the head of a roster of crew agents (inside the install, no heartbeat, run per task) and remote A2A agents like Hermes; phase 1 is crew agents. See [DESIGN_AGENTS.md](DESIGN_AGENTS.md).
2. **A terse background voice (agreed 2026-10-20):** the caveman idea, for what Pip writes that nobody reads for pleasure: thoughts between tool calls, worker reports, the digest's pages and index. Never his messages to the owner unless the owner asks, and never what he reads (his rules, the owner's words), since compressing a prompt makes answers worse. Matters most on a per-token backend like Sonnet.
3. **Desktop phase 2:** desktop jobs (a worker that holds the desktop for a long errand), `--replay` and `--calibrate`; see [DESIGN_DESKTOP.md](DESIGN_DESKTOP.md).
4. **Goals:** the next genre in the agent comparison: something longer-lived than an alarm or intent that Pip works toward across wakes and reports on.
5. **Model fallback:** when the running backend fails (a lapsed login, an outage), a wake falls back to a second one instead of not happening.
6. **Skill upkeep:** the dream reviews skills it wrote for ones that went stale or never get used.
7. **Sources phase 3 (parked 2026-10-20, the owner's call):** uploaded exports like ChatGPT's, built against a real export, with the monthly nudge and re-read; see [DESIGN_SOURCES.md](DESIGN_SOURCES.md). Phases 1 and 2 are built: Hermes's record delivered daily, searchable by Pip, and distilled each morning by the digest into pages under `memory/sources/hermes/`. The digest's first live run read and wrote nothing (fixed in `ddc7030`); check the next morning runs actually write pages.
8. **More search providers:** `brave` and `searxng` in `ahab/search.py`, for homes on an API.
9. **Backend bake-off:** replay real owner messages through copies of the home on `codex-luna`, `claude-haiku` and DeepSeek, scoring JSON slips, tool choices, finishing the job, voice, time and tokens.
10. **Small gaps:** find-and-replace in Docs; Notion restore from trash and moving into a database (both want a newer Notion API version).
11. **Mail off the list,** if wanted, through the approval step.
12. **A reply form for API backends** (`response_format: json_schema`), if an OpenAI or OpenRouter backend is used.
13. **Later:** a Windows `install.ps1`; browser sign-in to Pip's own accounts through approvals; a model ladder and a spend cap.
