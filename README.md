<p align="center">
  <img src="branding/proposals/logbook/banner.jpg" alt="Ahab — Logbook banner" width="100%">
</p>

<h1 align="center"><img src="branding/proposals/logbook/mark.svg" alt="" width="48" valign="middle"> Ahab</h1>

Ahab is a self-built, persistent personal assistant. It wakes on a schedule it picks, takes on work you hand it, keeps track of what's going on, brings things up on its own, and is someone you can just talk to. Its sense of who it is grows in its notes over time.

Originally named **Perro**, this software grew out of the Lobo (`../Lobo`) runtime. It is now called **Ahab**; **Pip** is the continuing personal agent running on it. The runtime is a fork of Lobo's, adapted for a persistent personal assistant. See [AGENTS.md](AGENTS.md) for the rules, [docs/ANATOMY.md](docs/ANATOMY.md) for the overview, [docs/STATUS.md](docs/STATUS.md) for what exists and what's next, and [docs/BUILD_LOG.md](docs/BUILD_LOG.md) for the details.

**Naming transition:** This is a documentation and repository rename, not a code migration. The Python package (`perro`), CLI commands (`py -m perro...`), environment variables (`PERRO_*`), current filesystem paths, Docker resources, Windows task names, and existing n8n credentials retain their original identifiers until a separate migration. Use the commands and paths documented below as written.

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
  ├─ workshop       → Codex builds static sites in a locked Docker box (workshop/); nothing is published
  ├─ browser        → a real Chromium in its own locked box (browser/), public sites only, never sends anything
  ├─ github         → keeps those sites in private repos on its own GitHub, through n8n; public only by approval
  ├─ vercel         → puts them online on its own Vercel: previews behind Vercel's sign-in, public only by approval
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

`install.sh` sets Ahab up without Git or anything else: it finds Python 3.11 or newer (or, after asking, installs it with Homebrew, or with the signed python.org installer if there's no Homebrew), downloads this repo from GitHub as a zip into `~/Perro` (`--dir` to change), checks that it starts, and prints what to do next. Read it before you run it:

```bash
curl -fsSLO https://raw.githubusercontent.com/silascroe/ahab/main/install.sh
```

```bash
bash install.sh
```

Running it again updates the code and leaves `home/` alone. On a Mac use `python3` where this README says `py`, and `~/.perro` where it says `%LOCALAPPDATA%\Perro`. Tested on Linux with faked Mac tools, not yet on a real Mac. A Windows version is still to come.

Set your DeepSeek key as an environment variable (never in a file here), then open a new terminal, since `setx` only affects new ones:

```powershell
setx DEEPSEEK_API_KEY "sk-..."
```

Check the model client works:

```powershell
py -m perro.model "Say hi in five words."
```

### The senses (optional installs)

Images need nothing extra: Codex and Claude Code take them, and an API backend does if its entry in `BACKENDS` (`perro/model.py`) says `"vision": True`. Reading PDFs needs pypdf, and hearing voice notes on this PC needs faster-whisper (about 500 MB for the speech model, downloaded once):

```powershell
py -m pip install -r requirements.txt
py -m perro.senses --warm
```

Or skip faster-whisper and set `GROQ_API_KEY` (or `OPENAI_API_KEY`) to transcribe through that API; `PERRO_HEARING` picks one when several are set up. `py -m perro.senses` shows what's set up, and `py -m perro.senses <file>` reads or transcribes one file as Ahab would.

### Obsidian (optional)

Pip keeps its own vault in `home/workspace/vault/`; open that folder as a vault in Obsidian to read along. To have it work in a vault of yours instead, put `"obsidian_vault": "C:\path\to\vault"` in `home/home.json`. Nothing else is needed: a vault is plain Markdown files. Pip never touches `.obsidian/` (the app's settings and plugins), and what it deletes goes to the vault's `.trash/`.

### Window screenshots (off unless you allow an app)

`screenshot_window` lets Pip look at one app's window on your screen, for the apps you list by program name in `home/home.json`, e.g. `"screen_apps": ["Obsidian"]`. With no list it refuses everything. It never takes the whole screen, and it can't click or type. Windows only.

### The desktop (computer use, in Docker)

`py -m perro.desktop --setup` builds and starts Pip's desktop: Linux with XFCE, Firefox, LibreOffice, Obsidian and a terminal, on a 1024x768 virtual screen, in a locked container with no network. `--check` runs the fence test. `--watch` shows the screen live at http://127.0.0.1:8765/. To let it reach some sites, list them in `home/home.json` as `"desktop_hosts": ["example.com"]`; anything in the box can then reach (and post to) those, and nothing else. It stops itself after 15 idle minutes; its files live in the Docker volume `perro-desktop-home`.

### Codex or Claude Code as the model (instead of an API key)

Ahab can think through your Codex or Claude Code subscription: backends `codex-luna` (GPT-6 Luna, max effort) and `claude-haiku` (Claude Haiku). Each CLI runs as the model only, with its own tools off, and uses a separate profile folder under `%LOCALAPPDATA%\Perro` (`~/.perro` on a Mac; `PERRO_CLI_HOME` to move it), so it never sees your own CLAUDE.md, AGENTS.md, memories or plugins. Log in once per profile, in a terminal you close afterwards:

```powershell
New-Item -ItemType Directory -Force "$env:LOCALAPPDATA\Perro\codex", "$env:LOCALAPPDATA\Perro\claude" | Out-Null
$env:CODEX_HOME = "$env:LOCALAPPDATA\Perro\codex"; codex login
$env:CLAUDE_CONFIG_DIR = "$env:LOCALAPPDATA\Perro\claude"; claude auth login
```

If `codex` isn't on your PATH, the desktop app's copy is under `~\.codex\packages\standalone\releases\<version>\bin\codex.exe` (Ahab finds it there on its own, or set `PERRO_CODEX_BIN`). Then put `{"backend": "codex-luna"}` in `home/home.json` and restart the heartbeat.

### Google and Notion (through n8n)

Ahab never signs in to Google or Notion. Your own n8n, in Docker, holds the sign-ins, and Ahab calls its webhooks on localhost. Everything to set it up is in [n8n/](n8n/README.md): the Docker setup, the workflows, and which sign-ins to create. Every call carries a key that n8n checks, so other programs and web pages on this PC can't use them. The key is the environment variable `PERRO_N8N_KEY`, and it must match n8n's credential "Perro relay key". Restart the heartbeat after changing it. See [docs/BUILD_LOG.md](docs/BUILD_LOG.md) for what each workflow does.

### The workshop (Codex builds sites, in Docker)

Ahab can have small static websites built: it writes a brief, and Codex builds the site in a locked container on this PC (see `perro/workshop.py` for exactly how it's locked down). Nothing is published. It needs Docker Desktop running and a Codex login of the builder's own, separate from Ahab's model profile:

```powershell
py -m perro.workshop --setup     # builds the image, starts the allowlist proxy
py -m perro.workshop --login     # once; add --device-auth to sign in with a code on your phone
```

Everything lives in `~\.perro\workshop` (`PERRO_WORKSHOP_HOME` to move it): `sites\<name>` is each site's folder, `runs\<id>` each build's logs and Codex's report, `codex-builder` the login. Open `sites\<name>\dist\index.html` to look at a finished site. It's deliberately not in AppData: a packaged Windows app (the Claude desktop app is one) gets AppData writes redirected into a private copy that Docker can't see.

### Telegram (chat from your phone)

Ahab itself never talks to Telegram. A small bridge (`perro/telegram.py`) runs next to the heartbeat, passes your messages into the inbox, which wakes Ahab within seconds, and sends Ahab's replies back to your phone. It also brings approval requests as cards with Approve and Deny buttons; only a tap counts as a yes.

1. In Telegram, message **@BotFather**, send `/newbot`, and pick a name and a username ending in `bot`. It gives you a token.
2. Save the token as an environment variable, then open a new terminal: `setx PERRO_TELEGRAM_TOKEN "123456:ABC..."`
3. Pair the bot with your account: `py -m perro.telegram --pair`. It prints a six-digit code; send that code to your bot. From then on the bridge only accepts messages from your account in your private chat with the bot. Anyone else who finds the bot is ignored, and their text never reaches Ahab.
4. Run `.\scripts\install-task.ps1` so the bridge and the heartbeat both start hidden at login (see Entry points). Or, to watch them, run `py -m perro.telegram` and `py -m perro.heartbeat` in two terminals. Both need to be running for chat to work.

The pairing lives in `home/telegram.json`. Delete that file and pair again to switch accounts.

## Entry points

- `py -m perro.inbox "text"` — give Ahab a task or a message; it reads it at its next wake. Add `--now` to wake it within a few seconds. `py -m perro.inbox` alone shows the conversation.
- `py -m perro.heartbeat` — run Ahab in this terminal, printing each wake-up; Ctrl+C stops it. `--once` runs a single wake-up. Only one heartbeat can run per home.
- `py -m perro.telegram` — run the Telegram bridge (`--pair` links it to your account first; see above).
- `py -m perro.approvals` — list what Ahab is waiting on you to approve; `--yes N` / `--no N` answers at the PC, and `--test "text"` sends a harmless test card to check the path.
- `py -m perro.workshop` — list Ahab's sites and how their latest builds went (`--setup`, `--login`: see above).
- `py -m perro.memory` — print what Ahab sees of its memory. `py -m perro.memory search <words>` searches the full log and memory files, as recall does.
- `py -m perro.report` — the weekly memory report (writes while awake, MEMORY.md's size, lookups, "don't remember"s), which the watchdog also sends on Telegram every Sunday at 7pm. `--send` sends it now.
- `py -m perro.senses` — what Ahab can see, read and hear here; `--warm` downloads the speech model, `<file>` reads or transcribes one file.
- `py -m perro.web <url>` — fetch a page exactly as Ahab would see it (`--search <words>` for its Wikipedia search).
- `py -m perro.browser --setup` — build and start the browser box (needs Docker Desktop); `--check` runs the fence test (what must open and what mustn't, about 20 seconds), `open <url>` prints a page as Ahab sees it, `--stop` removes it. Ahab starts the box itself when it first browses, as long as Docker Desktop is running.
- `py -m perro.dream` — have Ahab dream now instead of at 7:00 (only while the heartbeat is stopped). Dreams are written to `home/workspace/dreams/`.
- `py -m perro.days` — regenerate `home/days/` from the log.
- `py -m perro.watchdog --dry-run` — what the watchdog sees right now (heartbeat, bridge, n8n), without sending anything.
- `py -m perro.mail` — the email watch: how many emails each agent on the list sent in the last day, and what's waiting out its delay. `--lift` lets Ahab and the agents talk again (or send `/talk` to the bot). The list, with which addresses are agents, is `home/email.json` (see `perro/mail.py`).
- `py tests/run.py` — run the tests (all offline: fake relays, fake APIs, throwaway homes; `py tests/run.py test_vercel` runs one). GitHub runs them on every push, on Linux and macOS.
- `.\scripts\install-task.ps1` — run Ahab unattended: registers Windows scheduled tasks, "Perro heartbeat" and "Perro telegram" (the latter only if `PERRO_TELEGRAM_TOKEN` is set), that start hidden at login and restart if they crash, and "Perro watchdog" (also only with the token), which checks every 10 minutes that both are running and n8n answers, and tells you on Telegram if not. Output goes to `home/heartbeat.log`, `home/telegram.log` and `home/watchdog.log`. `-Remove` uninstalls them. Stop one with `Stop-ScheduledTask "Perro telegram"`. After changing code, `py -m perro.heartbeat --restart` reloads the heartbeat; for the bridge, `Stop-ScheduledTask` then `Start-ScheduledTask`.
- `py -m perro.heartbeat --restart` — restart the unattended heartbeat, for example to load new code.
- `.\scripts\install-backup-task.ps1` — registers "Perro backup", which commits and pushes `home/` to its private repo every 6 hours, only while Ahab is asleep (`-EveryHours N` to change, `-Remove` to uninstall). `py -m perro.backup` does one backup by hand. `home/` must be its own Git repo with a remote.
- `python3 scripts/install_mac.py` — the Mac equivalent: launchd agents for the heartbeat and, once it's paired, the Telegram bridge, running with no window, at every login, restarted after a crash (`--with-backup` adds the backup, `--remove` uninstalls, `--dry-run` shows the files). Run it from a terminal where Ahab already works, after stopping any copy running there with Ctrl+C: it copies that terminal's keys, settings and PATH into the agents, since launchd doesn't read `~/.zshrc`. Run it again after changing a key.
- `py -m perro.desktop --setup | --check | --watch | --stop` — the desktop box; `look`, `windows`, `click X Y`, `type TEXT`, `key KEYS`, `launch APP` and `shell CMD` drive it by hand.

## License

MIT, like Lobo.
