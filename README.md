# Perro

A persistent personal assistant in the spirit of Muse or OpenClaw. It wakes on a schedule it picks, takes on work you hand it, keeps track of what's going on, brings things up on its own, and is someone you can just talk to. Its sense of who it is grows in its notes over time.

The name is the joke: Lobo (`../Lobo`) is the wild wolf with a life of its own, and Perro is the wolf that decided to work with you. The runtime is a fork of Lobo's; the difference is what it's for. See [AGENTS.md](AGENTS.md) for the rules, and [docs/STATUS.md](docs/STATUS.md) for what exists and what's next.

## The shape of it

```
Your PC: Perro runtime (Python)
  ├─ model client   → DeepSeek API, OpenRouter, or a local llama.cpp/Ollama server (all OpenAI-compatible)
  ├─ identity       → workspace/SOUL.md (who it is) + workspace/USER.md (what it knows about you); starters in templates/
  ├─ memory         → home/notes.md (open tasks) + home/memory.db (event log, messages)
  ├─ heartbeat      → wake, show memory, model decides, save, sleep until it chose
  ├─ inbox          → messages between you and Perro; where its instructions come from
  ├─ telegram       → bridge from a Telegram bot to the inbox, paired to your account only
  ├─ tools          → recall, list/read/write files, set/cancel alarms, message you, read web pages
  ├─ web            → read-only: any public page as text, plus Wikipedia search
  ├─ alarms         → reminders and follow-ups; the heartbeat wakes it for them
  ├─ skills         → know-how files it keeps in workspace/skills/
  ├─ backup         → commits and pushes home/ to its private repo every 6 hours, only between wakes
  ├─ days           → home/days/: the log as one readable Markdown file per day
  └─ home/workspace/ → the only part of the disk its tools may touch
```

## Setup

Python 3.11+, standard library only for now. Run everything from this folder. On this PC `python` isn't on the PATH, so use the `py` launcher.

Set your DeepSeek key as an environment variable (never in a file here), then open a new terminal, since `setx` only affects new ones:

```powershell
setx DEEPSEEK_API_KEY "sk-..."
```

Check the model client works:

```powershell
py -m perro.model "Say hi in five words."
```

### Telegram (chat from your phone)

Perro itself never talks to Telegram. A small bridge (`perro/telegram.py`) runs next to the heartbeat, passes your messages into the inbox, which wakes Perro within seconds, and sends Perro's replies back to your phone.

1. In Telegram, message **@BotFather**, send `/newbot`, and pick a name and a username ending in `bot`. It gives you a token.
2. Save the token as an environment variable, then open a new terminal: `setx PERRO_TELEGRAM_TOKEN "123456:ABC..."`
3. Pair the bot with your account: `py -m perro.telegram --pair`. It prints a six-digit code; send that code to your bot. From then on the bridge only accepts messages from your account in your private chat with the bot. Anyone else who finds the bot is ignored, and their text never reaches Perro.
4. Run `.\scripts\install-task.ps1` so the bridge and the heartbeat both start hidden at login (see Entry points). Or, to watch them, run `py -m perro.telegram` and `py -m perro.heartbeat` in two terminals. Both need to be running for chat to work.

The pairing lives in `home/telegram.json`. Delete that file and pair again to switch accounts.

## Entry points

- `py -m perro.inbox "text"` — give Perro a task or a message; it reads it at its next wake. Add `--now` to wake it within a few seconds. `py -m perro.inbox` alone shows the conversation.
- `py -m perro.heartbeat` — run Perro in this terminal, printing each wake-up; Ctrl+C stops it. `--once` runs a single wake-up. Only one heartbeat can run per home.
- `py -m perro.telegram` — run the Telegram bridge (`--pair` links it to your account first; see above).
- `py -m perro.memory` — print what Perro sees of its memory. `py -m perro.memory search <text>` searches the full log.
- `py -m perro.web <url>` — fetch a page exactly as Perro would see it (`--search <words>` for its Wikipedia search).
- `py -m perro.days` — regenerate `home/days/` from the log.
- `.\scripts\install-task.ps1` — run Perro unattended: registers two Windows scheduled tasks, "Perro heartbeat" and "Perro telegram" (the latter only if `PERRO_TELEGRAM_TOKEN` is set), that start hidden at login and restart if they crash. Output goes to `home/heartbeat.log` and `home/telegram.log`. `-Remove` uninstalls both. Stop one with `Stop-ScheduledTask "Perro telegram"`. After changing code, `py -m perro.heartbeat --restart` reloads the heartbeat; for the bridge, `Stop-ScheduledTask` then `Start-ScheduledTask`.
- `py -m perro.heartbeat --restart` — restart the unattended heartbeat, for example to load new code.
- `.\scripts\install-backup-task.ps1` — registers "Perro backup", which commits and pushes `home/` to its private repo every 6 hours, only while Perro is asleep (`-EveryHours N` to change, `-Remove` to uninstall). `py -m perro.backup` does one backup by hand. `home/` must be its own Git repo with a remote.
- `scripts/install_mac.py` — the Mac equivalent of both tasks, also untested under the new name.

## License

MIT, like Lobo.
