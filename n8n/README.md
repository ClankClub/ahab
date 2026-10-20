# n8n: Ahab's hands

Ahab never signs in to anything. A self-hosted [n8n](https://n8n.io) on your own computer holds the sign-ins, and Ahab calls its webhooks on localhost (`ahab/n8n.py`, `ahab/google.py`). This folder holds everything needed to set that up: the Docker setup and the workflows. What it can't hold are the sign-ins themselves, which are yours to make.

| Workflow | Webhook | What it does |
|---|---|---|
| `ahab-calendar.json` | GET `/webhook/ahab/calendar` | Upcoming events on every calendar Ahab's account can see, with their ids. |
| `ahab-email-search.json` | GET `/webhook/ahab/email/search` | Gmail search: sender, subject, date, snippet, thread id. The heartbeat also uses it once a minute to watch for mail from Pip's email list. |
| `ahab-email-read.json` | GET `/webhook/ahab/email/read` | One full email. |
| `ahab-email-attachment.json` | GET `/webhook/ahab/email/attachment` | One attachment of one email, as base64, by the message id and the attachment id from the full email. Read-only. |
| `ahab-email-send.json` | POST `/webhook/ahab/email/send` | Sends one plain-text email, to one address on a list, with at most one BCC also on the list, 30 a day. The list is SHA-256 hashes of the lowercased addresses, in the "Check the email" node: replace them with your own (`py -c "import hashlib; print(hashlib.sha256(b'you@example.com').hexdigest())"`), and put the same addresses in `home/email.json` (see `ahab/mail.py`). The Gmail sign-in needs the send permission, which n8n's default Gmail scopes include. |
| `ahab-drive-download.json` | GET `/webhook/ahab/drive/download` | One Drive file's contents, as base64 (one GET with `alt=media`; not Google Docs or Sheets, which can't be downloaded that way). Read-only. |
| `ahab-google.json` | POST `/webhook/ahab/google` | The relay for Drive, Docs, Sheets, Tasks, Contacts and Pip's own calendar. It refuses any address outside the service's API, DELETE except for tasks, task lists and single contacts, and anything about sharing, permissions, trashing, clearing or bulk deleting, so it can't share, or delete files, even if Ahab's code had a bug. For the calendar it allows only events on Pip's own calendar, only a title, times, place, notes and reminders (no guests, so no invitations), and every change with `sendUpdates=none`. |
| `ahab-notion.json` | POST `/webhook/ahab/notion` | The relay for Notion. It passes only the search, pages, blocks and databases parts of Notion's API. Deleting goes only Notion's recoverable way: DELETE of one block, or exactly `{"in_trash": true}` on one page or database; no untrashing, comments or sharing. |
| `ahab-github.json` | POST `/webhook/ahab/github` | The relay for GitHub, in Pip's own account. An allowlist: reading, making private repositories, and adding commits (blobs, trees, commits, unforced branch moves). The one setting it changes is making a repository public, which Ahab's code only asks for after you approve. It refuses deleting, collaborators, hooks, keys, secrets, Actions and files under `.github/`. |
| `ahab-vercel.json` | POST `/webhook/ahab/vercel` | The relay for Vercel, in Pip's own account. An allowlist: reading, making a project, uploading files, preview deployments of static files (no target, no build, no Git, nothing that runs code on Vercel), and redeploying one existing deployment as production, unchanged, which Ahab's code only asks for after you approve. It refuses deleting, domains, aliases, environment variables and protection settings. |
| `ahab-account-check.json` | GET `/webhook/ahab/account-check` | Optional. Asks GitHub, Vercel and Notion who Ahab is, to see whether a sign-in has lapsed. No Ahab tool uses it yet. |

Every webhook checks a header key, so other programs and web pages on your computer can't use them.

## Setup

**1. Run n8n.** Install Docker Desktop, then from this folder:

```powershell
$env:N8N_TIMEZONE = "Europe/Berlin"   # your timezone; leave it out for UTC
docker compose up -d
```

Open http://localhost:5678 and create the owner account n8n asks for.

**2. The key.** Make up a long random key and save it as the user environment variable `AHAB_N8N_KEY` (then open a new terminal; `setx` only reaches new ones):

```powershell
setx AHAB_N8N_KEY ([guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N"))
```

In n8n, add a credential of the type **Header Auth** named exactly `Ahab relay key`, with the header name `X-Ahab-Key` and that key as its value.

**3. A Google account for Ahab.** Give it its own account, not yours: what Ahab can reach is what this account can reach, and you share things with it on purpose. In Google Cloud, make a project, enable the Calendar, Gmail, Drive, Docs, Sheets, Tasks and People APIs, and create an OAuth client of the "Web application" type with the redirect URI `http://localhost:5678/rest/oauth2-credential/callback`. Publish the consent screen (External); in "Testing" the sign-ins expire every week.

In n8n, add one credential per service, named exactly as below, each with that OAuth client's ID and secret, and click "Sign in with Google" as Ahab's account:

| Name | n8n credential type |
|---|---|
| `Google Calendar account` | Google Calendar OAuth2 API |
| `Gmail account` | Gmail OAuth2 API |
| `Google Drive account` | Google Drive OAuth2 API |
| `Google Docs account` | Google Docs OAuth2 API |
| `Google Sheets account` | Google Sheets OAuth2 API |
| `Google Tasks account` | Google Tasks OAuth2 API |
| `Google Contacts account` | Google Contacts OAuth2 API |

**3b. Notion (optional).** Give Ahab its own Notion workspace. At notion.so/profile/integrations, make an internal integration for that workspace with read, update and insert content, and copy its secret. In n8n, add a **Bearer Auth** credential named exactly `Ahab Notion` with that secret. The integration sees only the pages and databases you connect to it: on each top-level one, the menu, then "Connections", then the integration (whatever's inside comes along).

For GitHub, Vercel and the optional account check: `Ahab Github` (GitHub API, a classic token with only the `repo` scope) and `Ahab Vercel` (Bearer Auth, a token for Ahab's own Vercel account), plus `Ahab Notion` from above.

**4. Import the workflows.** In n8n: "Create workflow", then the menu, then "Import from file", once per file in `workflows/`. Open each one; if a node shows a credential warning, pick the matching credential from step 2 or 3. Then switch each workflow to active (published). Or from a terminal:

```powershell
docker cp workflows/ahab-calendar.json n8n:/tmp/w.json
docker exec n8n n8n import:workflow --input=/tmp/w.json
docker exec n8n n8n publish:workflow --id=<the workflow's id, from the file>
docker restart n8n   # the running n8n doesn't notice a command-line import otherwise
```

**5. Check it.** With n8n running and the key set, `py -m ahab.heartbeat --once` from the Ahab folder runs one wake-up (stop the installed heartbeat first: only one can run per home); asking Ahab what's on its calendar exercises the whole path. If the key is missing, the heartbeat says so when it starts.

## Changing a workflow

These files are the source of truth. Edit in n8n if you like, but export the result back here ("Download" in the workflow's menu, or `n8n export:workflow --id=<id> --output=...`) and commit it, so the repo and your n8n don't drift. The files hold n8n's internal IDs and the names of the credentials they use, never the sign-ins or the key: those stay in n8n's own encrypted database, in the Docker volume.
