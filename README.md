# Perro

A persistent personal assistant in the spirit of Muse or OpenClaw. It wakes on a schedule it picks, takes on work you hand it, keeps track of what's going on, brings things up on its own, and is someone you can just talk to. Its sense of who it is grows in its notes over time.

The name is the joke: Lobo (`../Lobo`) is the wild wolf with a life of its own, and Perro is the wolf that decided to work with you. The runtime is a fork of Lobo's; the difference is what it's for. See [AGENTS.md](AGENTS.md) for the rules, [docs/ANATOMY.md](docs/ANATOMY.md) for the overview, [docs/STATUS.md](docs/STATUS.md) for what exists and what's next, and [docs/BUILD_LOG.md](docs/BUILD_LOG.md) for the details.

## The shape of it

```
Your PC: Perro runtime (Python)
  ├─ model client   → DeepSeek API, OpenRouter, or a local llama.cpp/Ollama server (all OpenAI-compatible),
  │                   or Codex / Claude Code run as the model only, through your subscription
  ├─ identity       → workspace/SOUL.md (who it is) + USER.md (what it knows about you) + PROACTIVE_PREFERENCES.md (what you want brought up unasked)
  │                   + AGENTS.md (its own lessons on how to work here); starters in templates/
  ├─ memory         → home/notes.md (open tasks) + home/memory.db (event log, messages)
  ├─ heartbeat      → wake, show memory, model decides, save, sleep until it chose
  ├─ inbox          → messages between you and Perro; where its instructions come from
  ├─ telegram       → bridge from a Telegram bot to the inbox, paired to your account only
  ├─ tools          → about_yourself (its guide, docs/ABOUT_YOURSELF.md), recall, list/read/write files, set/cancel alarms, message you, read web pages, Google, Notion, start/check builds, GitHub, Vercel
  ├─ web            → read-only: any public page as text, plus Wikipedia search
  ├─ search         → web search, run by a separate Codex or Claude Code call (providers in search.py)
  ├─ n8n, google    → its own Google account through your local n8n: Gmail (read; send to a list you keep),
  │                   its calendar, Drive, Docs, Sheets, Tasks, Contacts (read and write; deletes only
  │                   its own tasks and contacts, 25 deletes a day; no sharing or calendar guests)
  ├─ notion         → its own Notion workspace through the same n8n (its to organize; deleting goes to Notion's trash)
  ├─ alarms         → reminders and follow-ups; the heartbeat wakes it for them
  ├─ workshop       → Codex builds static sites in a locked Docker box (workshop/); nothing is published
  ├─ browser        → a real Chromium in its own locked box (browser/), public sites only, never sends anything
  ├─ github         → keeps those sites in private repos on its own GitHub, through n8n; public only by approval
  ├─ vercel         → puts them online on its own Vercel: previews behind Vercel's sign-in, public only by approval
  ├─ approvals      → outward actions wait for your Approve/Deny tap on Telegram, then run in code
  ├─ skills         → know-how files it keeps in workspace/skills/
  ├─ dream          → once a day at 7:00, a quiet wake that tidies notes and files and writes workspace/dreams/
  ├─ backup         → commits and pushes home/ to its private repo every 6 hours, only between wakes
  ├─ days           → home/days/: the log as one readable Markdown file per day
  └─ home/workspace/ → the only part of the disk its tools may touch
```

## Setup

Python 3.11+, standard library only for now. Run everything from this folder. On this PC `python` isn't on the PATH, so use the `py` launcher.

### On a Mac

`install.sh` sets Perro up without Git or anything else: it finds Python 3.11 or newer (or, after asking, installs it with Homebrew, or with the signed python.org installer if there's no Homebrew), downloads this repo from GitHub as a zip into `~/Perro` (`--dir` to change), checks that it starts, and prints what to do next. Read it before you run it:

```bash
curl -fsSLO https://raw.githubusercontent.com/silascroe/perro/main/install.sh
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

### Codex or Claude Code as the model (instead of an API key)

Perro can think through your Codex or Claude Code subscription: backends `codex-luna` (GPT-6 Luna, max effort) and `claude-haiku` (Claude Haiku). Each CLI runs as the model only, with its own tools off, and uses a separate profile folder under `%LOCALAPPDATA%\Perro` (`~/.perro` on a Mac; `PERRO_CLI_HOME` to move it), so it never sees your own CLAUDE.md, AGENTS.md, memories or plugins. Log in once per profile, in a terminal you close afterwards:

```powershell
New-Item -ItemType Directory -Force "$env:LOCALAPPDATA\Perro\codex", "$env:LOCALAPPDATA\Perro\claude" | Out-Null
$env:CODEX_HOME = "$env:LOCALAPPDATA\Perro\codex"; codex login
$env:CLAUDE_CONFIG_DIR = "$env:LOCALAPPDATA\Perro\claude"; claude auth login
```

If `codex` isn't on your PATH, the desktop app's copy is under `~\.codex\packages\standalone\releases\<version>\bin\codex.exe` (Perro finds it there on its own, or set `PERRO_CODEX_BIN`). Then put `{"backend": "codex-luna"}` in `home/home.json` and restart the heartbeat.

### Google and Notion (through n8n)

Perro never signs in to Google or Notion. Your own n8n, in Docker, holds the sign-ins, and Perro calls its webhooks on localhost. Everything to set it up is in [n8n/](n8n/README.md): the Docker setup, the workflows, and which sign-ins to create. Every call carries a key that n8n checks, so other programs and web pages on this PC can't use them. The key is the environment variable `PERRO_N8N_KEY`, and it must match n8n's credential "Perro relay key". Restart the heartbeat after changing it. See [docs/BUILD_LOG.md](docs/BUILD_LOG.md) for what each workflow does.

### The workshop (Codex builds sites, in Docker)

Perro can have small static websites built: it writes a brief, and Codex builds the site in a locked container on this PC (see `perro/workshop.py` for exactly how it's locked down). Nothing is published. It needs Docker Desktop running and a Codex login of the builder's own, separate from Perro's model profile:

```powershell
py -m perro.workshop --setup     # builds the image, starts the allowlist proxy
py -m perro.workshop --login     # once; add --device-auth to sign in with a code on your phone
```

Everything lives in `~\.perro\workshop` (`PERRO_WORKSHOP_HOME` to move it): `sites\<name>` is each site's folder, `runs\<id>` each build's logs and Codex's report, `codex-builder` the login. Open `sites\<name>\dist\index.html` to look at a finished site. It's deliberately not in AppData: a packaged Windows app (the Claude desktop app is one) gets AppData writes redirected into a private copy that Docker can't see.

### Telegram (chat from your phone)

Perro itself never talks to Telegram. A small bridge (`perro/telegram.py`) runs next to the heartbeat, passes your messages into the inbox, which wakes Perro within seconds, and sends Perro's replies back to your phone. It also brings approval requests as cards with Approve and Deny buttons; only a tap counts as a yes.

1. In Telegram, message **@BotFather**, send `/newbot`, and pick a name and a username ending in `bot`. It gives you a token.
2. Save the token as an environment variable, then open a new terminal: `setx PERRO_TELEGRAM_TOKEN "123456:ABC..."`
3. Pair the bot with your account: `py -m perro.telegram --pair`. It prints a six-digit code; send that code to your bot. From then on the bridge only accepts messages from your account in your private chat with the bot. Anyone else who finds the bot is ignored, and their text never reaches Perro.
4. Run `.\scripts\install-task.ps1` so the bridge and the heartbeat both start hidden at login (see Entry points). Or, to watch them, run `py -m perro.telegram` and `py -m perro.heartbeat` in two terminals. Both need to be running for chat to work.

The pairing lives in `home/telegram.json`. Delete that file and pair again to switch accounts.

## Entry points

- `py -m perro.inbox "text"` — give Perro a task or a message; it reads it at its next wake. Add `--now` to wake it within a few seconds. `py -m perro.inbox` alone shows the conversation.
- `py -m perro.heartbeat` — run Perro in this terminal, printing each wake-up; Ctrl+C stops it. `--once` runs a single wake-up. Only one heartbeat can run per home.
- `py -m perro.telegram` — run the Telegram bridge (`--pair` links it to your account first; see above).
- `py -m perro.approvals` — list what Perro is waiting on you to approve; `--yes N` / `--no N` answers at the PC, and `--test "text"` sends a harmless test card to check the path.
- `py -m perro.workshop` — list Perro's sites and how their latest builds went (`--setup`, `--login`: see above).
- `py -m perro.memory` — print what Perro sees of its memory. `py -m perro.memory search <text>` searches the full log.
- `py -m perro.web <url>` — fetch a page exactly as Perro would see it (`--search <words>` for its Wikipedia search).
- `py -m perro.browser --setup` — build and start the browser box (needs Docker Desktop); `--check` runs the fence test (what must open and what mustn't, about 20 seconds), `open <url>` prints a page as Perro sees it, `--stop` removes it. Perro starts the box itself when it first browses, as long as Docker Desktop is running.
- `py -m perro.dream` — have Perro dream now instead of at 7:00 (only while the heartbeat is stopped). Dreams are written to `home/workspace/dreams/`.
- `py -m perro.days` — regenerate `home/days/` from the log.
- `py -m perro.watchdog --dry-run` — what the watchdog sees right now (heartbeat, bridge, n8n), without sending anything.
- `py -m perro.mail` — the email watch: how many emails each agent on the list sent in the last day, and what's waiting out its delay. `--lift` lets Perro and the agents talk again (or send `/talk` to the bot). The list, with which addresses are agents, is `home/email.json` (see `perro/mail.py`).
- `py tests/run.py` — run the tests (all offline: fake relays, fake APIs, throwaway homes; `py tests/run.py test_vercel` runs one). GitHub runs them on every push, on Linux and macOS.
- `.\scripts\install-task.ps1` — run Perro unattended: registers Windows scheduled tasks, "Perro heartbeat" and "Perro telegram" (the latter only if `PERRO_TELEGRAM_TOKEN` is set), that start hidden at login and restart if they crash, and "Perro watchdog" (also only with the token), which checks every 10 minutes that both are running and n8n answers, and tells you on Telegram if not. Output goes to `home/heartbeat.log`, `home/telegram.log` and `home/watchdog.log`. `-Remove` uninstalls them. Stop one with `Stop-ScheduledTask "Perro telegram"`. After changing code, `py -m perro.heartbeat --restart` reloads the heartbeat; for the bridge, `Stop-ScheduledTask` then `Start-ScheduledTask`.
- `py -m perro.heartbeat --restart` — restart the unattended heartbeat, for example to load new code.
- `.\scripts\install-backup-task.ps1` — registers "Perro backup", which commits and pushes `home/` to its private repo every 6 hours, only while Perro is asleep (`-EveryHours N` to change, `-Remove` to uninstall). `py -m perro.backup` does one backup by hand. `home/` must be its own Git repo with a remote.
- `python3 scripts/install_mac.py` — the Mac equivalent: launchd agents for the heartbeat and, once it's paired, the Telegram bridge, running with no window, at every login, restarted after a crash (`--with-backup` adds the backup, `--remove` uninstalls, `--dry-run` shows the files). Run it from a terminal where Perro already works, after stopping any copy running there with Ctrl+C: it copies that terminal's keys, settings and PATH into the agents, since launchd doesn't read `~/.zshrc`. Run it again after changing a key.

## License

MIT, like Lobo.
