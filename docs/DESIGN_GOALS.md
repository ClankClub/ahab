# Design: goals

Agreed in outline with the owner on 2026-10-21. Phase 1 is built (see BUILD_LOG); phases 2 and 3 are still to come. When it's done, the story goes into `BUILD_LOG.md`, the fences into `AGENTS.md` and the module docstring, Pip's side into `ABOUT_YOURSELF.md`, and this file is deleted. The research behind it is genre 3 of the second comparative study (`02-shared-genres.md` in the owner's reference folder, outside the repo), checked against the goal code in Hermes and OpenClaw and against a long-running Muse install's goal folders.

## Why

Pip has plenty of triggers. An alarm is a time, an intent is a phrase, a watch is a page, and a worker or crew task is one job with one report. None of them is an objective. Nothing says "this is unfinished, here's what done looks like, keep at it." So a big job either gets crammed into one wake's 20 tool calls or quietly stalls in the notes. A long-running thing like a weekly habit or a project waiting on someone else lives only as prose that Pip has to remember to reread.

The other agents split into two camps, and Pip wants a cross of the two:

- **Hermes and OpenClaw: a goal is a keep-going loop.** There's one objective per session. After each turn the agent is nudged to advance it instead of stopping, until it's done, blocked or out of budget. Hermes runs a separate judge model every turn to decide. OpenClaw lets the model mark only *complete* or *blocked*, and keeps pause, resume, edit and clear for the operator, so the agent can't quietly move the target.
- **Muse: a goal is a project folder.** It holds a description, a dated progress log, files, and the scheduled jobs that belong to it. "A goal by itself does not run anything." The jobs do the work, and deleting the goal takes its jobs with it.

Pip wakes on a schedule rather than holding a session open, so for Pip "keep going" means waking again soon while a goal is open and has budget. From Muse comes the goal as the thing Pip's other machinery hangs off, which matters more for Pip than for most: workers, crew tasks, alarms and watches done *for* a goal should count against it and end with it.

Pip has no judge model. A second model call each wake roughly doubles the cost of the wake. A hard budget, a deadline and code-side check-ins do the same job for a fraction of the cost: they stop a goal that's going nowhere.

## The rules

**Two sizes, decided by the numbers, not by Pip's description.**

- A *small* goal Pip may open on his own: at most `OWN_BUDGET` tokens (200,000, roughly 15 to 20 wakes), at most `OWN_DAYS` (14) to finish, at most `MAX_OWN_OPEN` (3) open at once. It opens straight away and shows in the record. The owner isn't pinged.
- Anything bigger is a *large* goal and needs the owner's yes. `start_goal` with a bigger budget or a longer deadline doesn't open anything. It files a `goal` approval, and code writes the card from the payload: the objective, what done means, the budget, the deadline and the pace. It's the usual approval path, a tap on Telegram or `--yes` at the PC. The approval card is capped at `MAX_BUDGET` (5,000,000) and `MAX_DAYS` (90).
- The owner can also start one directly: `/goal <objective>` from the paired Telegram account opens an owner goal in code with `OWNER_DEFAULT_BUDGET` (1,000,000), 30 days and a daily pace. If the owner asks in plain words instead, Pip files the proposal and the owner taps Approve. A typed "yes" never counts, as always.

**Pip moves goals forward. The owner moves the target.** Pip can log progress, set a goal waiting until a time, and finish it as *done* (with evidence) or *blocked* (with the reason). He can drop his own small goals. He can't edit the objective, budget or deadline of any goal, and he can't drop, pause or resume one the owner set or approved. If he can't continue, he marks it blocked and tells the owner why. For more budget he files a `goal_budget` approval, whose card shows what's been spent, the progress so far and how much more he wants. The owner has `/goal pause|resume|drop|budget N …` on Telegram and the same at the PC. This is OpenClaw's split. It keeps a page that steers Pip from also rewriting what he's working toward.

**One exception, so owner goals work from a one-line `/goal`:** if an owner goal was opened without a definition of done, Pip may fill it in once. It shows in the record like everything else.

**Budget is charged in code, and it's honest.** A wake isn't "for" one goal, so:

- A wake that logs progress on goals is charged to them, split evenly. Its tokens are known when it ends, and `beat()` charges the goals that got progress entries since the wake began.
- A wake that a due goal woke is charged to that goal even if Pip logs nothing, so ignoring a goal still costs it.
- Workers and crew tasks started with `goal: N` charge their tokens to it when they report. Delegating doesn't hide spend.

At 50% and 90% of the budget, code logs a `goal` event with the spend and the last few progress lines: "half your budget for #4 is gone; is this on track?" Pip reads it like an alarm. At 100% the goal moves to `budget_limited`, and nothing more is charged or woken for it until the owner extends it or Pip's `goal_budget` request is approved. Past its deadline a goal becomes `expired`. Neither deletes anything.

**Done needs evidence.** `finish_goal {id, how: "done", evidence}` requires text pointing at the thing itself: a file in the workspace, a URL, a page id, a commit. It goes in the record next to the claim. Code can't check it, but the owner can, and a bare "done" doesn't go in. As OpenClaw puts it, a nearly exhausted budget doesn't make unfinished work done. That line goes in Pip's prompt.

**Limits:** `MAX_OPEN` (8) goals open at once, of which at most `MAX_OWN_OPEN` are Pip's own. A goal's pace is how long it may go untouched before it wakes Pip: 1 hour to 7 days for Pip's own goals, and from the normal 10-minute wake floor up to 7 days for approved and owner goals. The fast end is what lets an approved goal keep going wake after wake, the Hermes loop. It's affordable because the owner approved its budget.

## The goal record

Two tables in `memory.db`, outside the workspace, so Pip's file tools can't touch them, same as approvals.

`goals` holds the id, created, `who` (`own`, `approved` or `owner`), objective, `done_when` and an optional `stop_when`. These are three of Hermes's five contract fields; its constraints and boundaries fold into `stop_when` and the objective text. It also holds the budget and tokens spent, the deadline, pace in minutes, last touched, `waiting_until` and its reason, the status (`active`, `waiting`, `paused`, `blocked`, `budget_limited`, `expired`, `done` or `dropped`), when it ended and how, the evidence, and the approval it came through.

`goal_notes` holds dated progress lines: Pip's own, code's (charges, check-ins, status changes) and the owner's (`/goal note N …`). This is Muse's progress log, and it's what Pip and the owner read to see where a goal stands.

Each goal also gets a folder, `workspace/goals/<id>-<slug>/`, for its working files and final report (Muse's files folder). It's Pip's to write in. The record that counts is in the tables.

## Pip's side

A tool group, `goals`, is always available:

- `start_goal {objective, done_when, stop_when?, budget?, days?, pace_hours?, note?}` opens a small goal or files the approval for a large one, and says which happened.
- `goal_progress {id, note, done_when?}` adds a progress line. It also marks the goal touched, which resets its pace, and makes this wake chargeable to it.
- `goal_wait {id, hours, why}` covers waiting on something outside: DNS, a reply, a release. It sets `waiting_until`, and the goal doesn't wake Pip before then. This is Hermes's WAIT verdict, without the judge.
- `finish_goal {id, how, evidence|reason}`, where `how` is `done`, `blocked` or `dropped` (own goals only).
- `request_goal_budget {id, more, why}` files the `goal_budget` approval.

`start_worker` and `ask_agent` get an optional `goal`.

On the page, a `## Your goals` section sits between the watches and the helpers. Each open goal gets one compact block: id, who set it, objective, done when, spend against budget, deadline, when it's due next or what it's waiting on, and the last three progress lines. The full history is `py -m ahab.goals N` at the PC and Pip's `recall`. Ended goals leave the page and stay in the record.

The heartbeat treats a goal like an alarm. `goals.next_due()` is the last touch plus pace, or `waiting_until`, whichever applies. It joins the alarm and watch times in the sleep loop. When one comes due, `goals.ring_due()` logs a `goal` event at the start of the wake, and its due time moves on by one pace, so an ignored goal rings once per pace rather than every wake.

## The owner's side (the record)

Pip's own goals don't ping anyone. The record is where they show up:

- **The status screen** gets a goals line under the helpers: open goals by short title with spend as a percentage, and *waiting* or *blocked* where that applies.
- **`/goals` on Telegram** lists the open goals and the last few ended ones, with spend, deadline and the latest progress line, written by code from the tables. `/goal N` shows one goal's whole log.
- **The day pages** (`days.py`) already render every event, so goal events appear in each day's story without extra work.
- **`py -m ahab.goals`** at the PC lists them, with `--note`, `--pause`, `--resume`, `--drop` and `--budget`.

Telegram notices from code happen only when the owner has a button to press: the approval cards for a large goal or more budget. Everything else is Pip's to say. If a goal is blocked he should say so, since blocked means he needs the owner, and the prompt tells him that.

## Phases

**Phase 1, goals.** This covers `ahab/goals.py` (the tables, limits, statuses, the due loop, charging, check-ins and the CLI), the `goals` tool group, the page section, the heartbeat's wake for a due goal, charging for wakes, workers and crew tasks, the `goal` and `goal_budget` approval kinds, `/goal` and `/goals` on Telegram, the status-screen line, and prompt lines on what goals are for and the evidence rule. Tests run offline with throwaway homes:

- The small and large limits route correctly, including when the 4th own goal is refused.
- Pip can't edit, drop or resume an owner goal.
- Charging splits correctly, and an ignored due goal still pays.
- The 50/90/100% events fire once each, and `budget_limited` stops the wakes.
- `waiting_until` holds.
- Done without evidence is refused.
- `/goal` from an unpaired chat is ignored.

After that comes one live goal on the laptop: a small one Pip picks, watched on the screen until it finishes or runs out.

**Phase 2, goals own their machinery.** Alarms, intents and watches get an optional `goal`. When a goal ends, code cancels what it owned and logs that, the way Muse removes a goal's scheduled jobs. The nightly dream gets the open goals on its page and flags any that have gone stale or are spending without progress. It also notes goals the owner implied but never set (Muse does this) as suggestions for Pip, who can open a small one or propose a large one. The dream never opens goals itself.

**Phase 3, as wanted.** A weekly goal letter to the owner (Muse's briefings); subgoals one level deep; standing orders for a crew agent tied to a goal.

## Decided, and still open

Decided with the owner: Pip opens small goals on his own, and they show in the record (the status screen, `/goals`, the day pages) without a ping. Large goals need the owner's approval by default.

Settled here, adjustable once there's data: the numbers (200k and 14 days for small goals, 5M and 90 days at most), no judge model, budget counted as tokens in plus out, and code-written notices only where there's a button.

Open, to settle while building:

- Whether cached input tokens should count at a discount. Codex reports none cached today, so it doesn't matter yet. If an API backend starts caching, a goal's spend should probably follow what the owner actually pays.
- Whether "small" should become a setting in `home.json` (`goals.own_budget`), folding into permission tiers when those arrive, the way `agent_approval` will. It's cheap, so probably yes.
- Whether a done owner goal should ask the owner to confirm before it closes, OpenClaw-style. Starting without: the evidence line plus Pip's message should be enough, and `/goal resume N` reopens it.
