# Design: sources (one memory across everything the owner uses)

Drafted 2026-10-20 from the owner's brief. Phase 1 (Hermes delivery) is built; the rest is still a draft. When it's all built, the story goes into `BUILD_LOG.md`, the fences into `AGENTS.md` and the module docstrings, and this file is deleted. Background research: the owner's private notes on how Hermes and OpenClaw hand work around (`03-orchestration.md`), which aren't in this repo.

## Why

People don't use one AI. They have a personal agent, a free chat app, a cloud assistant, a notes app, and every one of them keeps its own small pile of who they are. Nothing joins those piles up, so they repeat themselves everywhere. The owner's brief: weeks after doing a project with some other agent, ask Pip about it and have him simply know. That means the whole project, not one remembered detail, without asking that agent live or digging through its files on the spot.

That's knowing, not orchestrating. Asking another agent a question while it runs (A2A, which Hermes and OpenClaw both speak) is a separate and later piece. This one is about Pip keeping a copy of what happened elsewhere and digesting it into his own memory over time.

## What a source is

A source is anywhere the owner's AI life leaves a record Pip can get a copy of. Each one has:

- **a name and a kind**, set by the owner in `home.json` (outside the workspace, so Pip can't add sources himself):
  ```json
  "sources": {
    "hermes": {"kind": "hermes", "about": "My Hermes agent on the desktop PC"}
  }
  ```
- **a way in.** Either *delivered* on a schedule by a small sender where the other agent lives (Hermes, OpenClaw: they're on the owner's own machines), or *uploaded* by the owner (ChatGPT, Muse and most cloud services only export when you click a button).
- **an unpacker** for its kind, which turns whatever arrives into plain text: one Markdown file per conversation, plus the source's own notes and files.
- **an archive** in Pip's home, `sources/<name>/`, which is a mirror of the latest copy.

The Pip side (the archive, search, digest and reminders) is the same for every source. Only the sender and the unpacker know about a particular product. That's what keeps it general.

## The archive

```
home/sources/hermes/
  conversations/   one .md per conversation, as the source exported it
  memory/          the source's own curated memory (Hermes: MEMORY.md, USER.md)
  files/           its workspace: text files only
  manifest.json    what arrived when: per file, a hash, size, title and dates
```

A delivery replaces the archive as a whole (the source is the authority on its own history). The manifest's hashes tell the digest what's new or changed since last time.

**It's backed up with the rest of Pip's home** (the owner's call, 2026-10-20). That's fine as long as it stays text. GitHub refuses files over 100 MB, gets unhappy past a few gigabytes, and Git keeps every version forever, so the unpackers keep only text (`.md`, `.txt`, `.json`, `.csv` and source code, capped per file and per source) and leave out images, zips and binaries. A month of new exports then costs Git only the conversations that changed. Raw uploads (a ChatGPT zip) sit in `sources/_incoming/`, which `.gitignore` leaves out, and are deleted once unpacked.

Never copied, whatever the kind: keys, logins, tokens, config files, caches, databases. For Hermes, that means none of `.env`, `auth.json`, `config.yaml`, `state.db`, `pairing/` or `cache/`. Hermes's own export runs with `--redact`, which scrubs credential-shaped strings out of the conversations as well.

## Hermes, the first source

**What Hermes has** (checked on the owner's install): the curated memory in `/opt/data/memories/` (`MEMORY.md`, `USER.md`), every conversation in `state.db`, learned skills in `/opt/data/skills/`, and workspaces at `/opt/data/workspace` and `/workspace`. It has a read-only export built in. Its Markdown format refuses a whole-history export without a filter, and the filters skip sessions still open, so the sender uses `hermes sessions export <file> --redact` (JSON lines, one session per line) and Ahab turns each session into Markdown on arrival, keeping what was said and the names of the tools used, never what the tools returned.

**The sender** (`scripts/send_hermes.py`, standard library, runs where Hermes runs, so it needs nothing of Ahab installed) does this:
1. runs the export inside the container (`docker exec <container> hermes sessions export ...`), which only reads;
2. builds a tar of the export, the memory files, the `SKILL.md` files of the skills Hermes made or changed (the stock ones it ships are told apart by the hashes in its `skills/.bundled_manifest`) and the workspaces' text files, inside the container by a small script fed in on stdin, streamed out over `docker exec` (never `docker cp`, the same rule as the desktop box);
3. pipes it to the Ahab machine over SSH.

It's scheduled with the platform's own scheduler (Task Scheduler on Windows, a systemd timer or cron elsewhere). Daily is enough; the digest only runs nightly anyway.

**The receiving end** is a forced-command SSH key. On the Ahab machine, the service user's `authorized_keys` gets one line for that key:
```
command="cd /home/ahab/ahab && python3 -X utf8 -m ahab.sources receive hermes",no-pty,no-port-forwarding,no-agent-forwarding,no-X11-forwarding ssh-ed25519 AAAA...
```
That key can do exactly one thing: hand a tar stream to `receive`. It can't open a shell, pick a different command or forward anything. A stolen key can only deliver a bad tar, so `receive` checks the stream the way the desktop box checks its own: regular files only, relative paths, no `..`, the allowed extensions, size caps. It unpacks into a staging folder and swaps it in only when the whole stream checks out.

When Hermes and Ahab run on the same machine, there's no SSH: the sender's output goes straight into `receive` (a `local` mode).

**Network.** On the owner's setup, the desktop PC already reaches the laptop over the home network (it's how this was built), so Tailscale isn't needed to start. It earns its place if the laptop ever leaves home, or when something needs to reach the other way. No port on the desktop PC opens and nothing in Hermes's compose file changes: the sender only runs `docker exec` locally and connects out.

**Setup on the owner's machines.** A key pair made on the desktop PC; its public half goes in the laptop's `ahab` user's `authorized_keys`, which the owner's account can write without a password. The installer can learn a `--source-key` option later. Then a scheduled task on the desktop PC. That's a short list.

## What Pip can do with the archive

One tool, `sources` (built as one tool rather than two, with tool grouping coming):

- **Search it.** `{"do": "search"}` searches every archive (or one) in its own full-text index, kept in step with the manifests, each hit labeled with its source.
- **Read it.** `{"do": "read"}`, read-only, and every path has to resolve inside `sources/<name>/`, the same fence as the Obsidian vault's. `{"do": "list"}` shows each source and when it last arrived. No writes, no deletes: the source is the authority on its own history, and two writers on one memory is how you get mush.

## The digest: turning an archive into knowing

Search alone isn't knowing. Knowing means Pip already has a page on "the project you did with Hermes", with where it got to and what's unresolved, and reads it the way he reads his other memory.

**When:** nightly, an hour before the dream, and only when the manifest says something changed. A quiet day costs nothing.

**Two stages, so a 2,000-conversation export doesn't cost a fortune:**
1. **Sorting, in code.** New and changed files, their sizes, titles, dates and the source's own manifest. Small items are batched. Anything over a size cap is cut down to its owner's lines plus the start and end of the replies.
2. **Reading, as a worker run.** It gets the sorted list, Pip's current memory index and the pages it already wrote for this source, plus a budget of calls. It decides what's worth reading in full. For each project, person or decision it finds, it checks what Pip already believes and updates `memory/sources/<name>/<topic>.md`: what happened, when, where it stands, and the archive paths it came from.

**The fence**, and this is the delicate part. The dream deliberately never sees tool output, so that a sentence planted in a web page can't rewrite Pip's memory. An archive is exactly that sort of material: Hermes reads web pages, and its replies quote them. So:
- The digest worker can only write under `memory/sources/<name>/`. Code enforces it: its write tool resolves there or refuses. It can't touch `SOUL.md`, `AGENTS.md`, `USER.md`, `MEMORY.md`, skills or `PROACTIVE_PREFERENCES.md`, can't message anyone, file approvals or start workers. Same rules as any worker.
- Every page there starts with a line saying it's a digest of another source, written by a run that read outside text. Pip treats it as information about what happened elsewhere, never as instructions.
- Evidence has ranks, and the digest prompt says so. The owner's own lines in a transcript are good evidence about the owner (what they wanted, decided, care about), though they're not orders to Pip: they were said to someone else, maybe months ago. The other agent's replies are weaker evidence. Anything quoted from the web or an email inside them is weakest.
- The dream can read `memory/sources/`, and may carry a fact up into `USER.md` or `MEMORY.md` only when it's in the owner's own words. It keeps the source tag on it, so `USER.md` might read "prefers X (said to Hermes, 2026-10-18)".

## Keeping it current

- **Delivered sources** (Hermes) keep themselves current. If no delivery has arrived in a few days, that's an event for Pip ("haven't heard from Hermes since Tuesday"), and it shows on the console.
- **Uploaded sources** get a nudge. Each one records when it was last fed, and about once a month Pip asks for fresh exports of the uploaded sources that are behind, with the one-line how-to for each ("ChatGPT: Settings, Data controls, Export"). The owner drops the zip on Telegram. Telegram bots can only download files up to 20 MB, so bigger exports go in a folder the owner can reach (Drive, or a shared folder on the machine), and the design for that waits until a real export shows the sizes.
- **Re-reading.** Once a month the digest runs over the whole archive's index (titles, dates, its own pages), not just what changed, and asks what looks different now: a project that went quiet, a preference that changed, something that mattered more than it seemed. It's cheap, because it reads its own pages and the manifest, and only opens transcripts it has a reason to.

## Phases

1. **Hermes delivery (built 2026-10-20):** the sender script, `sources.py` (`receive`, the archive and manifest, a status command), the forced-command key, the `sources` tool, and the console line. Pip can look things up; there's no digest yet. Tested offline with a fake Hermes export and hostile tars.
2. **The digest:** the sorting stage, the digest worker with its write fence, the dream's rule for carrying facts up, the staleness event. Tested with a fake archive containing a planted instruction.
3. **Uploaded sources:** a ChatGPT unpacker built against a real export, `_incoming/`, the Telegram drop, the monthly nudge and the re-read. Muse and others follow the same way.
4. **OpenClaw** as a delivered source, when it's installed: its memory is already Markdown, so its sender is the easy one.

## Placement and open questions

- **Should the owner's own words in a transcript ever go straight into `USER.md`,** or always through the dream's judgment? This draft says through the dream.
- **How much of the other agent's workspace to copy.** Text only and capped is the draft. Code projects could be large; maybe copy a project's tree and README and leave its full source in place.
- **Hermes's skills:** only the ones it made or changed, `SKILL.md` only. Whether even those are worth reading is for the digest to find out.
- **Product defaults:** sources are off until listed in `home.json`, like every other outside connection.
