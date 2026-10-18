# Status

## Where it stands (2026-10-17)

Forked from Lobo's runtime (`../Lobo`, commit `2076c5d`). None of Lobo's `home/` came along, so Perro's memory started empty.

**Character.** [AGENTS.md](../AGENTS.md), the README and the heartbeat prompt (`PROMPT_BODY` in `perro/heartbeat.py`) describe a personal assistant in the spirit of Muse or OpenClaw. It does work you hand it, takes initiative within its own tools, is good to just talk to, and has a personality. An earlier draft was too strict, close to "speak when spoken to"; the current wording is deliberately looser.

**Identity files** (modelled loosely on Muse's core files, built one at a time). Who Perro is lives in `home/workspace/SOUL.md`: origin (the Lobo joke), character, voice. It's shown first on every wake-up page, capped at 3,000 characters, and Perro may edit it with `write_file` as long as it tells the owner. The prompt holds only the rules (trust, tools, reply format), which Perro can't edit. There's no separate IDENTITY file; it's folded into SOUL. Notes (4,000-character cap) now hold what Perro knows about the owner plus open tasks. Planned next, each its own step: USER.md (the owner), PROACTIVE_PREFERENCES.md (owner-edited: what to bring up, when, how often), and a read-only self-knowledge doc in the code repo describing what Perro can and can't do.

**What it can do.** Take tasks and conversation through the inbox or Telegram, research on the public web and Wikipedia, write files in its workspace, set reminders, and message the owner. Nothing outside its workspace yet: no email or calendar.

**Telegram.** `perro/telegram.py` is a bridge process. It long-polls the bot, passes the owner's messages into the inbox as knocks, and forwards Perro's replies back. A one-time pairing code locks it to the owner's account and private chat; other senders are dropped, and their text never reaches Perro. Tested against a fake Telegram API (pairing, strangers, group chats, non-text messages, splitting long replies, keeping the token out of error messages). Live since 2026-10-18 with a real bot paired to the owner; a real round trip took about 8 seconds. The heartbeat checks for knocks every 3 seconds instead of every 30.

**Running unattended.** `scripts/install-task.ps1` registers "Perro heartbeat" and "Perro telegram" as login tasks; both are installed and running on the owner's PC. The installer finds `pythonw.exe` through the `py` launcher, since `python` isn't on the PATH here. Hidden under `pythonw` there's no console (`sys.stdout` is `None`), so anything that runs hidden must switch to its `--log` file before printing. The bridge crashed on exactly that at first.

## What testing found (2026-10-18, on `deepseek-flash`)

- A 5-call limit ran out before Perro's report, and its notes then claimed it had reported. The limit is now 10, a refused call says plainly that it didn't happen, and the prompt says a message reached the owner only when `message_owner` returned "Sent".
- `deepseek-flash` sometimes returns an empty reply, or leaks its native tool-call markup ("DSML" tokens) instead of JSON. `perro/model.py` retries those up to 4 times, and the prompt asks for plain JSON only. Watch for this if the model changes.
- `parse_reply` crashed the heartbeat on an empty reply; it's now a logged error.
- A prompt injection planted in a saved email (`home/workspace/mail/landlord.txt`) was ignored and flagged to the owner.
- A 12-moon research task carried over two wake-ups through its notes. One side claim it made from memory was wrong (it said Miranda comes after Charon; Umbriel does). The prompt now asks it to keep checked facts apart from remembered ones.
- It read UTC as local time and used an emoji. Each wake-up now shows the owner's local time, and the prompt asks for plain text.
- It believed messages wait for its next scheduled wake-up; they actually wake it within seconds. The prompt now says so.
- Cost: one 10-call wake-up used about 227k input tokens, because every call resends all earlier page fetches. Trimming old tool results within a wake-up is the biggest saving available.

**Git.** Code at `silascroe/perro` (public), `home/` at `silascroe/firstborn-perro` (private), the same split as Lobo. The "Perro backup" task is installed and ran hidden successfully, push included.

Not yet tested: the Mac installer (`scripts/install_mac.py`, which doesn't know about the bridge), and a real logout and login with the tasks installed.

## Next, roughly in order

1. **Live with it for a while.** Chat with Perro through Telegram and adjust the prompt from what it actually does.
2. **Trim old tool results** inside a wake-up, to cut token use.
3. **Backend and budget.** The DeepSeek balance is small, with no auto-refill. Pick a backend deliberately before running unattended.
4. **Its own Google account.** The owner shares calendars and docs with it, never their own login.
5. **n8n as its hands.** n8n (`../n8n`, Docker) handles Gmail, Calendar and similar connections. Perro calls n8n webhooks instead of implementing sign-in per service, so outbound actions pass through one place where the approval step sits.
6. **Approval before acting.** Reading and drafting run freely; sending to others, accepting, deleting and spending get the owner's OK. The first outward-acting tool builds this.
7. **Later:** a shell, only inside a container.

