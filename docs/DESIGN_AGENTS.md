# Design: Pip at the head of a crew of agents

Agreed in outline with the owner on 2026-10-20. Phase 1 (crew agents) is built (see BUILD_LOG); phases 2 and 3 are still to come. When it's done, the story goes into `BUILD_LOG.md`, the fences into `AGENTS.md` and the module docstrings, and this file is deleted. The research behind it is the third comparative study (`03-orchestration.md` in the owner's reference folder, outside the repo), checked against OpenClaw's multi-agent and subagent docs and Hermes's A2A plugin source.

## Why

Pip can already hand a job to a worker: a throwaway run with a page of its own, read-only tools and one report. That covers "go read fifteen pages". It doesn't cover the other half of what OpenClaw and Hermes are known for: named agents with a lane of their own, which remember what they did last time, can write in their own space, run on a model the owner picked for that kind of work, and sit under one head that decides what the owner hears. Nor does it let Pip task the agents the owner already runs elsewhere, like Hermes.

So this gives Pip a roster of agents, of two kinds, reached the same way:

- **Crew agents** live inside the install. Each is a small Ahab of its own: its own folder, memory, notes and lane, the tool groups the owner gave it, and a model the owner chose. It has no heartbeat. It runs when Pip hands it a task (or, later, on a schedule the owner approved), does the task, updates its notes, reports, and stops. What it learned stays on disk for next time.
- **Remote agents** live somewhere else (Hermes, OpenClaw, Agent Zero) and are reached over A2A, the open agent-to-agent protocol all three speak.

Crew agents come first because they make the whole thing testable without Hermes, and because they're the useful half for any install: a librarian that owns the vault, a researcher that keeps its own reading notes, a builder lane that holds the workshop, each on a cheaper model than Pip's brain, each keeping its clutter off Pip's page.

The owner's instinct about OpenClaw is right, and it's the model here. In OpenClaw, every agent has a persistent workspace and session store, but one agent owns the heartbeat; the others run when a session is sent their way. A subagent run ends; the agent doesn't. Same here: the agent persists, its runs don't.

## The rules (the part that doesn't move)

**One head, one voice.** Agents report to Pip, never to the owner. No agent has `message_owner`, and none can file approvals in phase 1. Pip reads the report and decides what the owner hears. This is the worker rule, kept.

**No chatter.** Agents never talk to each other. There's no channel between them, an agent can't start a task for another agent (or a worker), and a reply never sets off another task by itself. If agent B needs what agent A found, Pip reads A's report and writes B's task. Every hop goes through Pip's judgment and shows in Pip's log. A follow-up in the same thread is a new task, counted against the limits like any other.

**Replies are information.** A report is text Pip reads, like a worker report or a web page. It can't approve, message, schedule or start anything, and nothing in it is treated as the owner's word.

**Delegation doesn't launder anything.** An agent never has more reach than Pip would have doing the job itself, unless the owner approved that exact task. In code:

- A crew agent's tools are core tools minus the outward and scheduling ones, plus the groups its roster entry lists, from a fixed set marked safe for agents. Its file tools reach only its own workspace. Every fence Pip's tools have (the delete brake, the relays' own refusals, the web reader's private-address refusal) holds for it too, because it's the same code.
- An agent whose roster entry says it has **outward powers** (Hermes can send mail through its own allowlist; a crew agent might one day hold `publishing`) gets a task only through the approval step. Pip files `agent_task` with the agent's name and the exact task text; code writes the card, which shows the agent, what the owner said it can do outward, and the whole task; the owner taps Approve; the next wake sends it. Task text for these agents is capped (`OUTWARD_TASK_CHAR_LIMIT`, about 3,000 characters) so the whole of it fits on the card. The owner approves what's actually sent, not Pip's summary of it.
- That approval is the default, and it's a setting: `agent_approval` in `home.json`, `"outward"` (the default), `"always"` (every task to any agent) or `"never"` (the owner's call, for someone who trusts their own agents). When Ahab grows permission levels, this key folds into them. Pip can't change it, since `home.json` is outside the workspace.

**Remote agents see Pip as a peer, not the owner.** Hermes and OpenClaw both frame peer text as untrusted outside input, refuse a peer's slash commands, and won't let a peer resolve their approvals. That's right, and it means anything Hermes keeps for its operator, Pip can't get it to do. Pip's task goes out as Pip's, never dressed up as the owner's words.

**URLs and tokens come only from the owner.** The roster in `home.json` is the only place an address comes from: never the model, a reply or an agent card. Tokens live in environment variables (`/etc/ahab/env` on the laptop, unreadable to the `pip` shell user), named by the roster, never in a file. Plain `http` is allowed only for loopback, private and Tailscale (`100.64.0.0/10`) addresses; anything public needs `https`, so a token never crosses the open internet in the clear. This is a deliberate exception to `web.py`'s private-address refusal and lives in the new module, not in `web.py`.

**Nothing listens.** Pip calls out; there's no inbound A2A door on the laptop. Other agents calling Pip can come later if there's a reason.

**Limits,** in code: `MAX_RUNNING` agent tasks at once (3), `DAILY_LIMIT` a day across all agents (20), `THREAD_LIMIT` turns in one thread (10), and a time limit per task from the roster (default 30 minutes). The limits stop a loop Pip talks itself into, which matters more than any one agent misbehaving.

## The roster

The owner writes it in `home.json`. Pip can read it, never edit it.

```json
"agents": {
  "librarian": {
    "kind": "crew",
    "about": "Owns the Obsidian vault: filing, linking, tidying.",
    "model": "claude-haiku",
    "groups": ["vault", "sources"],
    "max_calls": 30
  },
  "hermes": {
    "kind": "a2a",
    "about": "The owner's Hermes on the home server: long errands, its own skills.",
    "url": "http://100.x.y.z:9900/",
    "token_env": "AHAB_A2A_HERMES",
    "outward": "Can send email to its allowlist and post to its own Telegram.",
    "timeout_minutes": 30,
    "cost": "Runs on its own provider, not Pip's."
  }
}
```

`outward` is the owner's plain-language note of what that agent can do to the world. Its presence is what makes tasks to it need approval (under the default setting), and its text goes on the card. A crew agent gets no `outward` line unless the owner gives it an outward group, which phase 1 doesn't allow.

`model` for a crew agent is any backend in `model.py`. The workers' rule (nothing pricier than Pip's own brain) is a rule about what Pip may pick; the roster is the owner's, so the owner can put a crew agent on whatever they like.

Making a crew agent is the owner's call, like OpenClaw's operator approval for new agents. Phase 1: `py -m ahab.agents new librarian --model claude-haiku --groups vault,sources` writes the roster entry and the agent's folder. Phase 3: Pip can propose one through an `agent_create` approval, whose card shows the exact roster entry and lane.

## A crew agent

Its folder is `home/agents/<name>/`, a home of its own, so the existing `Memory`, file tools, `_resolve()` fence and recall all work on it unchanged:

```
home/agents/librarian/
  LANE.md          what it owns, what it doesn't, how it works (the lane contract)
  memory.db        its own log: its tasks, tool calls, reports
  workspace/       its own files; its tools reach only here
    MEMORY.md      its notes to itself, which it keeps up as it goes
```

`LANE.md` sits outside the agent's workspace, so an agent can grow its notes but can't rewrite its own job. The owner edits it (Pip proposes changes in phase 3). It follows OpenClaw's lane contracts: owns, doesn't own, tool posture, and when to say "this isn't mine" in a report instead of guessing. It's in the backed-up home, so it's private, not in the public repo.

A run is one task. Its page is a short generic agent prompt, its `LANE.md`, its `MEMORY.md`, and Pip's task with the current time. It works through the same loop the workers use (the JSON reply form, one tool at a time, results trimmed as it goes, the call budget and the clock), and finishes with a report for Pip and, optionally, new notes for its `MEMORY.md`. That end-of-task note is its whole memory upkeep: it has no heartbeat, so it has no dream.

Its tools: the core tools without `message_owner`, `set_alarm`, `cancel_alarm`, `set_intent` and `cancel_intent` (no heartbeat to wake, no owner to message), plus its listed groups, from the groups marked safe for agents (`agents.AGENT_GROUPS`, with the tools in each it may not have). Phase 1 marks only groups that read, or write in the agent's own space or Pip's own accounts under the existing relay fences: sources, vault, email without `send_email`, calendar, Google files, tasks and contacts, Notion. Left out for now:

- **watches:** a watch wakes Pip, and an agent shouldn't be able to set Pip's alarm clock.
- **browser, desktop, workshop:** each is one shared box or session, and two runs driving one box will step on each other. They come in once the code holds a lock per box (phase 3).
- **shell, screen:** the host shell and the owner's real screen are Pip's alone.
- **publishing and sending mail:** outward; they wait until crew agents can file approvals, with cards naming the agent (phase 3).
- **workers, agents:** agents don't delegate.

The real worker code does most of this already. Phase 1 pulls the worker loop out of `workers.py` into a shared runner both use, and the crew agent is a worker with a name, a home, a lane, notes and more tools. Workers stay as they are for Pip: a worker is the cheap, forgetful choice, a crew agent the one that learns its lane. Pip's prompt says when to pick which.

## A remote agent

`ahab/a2a.py` is a small A2A v1.0 client in the standard library: the agent card, `SendMessage` with text parts, `GetTask`, `CancelTask`, and a `contextId` per thread. One client for every agent that speaks A2A; per-kind adapters only come back for some later agent that doesn't.

Code does the waiting, not the model. `ask_agent` files a task row and starts a courier process, the way `start_worker` does. The courier sends the task, then waits: by polling `GetTask` every 30 seconds if the peer can return straight away (OpenClaw's `returnImmediately`), or by holding the call open for the roster's timeout (Hermes holds it until the reply, up to its own `A2A_REPLY_TIMEOUT`, 300 seconds unless raised). Either way Pip sleeps and spends nothing. The reply is saved in Pip's workspace, previewed in Pip's log as an `agent` event, and the heartbeat wakes Pip.

Things each peer does that the design has to live with:

- **Hermes's own approvals fail closed.** When a peer's task makes Hermes stop for its operator's approval, Hermes sends the prompt back to the session's chat, which in an A2A turn is the peer, Pip. Pip can't approve it (rightly), so the wait runs out and the action doesn't happen. Phase 2 checks live what the courier actually receives then. Either way the report says plainly that Hermes stopped for approval, and Pip tells the owner to do that part on Hermes directly. Tasks that obviously need Hermes's approval aren't worth sending.
- **Hermes remembers; OpenClaw doesn't.** A Hermes peer talks to the same Hermes the owner does, with its full memory and tools, so it learns from Pip's tasks. An OpenClaw peer gets a sealed session per peer and thread. Pip assumes neither: whatever the agent needs to know goes in the task.
- **Cancelling is best effort.** Hermes supports `CancelTask` (though it can't stop a turn already running); OpenClaw refuses it on purpose. `cancel_agent_task` says which happened, and a crew agent's cancel is real, as with workers.
- **Their tokens are theirs.** A remote agent runs on its own provider, so the roster's `cost` line goes on Pip's page and Pip delegates with that in mind.

The network side for the owner's Hermes is in the study: a per-peer token in Hermes's `.env`, its A2A port published on the home server's Tailscale address only, a Tailscale ACL that lets the laptop reach that one port, and Hermes's own outbound A2A tools left off.

## Pip's side

A new tool group, `agents`, shown when the roster isn't empty:

- `agents {}`: the roster (name, kind, about, cost, whether tasks need approval) and recent tasks.
- `ask_agent {agent, task, follow_up?, wake?}`: hands over a task. `follow_up` names an earlier task to continue its thread (the same `contextId` for a remote agent; the earlier exchange on the page for a crew agent). Returns at once: started, or filed for the owner's approval.
- `check_agent_task {id}`: status, the steps so far for a crew agent, the reply when there is one.
- `cancel_agent_task {id}`.

Reports land in `workspace/agents/<name>/<id>.md`, which Pip can read and quote. A crew agent's own folder stays out of Pip's reach in phase 1; reading it (for supervision) is easy to add if it turns out useful. Tasks live in their own table in Pip's `memory.db` (`agent_tasks`), with the courier's steps in a steps table, as with workers, so twenty tool calls by the librarian don't push Pip's history off its page. `py -m ahab.agents` lists them at the PC.

## Phases

**Phase 1, crew agents.** The shared runner pulled out of `workers.py`; the roster and `agent_approval` read from `home.json`; `ahab/agents.py` with the task table, the crew runner, `py -m ahab.agents new` and the listing; the `agents` tool group; the `agent_task` approval kind (a crew agent has no outward tools yet, but the owner can still give it an `outward` line to have its tasks approved); the heartbeat's wake on a finished task. Tests offline with fake models and throwaway homes: a task runs and reports, notes persist into the next run, file tools stay in the agent's workspace, the excluded tools are refused, an agent can't start an agent, the limits hold, and an `outward` entry routes through approval under each setting. Then one live run: a crew agent doing a small job in a throwaway home. (Built 2026-10-21; the live run was on DeepSeek, since this PC's Claude profile for Ahab isn't signed in.)

**Phase 2, remote agents.** `ahab/a2a.py` and the courier; the roster's URL and token rules; tested against a fake A2A peer (`http.server` on localhost in the test) that answers, stalls, returns a working task to poll, refuses a token and sends junk. Then the owner sets up Hermes's side, and a live run checks what really comes back, including the approval-prompt case.

**Phase 3, as wanted.** Standing orders (a crew or remote agent on a schedule the owner approved once, the one way an agent runs without Pip waking first); `agent_create` and lane changes through approvals; files handed in and out of a crew agent's workspace by code; per-box locks so crew agents can hold the browser, desktop or workshop; crew agents filing approvals with cards that name them; the Telegram switch (`/to hermes`) from the study.

## Decided, and still open

Decided with the owner: no agent-to-agent chatter; agents have no heartbeat of their own unless they have a scheduled task; delegation can't launder authority; tasking an agent with outward powers needs approval by default, and that becomes a permission setting.

Open, to settle while building:

- Whether a crew agent's report should follow a contract (Hermes's `output_schema`: Pip says what shape it wants, one retry if it's wrong). Cheap to add to the shared runner; useful for workers too.
- Whether approving one task should cover the follow-ups in its thread. Per task is safer and costs the owner more taps; start there and see how it feels.
- Whether Pip should see a crew agent's notes. Leaning no in phase 1: Pip judges its reports, and that keeps the lanes honest.
