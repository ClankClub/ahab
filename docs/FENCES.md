# Fences

The detailed boundaries each piece keeps, moved here from AGENTS.md so they aren't loaded into every session. They record what the code does and why; AGENTS.md has the short list of what matters when changing it.

Ahab's file tools only touch `<home>/workspace/`; every path goes through `_resolve()` in `ahab/tools.py`. That's fine for text files, not for a shell, which would need a container.

Web reading goes through `ahab/web.py`, which stays read-only and refuses private addresses.

Web search goes through `ahab/search.py`, whose providers each return the same results shape; the codex and claude ones are separate CLI runs with only search on. Never switch search (or any built-in tool) on in the brain's own CLI call, and keep Claude Code's WebFetch off, since it fetches from this PC.

Outside services go through the owner's local n8n: `ahab/connections/n8n.py` calls fixed webhook paths on localhost only, with the key in `AHAB_N8N_KEY`, and n8n holds the Google sign-ins. Ahab never sees a token.

Drive, Docs, Sheets, Tasks and Contacts share one relay workflow; `ahab/connections/google.py` builds the requests, and both it and the relay refuse sharing and trashing, and deleting anything but Ahab's own tasks, task lists and contacts.

Notion has its own relay workflow the same way (`ahab/connections/notion.py`): Ahab organizes its own workspace as it likes, and deletes only Notion's recoverable way (pages and databases to the trash, blocks one at a time). Those deletes are Ahab's own business (the owner's call, 2026-10-18), held to `DELETE_LIMIT` a day in `ahab/tools.py` so a planted instruction or a loop can't empty the place; keep that brake on any new delete tool.

GitHub's relay (`ahab/connections/github.py`) is an allowlist inside Pip's own account: private repositories and unforced commits, public only through the `github_public` approval, and nothing under `.github/`. Don't add Ahab's GitHub account to the code repos: its token would let it push to its own code.

Vercel's relay (`ahab/connections/vercel.py`) deploys static previews only; production moves only through the `vercel_publish` approval. Vercel makes a project's first deployment production no matter what, so the code spends it on a placeholder; don't connect a Git repository to a Vercel project, since that deploys `main` to production on every push. Anything that acts outward goes through the approval step there.

The browser box (`ahab/boxes/browser.py`, `browser/`) is Chromium under Playwright in a locked container whose only way out is its own copy of the workshop proxy in any-public-site mode; the proxy's private-address check is the whole fence there, so `py -m ahab.boxes.browser --check` must pass after any change to it, the driver or the box, and the driver's rules (GET and HEAD only, no WebSockets, WebRTC, downloads, popups or logins) stay. It isn't a way to get around a site's bot check: leave the user agent honest and take a challenge page as a no.

Workers (`ahab/workers.py`) get only the read-only tools in `WORKER_TOOLS` and pick from the fixed `MODELS` menu, which by the owner's rule holds nothing pricier than Ahab's own brain; they report to Ahab, never the owner, and never message, file approvals or start more workers.

Crew agents (`ahab/agents.py`) are the same with a name, a lane and a workspace of their own (`home/agents/<name>/`), made by the owner, never by Ahab: their tools are `CREW_CORE` plus the groups in `AGENT_GROUPS`, so a new core tool or group needs a decision there (the test checks every core tool is either given or refused); they never message the owner, file approvals, set alarms, intents or watches, or start workers or agents. Their deletes count on Ahab's own `DELETE_LIMIT`. A task for an agent with an `outward` line goes through the `agent_task` approval, word for word, under the owner's `agent_approval` setting.

Watches (`ahab/watches.py`) read pages only through `web.py`.

Goals (`ahab/goals.py`) keep their objective, budget, deadline and status in `memory.db`, out of Pip's tools' reach: Pip opens small ones itself (the limits are constants there), anything bigger and any extra budget or time goes through the `goal` and `goal_budget` approvals, whose cards code writes from the record, and only the owner (the paired Telegram chat's `/goal`, or the command line) edits, pauses, resumes or drops a goal they set or approved. Spend is charged by code from what the wake and its helpers actually used, never from what the model says. The goal tools aren't for workers or crew agents.

Media (`ahab/senses.py`) lives in the workspace like everything else: what arrives is saved under `media/`, and images reach the model only attached to a call by code (Codex's `--image`, Claude Code's image blocks, an API's image parts). Codex's `view_image` stays off, since it would let the model open files on this PC itself.

The email-attachment and Drive-download relays are read-only, one GET each.

The Obsidian vault (`ahab/connections/obsidian.py`) is the one place outside the workspace Pip's tools may reach, and only when the owner sets `obsidian_vault` in `home.json`; every path has to resolve inside the vault, nothing starting with a dot is touched (`.obsidian/` holds community plugins, which run code in the app), and deletes go to the vault's `.trash/` under `DELETE_LIMIT`.

Window screenshots (`ahab/boxes/screen.py`) look at the owner's real screen, so they stay one window at a time of an app the owner lists in `screen_apps`, matched by program name, never the whole desktop, and look-only; anything that clicks or types belongs in the desktop box, not here.

The desktop box (`ahab/boxes/desktop.py`, `desktop/`) is a Linux desktop in a locked container where Pip clicks, types and runs shell commands freely, because nothing in it reaches anyone: `--network none` unless the owner lists `desktop_hosts` in `home.json`, and then only those hosts through its own exact-host proxy. Nothing of the PC is mounted (its home is a Docker volume), files cross only through the code's checked tar streams, screenshots stay outside the backed-up home, and `py -m ahab.boxes.desktop --check` must pass after any change to the box, the driver or that list.

Sources (`ahab/sources.py`, `scripts/send_hermes.py`) are copies of what the owner did with other agents and apps, listed by the owner in `home.json`; `receive` is what a forced-command SSH key runs, so it trusts nothing in the tar (plain files only, safe names, the four folders, text extensions, size caps, staged and swapped whole), and the sender never packs keys, logins, config or databases. Pip's `sources` tool only reads, and the archive is information, not orders, the owner's lines in it included; Hermes conversations keep what was said and the tool names, never tool output.

The digest (`ahab/digest.py`) is the only thing that writes from it, and its one write tool is fenced in code to `memory/sources/<name>/` (size, page and per-run caps, a code-written marker on every page); it never gets the ordinary file tools, and the dream carries a fact up from its pages only in the owner's own words, with source and date.

The host shell (`ahab/boxes/hostshell.py`, set up by `scripts/install_linux.py`) runs commands on the machine itself, but as a separate Linux user (the owner's decision, 2026-10-20, for a machine that's the agent's own): it holds no secrets, isn't in the docker group and has no sudo, and the service user's sudo rights stay the one rule to become it plus start, stop and restart of its own units, listed exactly; keep the secrets out of that user's reach and the rule that narrow.

Otherwise code runs on Ahab's behalf in two places only: the desktop box, and the workshop (`ahab/workshop.py`, `workshop/`), Codex in a locked Docker container whose only way out is the allowlist proxy. Keep it that way: no Docker socket, host network, extra mounts or new proxy hosts without checking with the owner, and no secrets inside it beyond the builder's own Codex login.

The workflows are code too: `n8n/workflows/` is their source of truth (setup in `n8n/README.md`), so export any change made in n8n back there and commit it. The exports hold credential names and n8n's internal IDs, never sign-ins or keys; check that stays true before committing one.
