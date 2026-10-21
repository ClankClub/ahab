<p align="center">
  <img src="branding/proposals/logbook/banner.jpg" alt="Ahab — Logbook banner" width="100%">
</p>

<h1 align="center"><img src="branding/proposals/logbook/mark.svg" alt="" width="48" valign="middle"> Ahab</h1>

Ahab is a self-built, persistent personal assistant. It wakes on a schedule it picks, takes on work you hand it, keeps track of what's going on, brings things up on its own, and is someone you can just talk to. Its sense of who it is grows in its notes over time.

Originally named **Perro**, this software grew out of the Lobo (`../Lobo`) runtime. It is now called **Ahab**; **Pip** is the continuing personal agent running on it. The runtime is a fork of Lobo's, adapted for a persistent personal assistant. See [AGENTS.md](AGENTS.md) for the rules, [docs/ANATOMY.md](docs/ANATOMY.md) for the overview, [docs/STATUS.md](docs/STATUS.md) for what exists and what's next, and [docs/BUILD_LOG.md](docs/BUILD_LOG.md) for the details.

## The shape of it

```
Your PC: Ahab runtime (Python)
  ├─ model client   → DeepSeek API, OpenRouter, or a local llama.cpp/Ollama server (all OpenAI-compatible),
  │                   or Codex / Claude Code run as the model only, through your subscription
  ├─ identity       → workspace/SOUL.md (who it is) + USER.md (what it knows about you) + PROACTIVE_PREFERENCES.md (what you want brought up unasked)
  │                   + AGENTS.md (its own lessons on how to work here); starters in templates/
  ├─ memory         → workspace/MEMORY.md (what lasts) + workspace/memory/ (people, opinions, a note per day)
  │                   + home/notes.md (open tasks) + home/memory.db (every event and message, full-text searchable)
  ├─ heartbeat      → wake, show memory, model decides, save, sleep until it chose
  ├─ inbox          → messages between you and Pip; where its instructions come from
  ├─ telegram       → bridge from a Telegram bot to the inbox, paired to your account only; photos, voice notes and files both ways
  ├─ senses         → sight (images go to the model with a call), PDFs and Office files read as text, voice notes heard (Whisper);
  │                   everything that arrives is kept in workspace/media/
  ├─ tools          → about_yourself (its guide, docs/ABOUT_YOURSELF.md), recall and read_conversation (its whole past, word for word), list/read/write/edit files, set/cancel alarms and standing intents, message you, read web pages, Google, Notion, start/check builds, GitHub, Vercel
  ├─ web            → read-only: any public page as text, plus Wikipedia search
  ├─ search         → web search, run by a separate Codex or Claude Code call (providers in search.py)
  ├─ n8n, google    → its own Google account through your local n8n: Gmail (read; send to a list you keep),
  │                   its calendar, Drive, Docs, Sheets, Tasks, Contacts (read and write; deletes only
  │                   its own tasks and contacts, 25 deletes a day; no sharing or calendar guests)
  ├─ obsidian       → an Obsidian vault (its own in workspace/vault/, or yours, set in home.json): links, backlinks, tags, daily notes
  ├─ notion         → its own Notion workspace through the same n8n (its to organize; deleting goes to Notion's trash)
  ├─ alarms         → reminders and follow-ups; the heartbeat wakes it for them
  ├─ watches        → web pages checked in code while it sleeps ("tell me when this changes / mentions X"); no tokens until one trips
  ├─ workers        → background helpers it starts for read-only jobs (cheap models only: its own, Haiku, DeepSeek); they report to it, not you
  ├─ agents         → a crew you list in home.json: named agents with a lane, notes and tools of their own, run per task; they report to it, not you
  ├─ workshop       → Codex builds static sites in a locked Docker box (workshop/); nothing is published
  ├─ browser        → a real Chromium in its own locked box (browser/), public sites only, never sends anything
  ├─ github         → keeps those sites in private repos on its own GitHub, through n8n; public only by approval
  ├─ vercel         → puts them online on its own Vercel: previews behind Vercel's sign-in, public only by approval
  ├─ supabase       → rows in its own Supabase project, directly over its REST API
  ├─ approvals      → outward actions wait for your Approve/Deny tap on Telegram, then run in code
  ├─ skills         → know-how files it keeps in workspace/skills/
  ├─ dream          → once a day at 7:00, a quiet wake that tidies notes and memory, writes a day note and workspace/dreams/
  ├─ backup         → commits and pushes home/ to its private repo every 6 hours, only between wakes
  ├─ days           → home/days/: the log as one readable Markdown file per day
  └─ home/workspace/ → the only part of the disk its tools may touch
```

## Setup

Python 3.11+, standard library only, with optional extras for the senses (below). Run everything from this folder. On this PC `python` isn't on the PATH, so use the `py` launcher.

### On a Mac

`install.sh` sets Ahab up without Git or anything else: it finds Python 3.11 or newer (or, after asking, installs it with Homebrew, or with the signed python.org installer if there's no Homebrew), downloads this repo from GitHub as a zip into `~/Ahab` (`--dir` to change), checks that it starts, and prints what to do next. Read it before you run it:

```bash
curl -fsSLO https://raw.githubusercontent.com/ClankClub/ahab/main/install.sh
```

```bash
bash install.sh
```

Running it again updates the code and leaves `home/` alone. On a Mac use `python3` where this README says `py`, and `~/.ahab` where it says `%LOCALAPPDATA%\Ahab`. Tested on Linux with faked Mac tools, not yet on a real Mac. A Windows version is still to come.

### On Linux (a machine of its own)

For a computer that's Ahab's to live on (Debian or similar, with systemd), `scripts/install_linux.py` sets it up as services under three users: you, an `ahab` user that runs the code and holds the keys (the n8n key and Telegram token in `/etc/ahab/env`, the Claude login, `memory.db`, n8n through Docker), and the agent's own shell user (`agent` by default, `--agent-user` to change), which gets the network and its own home but none of `ahab`'s files, no Docker and no sudo. The only bridge between them is one sudo rule letting `ahab` run commands as that user, which the `shell` and `shell_files` tools use (`ahab/hostshell.py`); `home.json`'s `"host_shell": {"user": "agent"}` turns them on, and the installer writes it. It installs git, curl and Docker from Debian's packages (asking first), clones this repo into `/home/ahab/ahab`, and writes the heartbeat and Telegram services and the watchdog and backup timers, each switched on once what it needs is there. Run it as root, once (the only time it needs your password), and read `--dry-run` first:

```bash
sudo python3 scripts/install_linux.py --owner YOUR-LINUX-USER --dry-run
```

Afterwards you manage Ahab as its user without a password (`sudo -u ahab -i`): sign in the Claude CLI there, start n8n, pair Telegram, then run the installer again to switch the waiting services on; it prints those steps. `--remove` takes the services and sudo rule away and leaves the users and files. The n8n key in `/etc/ahab/env` starts commented out; uncomment it when you set up n8n, since its being set is how Ahab and its watchdog know the install uses n8n. Rerunning the installer restarts only the services whose files changed, since a heartbeat restart wakes the agent.

Set your DeepSeek key as an environment variable (never in a file here), then open a new terminal, since `setx` only affects new ones:

```powershell
setx DEEPSEEK_API_KEY "sk-..."
```

Check the model client works:

```powershell
py -m ahab.model "Say hi in five words."
```

### The senses (optional installs)

Images need nothing extra: Codex and Claude Code take them, and an API backend does if its entry in `BACKENDS` (`ahab/model.py`) says `"vision": True`. Reading PDFs needs pypdf, and hearing voice notes on this PC needs faster-whisper (about 500 MB for the speech model, downloaded once):

```powershell
py -m pip install -r requirements.txt
py -m ahab.senses --warm
```

Or skip faster-whisper and set `GROQ_API_KEY` (or `OPENAI_API_KEY`) to transcribe through that API; `AHAB_HEARING` picks one when several are set up. `py -m ahab.senses` shows what's set up, and `py -m ahab.senses <file>` reads or transcribes one file as Ahab would.

### Obsidian (optional)

Pip keeps its own vault in `home/workspace/vault/`; open that folder as a vault in Obsidian to read along. To have it work in a vault of yours instead, put `"obsidian_vault": "C:\path\to\vault"` in `home/home.json`. Nothing else is needed: a vault is plain Markdown files. Pip never touches `.obsidian/` (the app's settings and plugins), and what it deletes goes to the vault's `.trash/`.

### Sources (what you did with other agents)

Ahab can keep a copy of what you do with another agent, so it knows about it later without asking. List each source in `home.json` (`"sources": {"hermes": {"kind": "hermes", "about": "Hermes on the desktop"}}`); nothing is accepted from a source that isn't listed. For Hermes in Docker, `scripts/send_hermes.py` (standard library only, run where Hermes runs) packs its conversations (exported with credentials scrubbed), memory, the skills it made and its workspaces' text files, and sends them over SSH. On the Ahab machine, give the sender a key of its own in the service user's `~/.ssh/authorized_keys` that can only deliver:

```
command="cd /path/to/ahab && python3 -X utf8 -m ahab.sources receive hermes",no-pty,no-port-forwarding,no-agent-forwarding,no-X11-forwarding ssh-ed25519 AAAA...
```

Then schedule `python send_hermes.py --ssh ahab@<ahab machine> --key <that key> --log <file>` daily (on Windows, as a Task Scheduler task running `pythonw.exe`, so no window opens; `--log` is where each run's outcome goes) (or `--local <ahab folder>` when both run on one machine). The copy lands in `home/sources/hermes/`, text only, and Ahab searches and reads it with its `sources` tool. See [docs/DESIGN_SOURCES.md](docs/DESIGN_SOURCES.md).

### Window screenshots (off unless you allow an app)

`screenshot_window` lets Pip look at one app's window on your screen, for the apps you list by program name in `home/home.json`, e.g. `"screen_apps": ["Obsidian"]`. With no list it refuses everything. It never takes the whole screen, and it can't click or type. Windows only.

### The desktop (computer use, in Docker)

`py -m ahab.desktop --setup` builds and starts Pip's desktop: Linux with XFCE, Firefox, LibreOffice, Obsidian and a terminal, on a 1024x768 virtual screen, in a locked container with no network. `--check` runs the fence test. `--watch` shows the screen live at http://127.0.0.1:8765/. To let it reach some sites, list them in `home/home.json` as `"desktop_hosts": ["example.com"]`; anything in the box can then reach (and post to) those, and nothing else. It stops itself after 15 idle minutes; its files live in the Docker volume `ahab-desktop-home`.

### Codex or Claude Code as the model (instead of an API key)

Ahab can think through your Codex or Claude Code subscription: backends `codex-luna` (GPT-6 Luna, max effort), `claude-sonnet` (Claude Sonnet, medium effort) and `claude-haiku` (Claude Haiku). Each CLI runs as the model only, with its own tools off, and uses a separate profile folder under `%LOCALAPPDATA%\Ahab` (`~/.ahab` on a Mac; `AHAB_CLI_HOME` to move it), so it never sees your own CLAUDE.md, AGENTS.md, memories or plugins. Log in once per profile, in a terminal you close afterwards:

```powershell
New-Item -ItemType Directory -Force "$env:LOCALAPPDATA\Ahab\codex", "$env:LOCALAPPDATA\Ahab\claude" | Out-Null
$env:CODEX_HOME = "$env:LOCALAPPDATA\Ahab\codex"; codex login
$env:CLAUDE_CONFIG_DIR = "$env:LOCALAPPDATA\Ahab\claude"; claude auth login
```

If `codex` isn't on your PATH, the desktop app's copy is under `~\.codex\packages\standalone\releases\<version>\bin\codex.exe` (Ahab finds it there on its own, or set `AHAB_CODEX_BIN`). Then put `{"backend": "codex-luna"}` in `home/home.json` and restart the heartbeat. To give it a spare for when that model fails (a lapsed login, an outage, a rate limit), add `"fallback": "claude-sonnet"` (any backend name): a failed call goes to the spare, the usual model is tried again ten minutes later, and Pip sees a note on his page while the spare is answering. When a CLI login has expired the error says which `login` command to run.

### Google and Notion (through n8n)

Ahab never signs in to Google or Notion. Your own n8n, in Docker, holds the sign-ins, and Ahab calls its webhooks on localhost. Everything to set it up is in [n8n/](n8n/README.md): the Docker setup, the workflows, and which sign-ins to create. Every call carries a key that n8n checks, so other programs and web pages on this PC can't use them. The key is the environment variable `AHAB_N8N_KEY`, and it must match n8n's credential "Ahab relay key". Restart the heartbeat after changing it. See [docs/BUILD_LOG.md](docs/BUILD_LOG.md) for what each workflow does.

### The workshop (Codex builds sites, in Docker)

Ahab can have small static websites built: it writes a brief, and Codex builds the site in a locked container on this PC (see `ahab/workshop.py` for exactly how it's locked down). Nothing is published. It needs Docker Desktop running and a Codex login of the builder's own, separate from Ahab's model profile:

```powershell
py -m ahab.workshop --setup     # builds the image, starts the allowlist proxy
py -m ahab.workshop --login     # once; add --device-auth to sign in with a code on your phone
```

Everything lives in `~\.ahab\workshop` (`AHAB_WORKSHOP_HOME` to move it): `sites\<name>` is each site's folder, `runs\<id>` each build's logs and Codex's report, `codex-builder` the login. Open `sites\<name>\dist\index.html` to look at a finished site. It's deliberately not in AppData: a packaged Windows app (the Claude desktop app is one) gets AppData writes redirected into a private copy that Docker can't see.

### Telegram (chat from your phone)

Ahab itself never talks to Telegram. A small bridge (`ahab/telegram.py`) runs next to the heartbeat, passes your messages into the inbox, which wakes Ahab within seconds, and sends Ahab's replies back to your phone. It also brings approval requests as cards with Approve and Deny buttons; only a tap counts as a yes.

1. In Telegram, message **@BotFather**, send `/newbot`, and pick a name and a username ending in `bot`. It gives you a token.
2. Save the token as an environment variable, then open a new terminal: `setx AHAB_TELEGRAM_TOKEN "123456:ABC..."`
3. Pair the bot with your account: `py -m ahab.telegram --pair`. It prints a six-digit code; send that code to your bot. From then on the bridge only accepts messages from your account in your private chat with the bot. Anyone else who finds the bot is ignored, and their text never reaches Ahab.
4. Run `.\scripts\install-task.ps1` so the bridge and the heartbeat both start hidden at login (see Entry points). Or, to watch them, run `py -m ahab.telegram` and `py -m ahab.heartbeat` in two terminals. Both need to be running for chat to work.

The pairing lives in `home/telegram.json`. Delete that file and pair again to switch accounts.

## Entry points

- `py -m ahab.inbox "text"` — give Ahab a task or a message; it reads it at its next wake. Add `--now` to wake it within a few seconds. `py -m ahab.inbox` alone shows the conversation.
- `py -m ahab.heartbeat` — run Ahab in this terminal, printing each wake-up; Ctrl+C stops it. `--once` runs a single wake-up. Only one heartbeat can run per home.
- `py -m ahab.telegram` — run the Telegram bridge (`--pair` links it to your account first; see above).
- `py -m ahab.approvals` — list what Ahab is waiting on you to approve; `--yes N` / `--no N` answers at the PC, and `--test "text"` sends a harmless test card to check the path.
- `py -m ahab.goals` — Ahab's open goals and the last few ended; `py -m ahab.goals N` shows one goal's whole log. `--start "<objective>"` opens a goal of yours, and `--pause`, `--resume`, `--drop`, `--done N`, `--budget N 2M`, `--days N 30` and `--note N "<text>"` steer one. The same works on Telegram as `/goals` and `/goal …` from your paired account.
- `py -m ahab.agents` — Ahab's crew and their recent tasks. `py -m ahab.agents new <name> --about "what it's for" [--model claude-haiku] [--groups vault,sources] [--outward "what it can do outward"]` adds one to `home.json` and makes its folder in `home/agents/<name>/`; then write its job into that folder's `LANE.md`. An agent with an `--outward` line gets tasks only through an Approve tap; `"agent_approval"` in `home.json` (`outward`, `always` or `never`) changes that.
- `py -m ahab.workshop` — list Ahab's sites and how their latest builds went (`--setup`, `--login`: see above).
- `py -m ahab.session` — Ahab's running session: how big it is against its size, and its summary. `--compact` folds the older part into the summary now; `--new` starts a fresh session (the old one stays in the log). `"session_tokens"` in `home.json` sets the size (100,000 by default; the `local` backend's default is 16,000).
- `py scripts/simulate_sessions.py` — months of the running session in minutes, with no model: the real heartbeat against a throwaway home, with a fake clock, a fake agent drawn from measured wake-up sizes, a fake owner (`--owner busy` or `quiet`) and a flaky fake summarizer. It checks the session's rules after every wake and prints the spread across runs: folds, page sizes, the token bill and how much a prompt cache would reuse (`--help` for sizes, failure rates and `--profile` to measure from another `memory.db`).
- `py -m ahab.memory` — print what Ahab sees of its memory. `py -m ahab.memory search <words>` searches the full log and memory files, as recall does.
- `py -m ahab.report` — the weekly memory report (writes while awake, MEMORY.md's size, lookups, "don't remember"s), which the watchdog also sends on Telegram every Sunday at 7pm. `--send` sends it now.
- `py -m ahab.senses` — what Ahab can see, read and hear here; `--warm` downloads the speech model, `<file>` reads or transcribes one file.
- `py -m ahab.web <url>` — fetch a page exactly as Ahab would see it (`--search <words>` for its Wikipedia search).
- `py -m ahab.browser --setup` — build and start the browser box (needs Docker Desktop); `--check` runs the fence test (what must open and what mustn't, about 20 seconds), `open <url>` prints a page as Ahab sees it, `--stop` removes it. Ahab starts the box itself when it first browses, as long as Docker Desktop is running.
- `py -m ahab.dream` — have Ahab dream now instead of at 7:00 (only while the heartbeat is stopped). Dreams are written to `home/workspace/dreams/`.
- `py -m ahab.days` — regenerate `home/days/` from the log.
- `py -m ahab.watchdog --dry-run` — what the watchdog sees right now (heartbeat, bridge, n8n), without sending anything.
- `py -m ahab.mail` — the email watch: how many emails each agent on the list sent in the last day, and what's waiting out its delay. `--lift` lets Ahab and the agents talk again (or send `/talk` to the bot). The list, with which addresses are agents, is `home/email.json` (see `ahab/mail.py`).
- `py tests/run.py` — run the tests (all offline: fake relays, fake APIs, throwaway homes; `py tests/run.py test_vercel` runs one). GitHub runs them on every push, on Linux and macOS.
- `.\scripts\install-task.ps1` — run Ahab unattended: registers Windows scheduled tasks, "Ahab heartbeat" and "Ahab telegram" (the latter only if `AHAB_TELEGRAM_TOKEN` is set), that start hidden at login and restart if they crash, and "Ahab watchdog" (also only with the token), which checks every 10 minutes that both are running and n8n answers, and tells you on Telegram if not. Output goes to `home/heartbeat.log`, `home/telegram.log` and `home/watchdog.log`. `-Remove` uninstalls them. Stop one with `Stop-ScheduledTask "Ahab telegram"`. After changing code, `py -m ahab.heartbeat --restart` reloads the heartbeat; for the bridge, `Stop-ScheduledTask` then `Start-ScheduledTask`.
- `py -m ahab.heartbeat --restart` — restart the unattended heartbeat, for example to load new code.
- `.\scripts\install-backup-task.ps1` — registers "Ahab backup", which commits and pushes `home/` to its private repo every 6 hours, only while Ahab is asleep (`-EveryHours N` to change, `-Remove` to uninstall). `py -m ahab.backup` does one backup by hand. `home/` must be its own Git repo with a remote.
- `python3 scripts/install_mac.py` — the Mac equivalent: launchd agents for the heartbeat and, once it's paired, the Telegram bridge, running with no window, at every login, restarted after a crash (`--with-backup` adds the backup, `--remove` uninstalls, `--dry-run` shows the files). Run it from a terminal where Ahab already works, after stopping any copy running there with Ctrl+C: it copies that terminal's keys, settings and PATH into the agents, since launchd doesn't read `~/.zshrc`. Run it again after changing a key.
- `sudo python3 scripts/install_linux.py --owner YOU` — the Linux equivalent, for a machine of Ahab's own: systemd services running as an `ahab` user, and a separate user for the agent's shell (see "On Linux" above; `--dry-run`, `--remove`). As `ahab`, `python3 -m ahab.heartbeat --restart` reloads the heartbeat and `journalctl -u ahab-heartbeat` shows its log.
- `python3 -m ahab.console` — a live status screen: asleep or awake, the model it runs on and the tokens spent so far, the session's size, the last messages, the recent log, any workers and crew agents at work, the services and the machine, redrawn every 5 seconds, read-only (`--once` prints one frame). The Linux installer puts it on the machine's own screen in place of the login prompt (`--no-console` to skip); `"console": {"show_chat": false}` in `home.json` hides the conversation for a screen in a shared room.
- `py -m ahab.sources` — each source and when it last arrived; `receive <name>` takes one delivery as a tar on stdin (what the forced-command key runs).
- `py -m ahab.digest [source] [--all]` — run the digest by hand: distills what's new in each source (or one) into pages under `workspace/memory/sources/<name>/`; `--all` re-reads everything. The heartbeat runs it each morning before the dream.
- `py -m ahab.desktop --setup | --check | --watch | --stop` — the desktop box; `look`, `windows`, `click X Y`, `type TEXT`, `key KEYS`, `launch APP` and `shell CMD` drive it by hand.

## License

MIT, like Lobo.
