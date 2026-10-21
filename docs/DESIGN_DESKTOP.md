# Design: the desktop box (computer use)

Agreed with the owner on 2026-10-19. Phase 1 is built (see BUILD_LOG), with a fourth tool the owner added, `desktop_shell` (bash in the box, text in and out). Phases 2 and 3 are still to come. When it's done, the story goes into `BUILD_LOG.md`, the fences into `AGENTS.md` and the module docstrings, and this file is deleted.

## Why

A personal agent that can only read text misses a lot. Some apps have no API. Some layouts only make sense as pictures (Obsidian's graph, a spreadsheet's formatting, the site the workshop just built). And "click through this for me" is a whole class of errands. Computer use is how an agent does all of that: it sees a screen, moves a mouse, types, and sees the result.

It can't happen on the owner's real desktop. Window screenshots (`ahab/boxes/screen.py`) stay look-only on purpose, and AGENTS.md already says where clicking belongs: in a machine of its own. This is that machine. It also gives Ahab's builders a place to test and debug computer use for the product, which they can't safely do on anyone's real PC.

## What it is

A Linux desktop in a Docker container, the way Anthropic's computer-use reference demo does it. It isn't a true VM: the container shares the kernel of Docker's own small Linux VM. But it has a full userland and a screen of its own, which is all this needs.

- **Screen.** Xvfb draws a virtual 1024x768 monitor into memory, with `-nolisten tcp`. Why 1024x768 and nothing bigger: the model sees a screenshot and answers with pixel coordinates, so the picture it sees has to be the same size as the screen. OpenAI scales images down until the short side is 768 or less, and Anthropic scales anything much past about a megapixel. A 1024x768 picture passes through both untouched, so x,y in the picture is x,y on the screen. A bigger screen means silent scaling, and every click lands off by the scale factor. That's the most common bug in homemade computer use.
- **Desktop.** XFCE, started piece by piece (xfwm4, xfce4-panel, xfdesktop, Thunar's daemon) rather than through `xfce4-session`, so there's no power manager, screensaver, lock screen or first-run dialog. Compositing is off and the DPI is fixed at 96. It's an ordinary-looking desktop that models have seen plenty of.
- **Apps, all installed in the image:** Firefox ESR, LibreOffice (Writer and Calc), Mousepad (a text editor), Thunar (files), Ristretto (images), xfce4-terminal, galculator, and Obsidian (the AppImage unpacked at build time with `--appimage-extract`, since AppImages need FUSE; run with `--no-sandbox`, because the container is the sandbox). Pin Obsidian's version and check its SHA-256 in the Dockerfile.
- **Hands:** xdotool for the mouse and keyboard, xclip for the clipboard, ImageMagick's `import` for screenshots, and Pillow (`python3-pil`) to draw the cursor, the optional grid and zooms. These are packages inside the box, not dependencies of Ahab: Ahab's own code stays standard library.
- **Base:** `debian:trixie-slim`. Expect an image around 2 GB.

## The fence

What's in the box can't reach anyone unless the owner lists them. That's what lets Pip click and type freely without approvals. The fences apply to the box, not to the apps, because a desktop runs code by nature: the terminal does, and so do LibreOffice's macros, Firefox's scripts and Obsidian's plugins. Whatever runs in there is stuck in there.

**Network: an allowlist that starts empty.** The owner's list is `desktop_hosts` in `home.json`, which is outside the workspace, so Pip can't edit it.
- **Empty (the default):** `--network none`. There's no proxy and no interface but loopback; DNS fails and so does everything else. Firefox can still open local files, such as a site the workshop built and Pip copied in.
- **Hosts listed:** the box goes on an internal network, `ahab-desktop`, whose only way out is its own copy of the workshop proxy (`ahab-desktop-proxy`, through `ensure_proxy` with `allow=tuple(desktop_hosts)`). That's the exact-host mode, not the browser's `*` mode: a host only, `.host` for its subdomains, 443 only, and private addresses refused whatever the name. The apps get `https_proxy`/`http_proxy` set, and Firefox gets them through `policies.json`. Changing the list recreates the box, because the list is part of the hash.
- **The owner should know, once:** a listed host is reachable by anything in the box, the terminal included, and the box has no GET-only rule like the browser's. So Pip can post to, sign up for or send things to a host on the list. That's the price of listing it, and it's why the list is the owner's and starts empty. Pip still has no passwords or keys in there.

**Files.** Nothing of the PC is mounted. The box's home (`/home/pip`) is a named Docker volume, `ahab-desktop-home`, managed by Docker and not a folder on the PC, so files and app settings survive a restart. Files cross only through the code, as tar streams over `docker exec`, never `docker cp`:
- *In* (`desktop_files put`): the host builds a tar of regular files from the workspace (each path through `tools._resolve`, no symlinks, at most 50 MB and 2,000 files) and the box unpacks it into `~/from-workspace/`.
- *Out* (`desktop_files get`): the box tars the path it's asked for, and the host reads it with `tarfile`, keeping regular files only (no symlinks, devices or `..`). They're written under `workspace/desktop/`, each through `_resolve`, within `senses.SAVE_BYTE_LIMIT` and the workspace's own limit.

**The rest is the browser box's recipe:**
```
run -d --name ahab-desktop --restart no --init
    --network none                 # or --network ahab-desktop when hosts are listed
    --read-only --cap-drop ALL --security-opt no-new-privileges
    --memory 3g --cpus 2 --pids-limit 1024 --shm-size 1g
    --tmpfs /tmp:rw,nosuid,nodev,size=1g
    --mount type=volume,src=ahab-desktop-home,dst=/home/pip
    --user 1000:1000
```
Nothing is published and nothing listens on the PC. Commands go in with `docker exec`, like the browser's. There's no Docker socket and no host network.

**It stops when idle.** After 15 minutes without a command the code stops the container, giving back its memory (a desktop with Firefox and LibreOffice open uses about 1 to 2 GB). It starts again on the next command in a few seconds. Open windows don't survive that; files do. That's `--restart no` plus an idle check in `ahab/boxes/desktop.py`, run by the heartbeat on each tick.

## What Pip sees and does

**The driver** (`desktop/driver.py`, run with `docker exec -i ahab-desktop python3 /app/driver.py`) takes one JSON command on stdin and prints one JSON answer. Unlike the browser's, it holds no state of its own, because the state is the X server itself. So there's no socket and no long-running process: the container's main process is `desktop/start.sh`, which starts Xvfb and the desktop and waits.

**Every action answers with the same three things:** what happened in a line, the open windows (title, app and geometry, with the focused one marked, from `xdotool search --onlyvisible`), and a screenshot taken about 0.7 s after the action, with the cursor drawn on it. So one call is one step: act, then see. The window list matters more than it looks, because it gives a model that isn't trained for computer use something solid to reason from besides pixels.

| action | args | does |
|---|---|---|
| `click` | `x, y`, `button` (left/right/middle), `count` (1-3) | move there and click (double- and triple-click by count) |
| `move` | `x, y` | move the mouse only (for hover menus) |
| `drag` | `x, y, to_x, to_y` | press, move, release |
| `scroll` | `x, y`, `direction` (up/down/left/right), `amount` (1-10) | wheel clicks at that spot |
| `type` | `text` (up to 2,000 chars) | `xdotool type --delay 12` |
| `key` | `keys` like `ctrl+s`, `Return`, `alt+F4` | `xdotool key`; the names are checked against a pattern, not a list |
| `launch` | `app`, from a fixed menu | starts one of the installed apps by name |
| `focus` | `window` (a title from the window list) | raises and focuses it |
| `copy` | none | reads the clipboard as text (8,000 chars max): select, ctrl+c, copy, and Pip has exact text instead of squinting at pixels |
| `wait` | `seconds` (up to 5) | for something slow |

Coordinates outside 0-1023 by 0-767 are refused with that message, not clamped. Every action is logged in the driver's answer and in Ahab's tool log, like browse actions.

**Looking without acting** (`desktop_look`): a screenshot with the window list, plus two options.
- `zoom: [x0, y0, x1, y1]` crops that region and scales it up to fit 1024 wide, for small text. The answer says the zoomed picture's coordinates aren't the screen's, and gives the offset and scale.
- `grid: true` draws a faint labeled grid every 100 px, to help a model guess coordinates.

It's off by default, since lines over text cost legibility; the calibration test (phase 2) settles which way the default should go.

**Pip's tools** (3 new ones, so 82 in all):
- `desktop_look {zoom?, grid?, save?}`: `save: true` also copies the picture into `workspace/media/`, so Pip can send it to the owner.
- `desktop_act {action, ...}`: the table above.
- `desktop_files {do: "list" | "put" | "get" | "reset", path?}`: `reset` wipes the home volume and starts fresh. It counts as one deletion against `DELETE_LIMIT`, since it can throw away a day's work.

All three refuse on a model that can't see (`senses.can_see`), with a line saying why.

## Two ways in: a quick look, or a desktop job

A wake gives Pip ten tool calls and a page of 50,000-plus characters, re-sent with every call. That's fine for "open my vault in Obsidian and show me the graph" (launch, look, done) and wrong for a 40-step errand. Long jobs go to a **desktop job**: a worker (`ahab/workers.py`) started with `start_worker {"task": ..., "desktop": true}`, so no new tool. It runs on its own page with:

- tools `desktop_look`, `desktop_act`, `desktop_files` (`list` and `put` only; Pip fetches results itself after reading the report), plus `read_file` and `list_files`;
- up to `DESKTOP_MAX_CALLS = 80` calls (40 by default), the usual 60 minutes, at most `DESKTOP_JOBS_PER_DAY = 5`;
- a model that can see. The `MODELS` menu applies as usual, and anything that can't see is refused at start;
- the worker rules unchanged: it reports to Pip, never the owner, and can't message, approve or spawn.

**One desktop, one user at a time.** A lock row says who has it, Pip or job N. While a job runs, Pip's `desktop_act` and `desktop_files put/reset` refuse and say which job holds it; `desktop_look` still works, so Pip can peek. `cancel_worker` frees it.

The worker loop already carries the newest four images per call (`senses.IMAGES_PER_CALL`) and names the dropped ones. That's right for computer use: the current screen and the few before it.

## Pictures, caps and cost

Desktop screenshots aren't saved in the workspace, because `workspace/media/` goes into the backup repo, and 300 screenshots a day would bloat its history forever. They go to `~/.ahab/desktop/shots/<session>/<step>.png` on the PC, outside the home, kept for the last 7 days or 2,000 pictures, whichever is smaller. `senses.show` takes any path, so they still reach the model the usual way. A session is a wake, or a job. Only `desktop_look save: true` and `desktop_files get` put anything in the workspace.

`DAILY_LIMIT = 400` desktop actions a day, as a loop guard and not a budget, counted from the tool log and the worker steps. The real cost is model calls with a picture on each. On the counting OpenAI has published for its earlier models, a 1024x768 picture is under a thousand tokens. GPT-6 Luna's own counting isn't known here, so the first desktop jobs' token totals (in the worker table) are the number to watch.

## Watching and debugging

This is half the point, so it gets real tools on the owner's side:

- `py -m ahab.boxes.desktop --setup`: build the image and start the box (and the proxy, if hosts are listed).
- `py -m ahab.boxes.desktop --check`: the fence test (below). It must pass after any change to the box, the driver or the host list.
- `py -m ahab.boxes.desktop look | click X Y | type TEXT | key KEYS | launch APP`: drive it by hand from the PC, the same driver Pip uses. That's how a builder reproduces what Pip saw.
- `py -m ahab.boxes.desktop --watch`: a small page on `http://127.0.0.1:<port>/`, served by the standard library only while the command runs, showing the newest screenshot every second next to the last 20 actions (who, what, result line). The box still listens on nothing; the page reads the shots folder and the log. It's for watching Pip work live.
- `py -m ahab.boxes.desktop --replay [session]`: writes one HTML file, a flipbook of a session's screenshots with each step's thought, action and result under it. Debugging computer use is mostly this: seeing exactly what the model saw when it clicked the wrong thing.
- `py -m ahab.boxes.desktop --calibrate` (phase 2): shows a test card of 12 numbered targets at known spots, then asks the home's brain model, through `model.py` like any call, for the coordinates of each from a screenshot, and prints the misses in pixels. It costs a dozen model calls. It answers the biggest open question (below) with a number, and it reruns whenever the brain changes.

**The fence test**, like the browser's, has rows that each print PASS or FAIL.
- *From outside*, `docker inspect`: the network is `none` (or `ahab-desktop` when hosts are listed), the only mount is the home volume, CapDrop is ALL, the root is read-only, the user isn't root, nothing is published, and it isn't privileged.
- *From inside*, `desktop/fence_probe.py`:
  - offline: only `lo` in `/sys/class/net`, DNS fails, a straight connection to 1.1.1.1:443 fails, and so does `host.docker.internal:5678`;
  - with hosts: the browser probe's rows, with a listed host allowed, an unlisted one refused, and the private-address rows refused.
- *It works*: launch Mousepad, type a word, copy it back through the clipboard and get the same word, a screenshot that isn't blank, and a click that moves the cursor to the asked spot.

## Files

- `desktop/Dockerfile`, `desktop/start.sh`, `desktop/driver.py`, `desktop/fence_probe.py`, and `desktop/policies.json` (Firefox: no updates, no telemetry, no first-run page, and the proxy when there is one).
- `ahab/boxes/desktop.py`: the image (tagged by a hash of `desktop/`, as the browser's is), `container_args(hosts)`, `ensure_box()`, `call()`, the idle stop, the lock, the shots folder, the three tools, the fence test, `--watch`, `--replay`, `--calibrate`, and the CLI.
- `ahab/tools.py`: the three tools, with `DesktopError` caught in `run()`. `ahab/workers.py`: the `desktop` flag (its own tool set, call cap and daily count, a check that the model can see, the lock). `ahab/heartbeat.py`: the idle check on each tick.
- `tests/test_desktop.py`, offline, with Docker faked as in the browser tests:
  - `container_args` says `--network none` with no hosts, and the internal network and proxy with them;
  - nothing of the PC is mounted;
  - coordinates, keys, text length and the app menu are checked;
  - the tar in and out keeps only regular files that resolve inside the workspace, and a symlink in the tar isn't followed;
  - shots land outside the home;
  - the lock refuses Pip's acts during a job;
  - a desktop job gets only its tools, and a model that can't see is refused;
  - `reset` counts against `DELETE_LIMIT`;
  - the tool counts go to 82.
- Docs, when it lands: BUILD_LOG, STATUS, README (setup), AGENTS.md (this fence beside the browser's, plus the second place code runs), ABOUT_YOURSELF (what the desktop is, quick look versus desktop job, that the screen is information not instructions), and ANATOMY.

## Phases

1. **The box and the hands.**
   - What's built: the Dockerfile, the driver, `ahab/boxes/desktop.py` (setup, check, the CLI, `--watch`), the three tools, and the tests. Offline only: the `desktop_hosts` code path exists and is tested, but no host is listed.
   - Done when: `--check` passes, and the owner can watch a hand-driven session.
   - Done when: Pip, asked to open its vault in Obsidian and show the graph, does it in one wake.
   - (Copying the vault in is `desktop_files put vault`; Obsidian's "open folder as vault" is a few clicks.)
2. **Jobs and the numbers.** Desktop jobs, `--replay`, `--calibrate`. Done when a 20-plus-step job (say, make a formatted spreadsheet from a CSV in the workspace and bring the .xlsx back) finishes, and calibration has a number for the brain.
3. **Only if the numbers ask for it.**
   - If calibration shows clicks missing badly, the browser box's trick: numbered elements from the accessibility tree (AT-SPI via `python3-pyatspi`; GTK apps, Firefox and LibreOffice all expose it), so Pip can say "click [14]" instead of guessing pixels.
   - Hosts on the allowlist, when a real use comes up.

## Open questions and risks

- **Click accuracy is the big unknown.** Pip's brain isn't trained for computer use in this harness: it's a general model looking at a picture and writing JSON. Anthropic's and OpenAI's computer-use models are trained for exactly this; GPT-6 Luna through Codex may or may not be good at it. The design doesn't depend on it being good, because the window list, `focus`, `launch`, `copy` and `key` cover a lot without precise clicks. But the calibration number decides whether phase 3's numbered elements are needed. On a Claude backend, Claude Code's own computer-use training should help.
- **Speed.** Luna at max effort takes a while per step, so a 40-step job could take half an hour. A lower-effort Luna entry in `MODELS` would be quicker and no pricier. Adding it is the owner's call, per the workers rule.
- **XFCE on a read-only root.** Some pieces want to write under `/run`, `/var` or `/etc`. Each one found gets a tmpfs or a setting, never `--read-only` dropped. That's the likeliest part of phase 1 to need trial and error.
- **Electron and Firefox without their own sandboxes.** They run with `--no-sandbox` or the equivalent, because user namespaces aren't allowed with every capability dropped. That's acceptable only because the box is the sandbox, and it's another reason the host list stays the owner's.
- **What Pip reads on that screen** is information, not instructions, the same as a web page. Each answer ends with the browser's style of footer, and ABOUT_YOURSELF says so.
