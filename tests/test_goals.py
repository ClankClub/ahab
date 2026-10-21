"""Goals (ahab.goals): sizes, who may change what, budgets charged in code, waits, Telegram and the screen."""
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, ROOT)
from ahab import approvals, console, goals, telegram, tools, workers
from ahab.memory import Memory

UTC = timezone.utc
home = Path(tempfile.mkdtemp())
m = Memory(home)


def iso(**delta):
    return (datetime.now(UTC) + timedelta(**delta)).replace(microsecond=0).isoformat()


def set_(goal_id, **cols):
    for col, value in cols.items():
        m.db.execute(f"UPDATE goals SET {col} = ? WHERE id = ?", (value, goal_id))
    m.db.commit()


def events(kind="goal"):
    return [e.content for e in m.recent(50) if e.kind == kind]


def run(name, **args):
    return tools.run(m, name, args)


# --- small goals open at once; the fourth of Ahab's own is refused --------------------
out = run("start_goal", objective="Tidy the vault's inbox folder", done_when="inbox/ holds no notes older than a week")
assert out.startswith("Opened goal #1") and "200k tokens" in out, out
g = goals.get(m, 1)
assert g.who == "own" and g.budget == goals.OWN_BUDGET and g.status == "active"
assert (home / "workspace" / goals.folder(1, g.objective)).is_dir()
assert "#1 (yours) Tidy the vault's inbox folder" in goals.describe(m)
assert "done means: inbox/ holds no notes" in goals.describe(m)
assert run("start_goal", objective="Second", done_when="x", budget="50k").startswith("Opened goal #2")
assert run("start_goal", objective="Third", done_when="x", pace_hours=2).startswith("Opened goal #3")
out = run("start_goal", objective="Fourth", done_when="x")
assert "refused" in out and "3 goals of your own" in out, out
assert "refused" in run("start_goal", objective="Fast", done_when="x", pace_hours=0.5)   # own goals: hourly at most
assert "refused" in run("start_goal", objective="No done", done_when="")
# An open goal keeps its tools in full on the tool list.
assert "- goal_progress:" in tools.describe(m)

# --- a big goal goes to the owner as an approval card, written by code -------------------
run("finish_goal", id=3, how="dropped", reason="Folded into #1.")
out = run("start_goal", objective="Build the recipe site", done_when="live preview URL that loads",
          budget="2M", days=30, pace_hours=0.5, note="It's the one they keep asking about")
assert "filed as #" in out and "Nothing is open yet" in out, out
req = approvals.pending(m)[-1]
assert req.kind == "goal" and "Budget: 2M tokens. Time: 30 days." in req.card and "30 min untouched" in req.card, req.card
assert len(goals.open_goals(m)) == 2, [(g.id, g.objective, g.status) for g in goals.open_goals(m)]
approvals.decide(m, req.id, True, "test")
approvals.run_decided(m)
assert "opened goal #4" in events("approval")[-1], events("approval")
big = goals.get(m, 4)
assert big.who == "approved" and big.budget == 2_000_000 and big.pace == 30 and big.approval == req.id

# --- Ahab moves goals forward; only the owner moves the target -----------------------------
assert "refused" in run("finish_goal", id=4, how="dropped", reason="meh")          # not Ahab's to drop
assert "refused" in run("goal_progress", id=1, note="x", done_when="something else")  # done_when is set
assert "refused" in run("finish_goal", id=1, how="done")                            # done needs evidence
assert "refused" in run("finish_goal", id=1, how="done", evidence="  ")

# --- charging: touched goals share a wake; a goal that woke Ahab pays even if ignored ----------
time.sleep(1.1)                              # a new wake, after the ones that opened them
wake_start = iso()
run("goal_progress", id=1, note="Filed 12 notes.")
run("goal_progress", id=2, note="Read the brief.")
goals.charge_wake(m, wake_start, 10_000)
assert goals.get(m, 1).spent == 5_000 and goals.get(m, 2).spent == 5_000 and goals.get(m, 4).spent == 0
time.sleep(1.1)
set_(4, due=iso(minutes=-1))
assert goals.due_now(m)
wake_start = iso()
assert goals.ring_due(m) == 1
assert "Goal #4 is due" in events()[-1] and "No progress logged yet" in events()[-1], events()[-1]
assert not goals.due_now(m)                        # it moved on by one pace
assert goals.next_due(m) is not None
goals.charge_wake(m, wake_start, 8_000)
assert goals.get(m, 4).spent == 8_000 and goals.get(m, 1).spent == 5_000

# --- check-ins at 50% and 90%, once each; at 100% it stops waking Ahab -----------------------
goals.charge(m, 2, 20_000, "test")          # 25k of 50k: 50%
assert "used 50% of its budget" in events()[-1], events()[-1]
goals.charge(m, 2, 1_000, "test")
assert "used 50%" in events()[-1] and len([e for e in events() if "Goal #2" in e and "%" in e]) == 1
goals.charge(m, 2, 20_000, "test")          # 46k: 92%
assert "used 92% of its budget" in events()[-1], events()[-1]
goals.charge(m, 2, 10_000, "test")          # 56k: over
assert goals.get(m, 2).status == "budget_limited" and "won't wake you again" in events()[-1]
set_(2, due=iso(minutes=-5))
assert goals.ring_due(m) == 0               # a used-up goal doesn't ring
assert "budget used up" in goals.describe(m)

# --- waiting: no wake before the time; then it rings and says the wait is over ----------------
assert "waits until" in run("goal_wait", id=1, hours=6, why="the vault sync runs tonight")
assert goals.get(m, 1).waiting and "waiting until" in goals.describe(m)
set_(1, due=iso(minutes=-1), waiting_until=iso(minutes=-1))
assert goals.ring_due(m) >= 1 and "the wait you set is over (the vault sync runs tonight)" in "".join(events())
assert not goals.get(m, 1).waiting
assert "refused" in run("goal_wait", id=2, hours=1, why="x")      # only an active goal waits

# --- helpers started for a goal charge it when they report ----------------------------------
workers._table(m)
assert "refused" in run("start_worker", task="Read every page about recipe sites and report back.", goal=2)
m.db.execute("INSERT INTO workers (created, task, model, max_calls, status, tokens_in, tokens_out) "
             "VALUES (?, 'x', 'luna', 5, 'done', 3000, 1000)", (iso(),))
m.db.commit()
goals.link(m, 4, "worker", tools._newest(m, "workers"))
before = goals.get(m, 4).spent
goals.charge_helpers(m)
goals.charge_helpers(m)                      # once only
assert goals.get(m, 4).spent == before + 4_000

# --- blocked, done with evidence, expiry --------------------------------------------------
assert "Only your owner can resume it" in run("finish_goal", id=4, how="blocked", reason="Need the domain login.")
assert "refused" in run("start_worker", task="Read every page about recipe sites and report back.", goal=5)
out = run("finish_goal", id=1, how="done", evidence="workspace/goals/1-tidy/report.md")
assert "is done" in out and goals.get(m, 1).evidence.startswith("workspace/goals")
set_(4, deadline=iso(minutes=-1))
goals.ring_due(m)
assert goals.get(m, 4).status == "expired" and "expired: its deadline passed" in events()[-1]

# --- the owner: /goal on Telegram, from the paired chat only ---------------------------------
state = {"owner_user_id": 111, "owner_chat_id": 111}


class Bot:
    def __init__(self):
        self.sent = []

    def send(self, chat_id, text):
        self.sent.append(text)


def tg(text, who=111, chat=111):
    bot = Bot()
    telegram.handle_update({"update_id": 1, "message": {"chat": {"id": chat, "type": "private"},
                                                        "from": {"id": who}, "text": text}}, state, m, bot)
    return bot.sent


assert tg("/goal plan the trip to Lisbon", who=999) == []          # a stranger: nothing
assert len(goals.open_goals(m)) == 1                               # still just #2
reply = tg("/goal plan the trip to Lisbon")[0]
assert reply.startswith("Opened goal #5") and "1M tokens" in reply, reply
g6 = goals.get(m, 5)
assert g6.who == "owner" and g6.done_when == "" and goals.due_now(m)
assert goals.ring_due(m) == 1 and "your owner just set it" in events()[-1]
# Ahab fills in what done means once; it can't drop or reword the owner's goal.
assert "wrote down what done means" in run("goal_progress", id=5, note="Asked about dates.",
                                           done_when="flights and a place booked, plan in workspace/goals/5")
assert "refused" in run("finish_goal", id=5, how="dropped", reason="x")
assert "Open goals:" in tg("/goals")[0] and "#5 plan the trip" in tg("/goals")[0]
assert "Done means: flights and a place booked" in tg("/goal 5")[0]
assert tg("/goal pause 5")[0] == "Paused #5." and goals.get(m, 5).status == "paused"
assert "Your owner paused goal #5" in events()[-1]
assert "refused" in run("start_worker", task="Read every page about Lisbon flights and report back.", goal=6)
assert tg("/goal resume 5")[0].startswith("Resumed #5") and goals.due_now(m)
assert tg("/goal budget 5 3M")[0] == "#5's budget is now 3M." and goals.get(m, 5).budget == 3_000_000
assert "isn't a number of tokens" in tg("/goal budget 5 lots")[0]
assert tg("/goal note 5 window seat")[0] == "Noted on #5." and "window seat" in events()[-1]
# A used-up goal comes back when the owner resumes it.
reply = tg("/goal resume 2")[0]
assert reply.startswith("Resumed #2") and "budget was used up" in reply, reply
assert goals.get(m, 2).status == "active" and goals.get(m, 2).budget > goals.get(m, 2).spent
assert tg("/goal 99")[0] == "There's no goal #99."
assert goals.telegram_command(m, "hello there") is None and goals.telegram_command(m, "/goalie") is None

# --- more budget, through an approval card that states the record's facts -------------------
out = run("request_goal_budget", id=5, more="1M", days=10, why="Hotels take longer than flights.")
assert "Filed #" in out, out
req = approvals.pending(m)[-1]
assert req.kind == "goal_budget" and "1M more tokens and 10 more days" in req.card and "plan the trip" in req.card
approvals.decide(m, req.id, True, "test")
approvals.run_decided(m)
assert goals.get(m, 5).budget == 4_000_000
assert "refused" in run("request_goal_budget", id=5, why="no amount")

# --- tokens, the status screen, the command line's view ---------------------------------
assert goals.tokens("2M") == 2_000_000 and goals.tokens("500k") == 500_000 and goals.tokens("1,500") == 1_500
assert goals.compact(1_500_000) == "1.5M" and goals.compact(200_000) == "200k"
line = console.goals_line(home, time.time(), True, lambda code, s: s, 80)
assert line and line[0].startswith("goals #2") and "#5 plan the trip to Lisbon" in line[0], line
hidden = console.goals_line(home, time.time(), False, lambda code, s: s, 80)
assert "Lisbon" not in hidden[0] and "#5 " in hidden[0], hidden
assert all(len(l) <= 40 for l in console.goals_line(home, time.time(), True, lambda code, s: s, 40))
frame = console.frame(home, 80, 30, color=False)
assert any(l.startswith("goals ") for l in frame)
assert "Lately ended:" in goals.summary(m) and "#1 Tidy" in goals.summary(m)
assert "Filed 12 notes." in goals.detail(m, 1) and "Evidence: workspace/goals" in goals.detail(m, 1)

m.close()

# --- a whole wake: the owner's goal rings, is on the page, and the wake's tokens land on it ----------
import json
from ahab import heartbeat
from ahab.model import Reply

home2 = Path(tempfile.mkdtemp())
m = Memory(home2)
goals.owner_start(m, "Find a dentist near the new flat")


class Fake:
    def __init__(self, *replies):
        self.replies, self.seen = list(replies), []

    def complete(self, messages, temperature=0.7, json_mode=False):
        self.seen.append([dict(x) for x in messages])
        return Reply(json.dumps(self.replies.pop(0)), "fake", 100, 10)


time.sleep(1.1)
f = Fake({"thought": "On it.", "tool": "goal_progress",
          "args": {"id": 1, "note": "Listed three clinics.", "done_when": "an appointment booked"}},
         {"thought": "Done for now.", "next_wake_minutes": 600})
heartbeat.beat(m, f, heartbeat.system_prompt("Pip"))
page = f.seen[0][1]["content"]
assert "## Your goals\n#1 (your owner) Find a dentist" in page, page[-1500:]
assert "- goal_progress:" in f.seen[0][0]["content"]                       # its tools in full
assert "Goal #1 is due: your owner just set it" in page
g = goals.get(m, 1)
assert g.spent == 220 and g.done_when == "an appointment booked", (g.spent, g.done_when)
assert not goals.due_now(m) and goals.next_due(m) > time.time() + 23 * 3600
m.close()
print("goals ok")
