# Status

## Where it stands (2026-10-18)

Forked from Lobo's runtime (`../Lobo`, commit `2076c5d`). None of Lobo's `home/` came along, so Perro's memory started empty.

**Character.** [AGENTS.md](../AGENTS.md), the README and the heartbeat prompt (`PROMPT_BODY` in `perro/heartbeat.py`) describe a personal assistant in the spirit of Muse or OpenClaw. It does work you hand it, takes initiative within its own tools, is good to just talk to, and has a personality. An earlier draft was too strict, close to "speak when spoken to"; the current wording is deliberately looser.

**Identity files** (modelled loosely on Muse's core files, built one at a time). Three files in `home/workspace/`, all shown at the top of every wake-up page and capped at 3,000 characters each (`perro/identity.py`). `SOUL.md` is who Perro is (origin, character, voice; it tells the owner when it edits it), and `USER.md` is what it knows about the owner; Perro edits both with `write_file`. `PROACTIVE_PREFERENCES.md` (2026-10-18) is the owner's: what to bring up unasked, what to leave alone, when and how. Muse's version is a blank form, because Muse has a separate pipeline that decides what to send and when. Perro has no such pipeline (its judgment on each wake-up decides), so this file starts with working defaults. Perro records preferences the owner tells it there, in their words, and adds no guesses of its own. The quiet hours in Perro's copy (about 4am to noon) are a guess by the build agent and say so. The prompt holds only the rules (trust, tools, reply format), which Perro can't edit. There's no separate IDENTITY file; it's folded into SOUL. Notes (4,000 characters) are now for open tasks and short-lived things. Generic starters live in `templates/` in the code repo (see its README); each wake-up copies in any that are missing, with the agent's name filled in, and never overwrites. Perro's own copies were written by hand for it and live only in the private home repo. Planned next: a read-only self-knowledge doc in the code repo describing what Perro can and can't do.

**What it can do.** Take tasks and conversation through the inbox or Telegram, research on the public web and Wikipedia, write files in its workspace, set reminders, message the owner, read its Google calendars (`check_calendar`), and search and read its own Gmail (`search_email`, `read_email`). It can't send email yet.

**Google, through n8n** (2026-10-18). Perro has its own Google account. A Cloud project, also named "Perro", holds an OAuth client of the Web type with redirect `http://localhost:5678/rest/oauth2-credential/callback`. Its consent screen is published, External, with no logo, so tokens don't expire weekly and no verification is triggered. The privacy-policy link points at [PRIVACY.md](../PRIVACY.md). The owner's n8n (`../n8n`, Docker, localhost only) holds signed-in credentials for Calendar, Gmail, Drive, Docs, Sheets, Tasks and Contacts; all seven answered a live API call. There are three workflows (copies in `../n8n/*.json`; that folder isn't in any repo):
- "Perro: calendar" (`/webhook/perro/calendar?days=N`) lists events from every calendar the account can see, so calendars the owner shares with it show up on their own.
- "Perro: email search" (`/webhook/perro/email/search?q=<gmail search>&max=N`) returns sender, subject, date and snippet.
- "Perro: email read" (`/webhook/perro/email/read?id=`) returns one full message.

Neither email workflow marks mail as read. `perro/n8n.py` calls webhooks by fixed path only, refuses any non-local n8n address, and shows times in the owner's local time. For email it prefers the plain-text part. HTML-only mail goes through `web.py`'s extractor, which drops hidden elements. It strips the invisible padding characters marketing mail uses, and it lists attachments without opening them. It was tested against a fake n8n, including a hidden-text injection, and live: once with a temporary calendar event that was then deleted, once with a real email from the owner's other account. Gmail message IDs differ per mailbox, so the sender's copy of an email and Perro's copy have different IDs. On its first wake with the new tools, Perro noticed them, tested both on its own and reported to the owner. Gotchas: n8n's production webhook only exists once the workflow is published; each Google API must be enabled separately in the Cloud project (a disabled one gives a 403 `SERVICE_DISABLED`); n8n 2.x's CLI can `import:workflow` and `publish:workflow`, but the running instance only notices after a restart.

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
- Cost: one 10-call wake-up used about 227k input tokens, because every call resends all earlier page fetches. Now only the latest tool result stays whole and long earlier ones are cut to 600 characters; in a fake 10-read wake-up that sent 36% of the characters. DeepSeek caches repeated prompt openings and bills those tokens far cheaper, so the untrimmed version cost less than its raw count suggested; the sleep line now shows how much was cached (`3081 in (2900 cached)`), and tokens from retried empty replies are counted too.
- Perro only saw the last 20 log events, about two chat exchanges (a reply makes ~9 events). Asked how long it had been alive, it dated its "earliest memory" to the last thing on its page, not its first wake-up. The page now has a "Recent conversation" section from the messages table (16 messages / 4,000 characters), and message events in the log show only their header.
- Told that SOUL.md is a file it can edit, it also wrote its notes into `workspace/notes.md` before using the notes field. The prompt now says notes aren't a workspace file.

**Git.** Code at `silascroe/perro` (public), `home/` at `silascroe/firstborn-perro` (private), the same split as Lobo. The "Perro backup" task is installed and ran hidden successfully, push included.

Not yet tested: the Mac installer (`scripts/install_mac.py`, which doesn't know about the bridge). A real reboot (2026-10-17) brought the heartbeat and bridge back on their own, with the backup still scheduled.

## Next, roughly in order

1. **Live with it for a while.** Chat with Perro through Telegram and adjust the prompt from what it actually does. Watch whether the proactive defaults produce useful messages or noise. Next identity file: the self-knowledge doc.
2. **Backend and budget.** The DeepSeek balance is small, with no auto-refill. Pick a backend deliberately before running unattended.
3. **More n8n workflows.** Drive, Docs, Sheets, Tasks and Contacts are connected and wait for workflows. The owner barely uses a calendar, so the useful calendar step is Perro writing to its own: the owner mentions an appointment, Perro adds it, and the owner subscribes to Perro's calendar on their phone.
4. **Sending mail.** Still undecided. Either the approval step (Perro asks on Telegram, the owner says yes) or a lighter version: send freely, BCC the owner on everything, and a daily send cap so a confused loop can't get the account banned. Mail to the owner's own addresses needs neither. The owner leans toward lighter, since Perro doesn't hold sensitive personal information.
5. **Approval before acting** for anything else with outside consequences (accepting invites, deleting, spending), built by the first such tool.
6. **Later:** a shell, only inside a container.

