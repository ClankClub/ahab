# Templates

Starter versions of the files that make each agent who it is. A new install has none of them, so on every wake-up the heartbeat checks the agent's workspace and copies in any that are missing. It never overwrites one that exists: from then on the copy in `home/workspace/` belongs to the agent and its owner, and these templates only matter for the next new home.

| File | What it's for | Who edits the copy |
|---|---|---|
| `SOUL.md` | Who the agent is: where it comes from, its character, how it talks. | The agent, as it figures itself out (it tells the owner when it does), and the owner. |
| `USER.md` | What the agent knows about its owner: basics, how they like to work, what's going on, things to remember. | The agent, as it learns, and the owner, to correct it. |
| `PROACTIVE_PREFERENCES.md` | What the owner wants to hear about without asking, what to leave alone, when, and how. Unlike Muse's blank version, it starts with working defaults, because the agent's own judgment on each wake-up is the only thing deciding when to reach out. | The owner. The agent writes down preferences the owner tells it, in their words, and never adds its own guesses. |
| `MEMORY.md` | What the agent remembers: facts, decisions, projects and what happened, dated, with the detail in `memory/` (people, opinions, the world, a note per day). | The agent, as it learns and in its dream. |
| `FEEDBACK.md` | What the agent tells its builders: a tool that failed, an ability it's missing, a limit that pinched, an idea. Not shown on the page; the builders read it, and the weekly memory report says when it's grown. | The agent, and the builders, who mark entries answered or fixed. |
| `AGENTS.md` | The agent's own operating manual: conventions it settled on, tool and site quirks, mistakes not to repeat. Not the rules it works by (those are in the code) and not a full how-to (that's a skill). Starts empty, like Muse's. | The agent, mostly in its daily dream. |

All but FEEDBACK.md are shown at the top of every wake-up page, capped at 8,000 characters each (AGENTS.md at 10,000, MEMORY.md at 20,000). The rules the agent works by (who may give it orders, its tools, the reply format) are in the code, not here, so no file in the workspace can talk it out of them.

**Naming note:** The software/repository is now called **Ahab**; the runtime's current default name and identifiers still say `Perro` until the code itself is migrated. Existing agents, including Pip, retain their own names.

`{name}` is replaced with the agent's name from `home/home.json` (Perro by default) when the file is copied. Italic lines tell the agent what each part is for; it's free to rewrite them along with the rest.

To start an agent's file over, delete its copy in `home/workspace/`; the next wake-up puts a fresh starter in its place.
