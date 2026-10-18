"""Run Perro unattended on a Mac, the launchd equivalent of the Windows tasks.

launchd is macOS's built-in service manager. A "LaunchAgent" is a small
.plist file in ~/Library/LaunchAgents describing a program to run as you;
launchd starts it at login and (here) restarts it if it crashes.

  python3 scripts/install_mac.py                 install the heartbeat, and the
                                                 Telegram bridge and the watchdog
                                                 if the bridge is paired
  python3 scripts/install_mac.py --with-backup   also back up home/ every 6 hours
                                                 (needs home/ to be a Git repo
                                                 with a remote; see README)
  python3 scripts/install_mac.py --remove        uninstall them all
  python3 scripts/install_mac.py --dry-run       print the .plist files, change nothing

Run it from a terminal where Perro already works: launchd doesn't read your
shell's startup files, so the keys and settings Perro needs (and your PATH,
so it can find codex) are copied from that terminal into the plists. They're
stored as plain text, readable only by you, like `setx` on Windows.
Output goes to home/heartbeat.log, home/telegram.log, home/watchdog.log and
home/backup.log.
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
AGENTS = Path.home() / "Library" / "LaunchAgents"
HEARTBEAT = "perro.heartbeat"
TELEGRAM = "perro.telegram"
BACKUP = "perro.backup"
WATCHDOG = "perro.watchdog"
# What Perro reads from the environment; whichever are set get copied.
PASSED_ON = ("DEEPSEEK_API_KEY", "OPENROUTER_API_KEY", "PERRO_TELEGRAM_TOKEN", "PERRO_N8N_KEY",
             "PERRO_BACKEND", "PERRO_CLI_HOME", "PERRO_CODEX_BIN", "PERRO_WORKSHOP_HOME")


def environment() -> dict[str, str]:
    env = {name: os.environ[name] for name in PASSED_ON if os.environ.get(name)}
    # Git usually lives in /usr/bin (Xcode tools) or /opt/homebrew/bin; codex wherever it was installed.
    env["PATH"] = os.environ.get("PATH") or "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
    return env


def plist(label: str, args: list[str], extra: dict) -> bytes:
    home = REPO / "home"
    return plistlib.dumps({
        "Label": label,
        "ProgramArguments": [sys.executable, "-X", "utf8", "-m", *args],
        "WorkingDirectory": str(REPO),
        "EnvironmentVariables": environment(),
        # Anything that dies before Python can open its own log lands here.
        "StandardOutPath": str(home / "launchd.log"),
        "StandardErrorPath": str(home / "launchd.log"),
        **extra,
    })


def heartbeat_plist() -> bytes:
    return plist(HEARTBEAT, ["perro.heartbeat", "--log", "--scheduled"], {
        "RunAtLoad": True,                    # start at login
        "KeepAlive": {"Crashed": True},       # restart after a crash, not after a clean exit
    })


def telegram_plist() -> bytes:
    return plist(TELEGRAM, ["perro.telegram", "--log"], {
        "RunAtLoad": True,
        "KeepAlive": {"Crashed": True},
    })


def watchdog_plist() -> bytes:
    return plist(WATCHDOG, ["perro.watchdog", "--log"], {
        "StartInterval": 10 * 60,             # every 10 minutes; checks the others and n8n
    })


def backup_plist() -> bytes:
    return plist(BACKUP, ["perro.backup", "--log"], {
        "StartInterval": 6 * 60 * 60,         # every 6 hours; missed runs catch up after sleep
    })


def launchctl(*args: str) -> None:
    subprocess.run(["launchctl", *args], check=False, capture_output=True)


def install(label: str, data: bytes) -> None:
    path = AGENTS / f"{label}.plist"
    launchctl("bootout", f"gui/{os.getuid()}", str(path))   # if already loaded
    AGENTS.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    # Created readable only by you, since it holds keys.
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as f:
        f.write(data)
    launchctl("bootstrap", f"gui/{os.getuid()}", str(path))
    print(f"Installed {label} ({path}).")


def remove(label: str) -> None:
    path = AGENTS / f"{label}.plist"
    if path.exists():
        launchctl("bootout", f"gui/{os.getuid()}", str(path))
        path.unlink()
        print(f"Removed {label}.")


def running_by_hand() -> bool:
    """Is a heartbeat already running for this home that launchd didn't start? Then the
    background one would find the lock taken and quietly stop."""
    if (AGENTS / f"{HEARTBEAT}.plist").exists():
        return False                          # ours: installing again replaces it
    sys.path.insert(0, str(REPO))
    from perro.heartbeat import acquire_lock
    lock = acquire_lock(REPO / "home")
    if lock is None:
        return True
    lock.close()
    return False


def backend() -> str:
    """The mind this home runs on: PERRO_BACKEND, else home.json, else DeepSeek."""
    try:
        chosen = json.loads((REPO / "home" / "home.json").read_text(encoding="utf-8")).get("backend")
    except (OSError, ValueError, AttributeError):
        chosen = None
    return os.environ.get("PERRO_BACKEND") or chosen or "deepseek"


def main() -> None:
    parser = argparse.ArgumentParser(description="Install Perro's launchd agents (macOS).")
    parser.add_argument("--with-backup", action="store_true")
    parser.add_argument("--remove", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if sys.version_info < (3, 11):
        sys.exit(f"Perro needs Python 3.11 or newer; this is {sys.version.split()[0]}. "
                 "Install one from python.org or with `brew install python`.")
    if args.remove:
        for label in (HEARTBEAT, TELEGRAM, WATCHDOG, BACKUP):
            remove(label)
        return

    mind = backend()
    if mind == "deepseek" and not os.environ.get("DEEPSEEK_API_KEY") and not args.dry_run:
        sys.exit("Perro runs on DeepSeek here, and DEEPSEEK_API_KEY isn't set in this terminal. Set it "
                 "(or pick Codex in home/home.json, see README), check Perro works, then run this again.")
    if not args.dry_run and running_by_hand():
        sys.exit("Perro's heartbeat is running in another window. Stop it there with Ctrl+C (and the "
                 "Telegram bridge too, if it's running), then run this again.")
    if args.with_backup and not (REPO / "home" / ".git").is_dir():
        sys.exit("--with-backup needs home/ to be its own Git repo with a remote (see README).")

    plists = [(HEARTBEAT, heartbeat_plist())]
    paired = (REPO / "home" / "telegram.json").is_file()
    if os.environ.get("PERRO_TELEGRAM_TOKEN") and paired:
        plists.append((TELEGRAM, telegram_plist()))
        plists.append((WATCHDOG, watchdog_plist()))   # it tells you through the bot
    elif os.environ.get("PERRO_TELEGRAM_TOKEN"):
        print("Skipped the Telegram bridge: pair it first (python3 -m perro.telegram --pair), then run this again.")
    else:
        print("Skipped the Telegram bridge: PERRO_TELEGRAM_TOKEN isn't set (see README).")
    if args.with_backup:
        plists.append((BACKUP, backup_plist()))

    if args.dry_run:
        for label, data in plists:
            text = data.decode()
            for name in PASSED_ON:
                if os.environ.get(name):
                    text = text.replace(os.environ[name], f"<your {name}>")
            print(f"--- {AGENTS / (label + '.plist')}\n{text}")
        return
    if sys.platform != "darwin":
        sys.exit("This installer is for macOS. On Windows use scripts/install-task.ps1.")
    (REPO / "home").mkdir(exist_ok=True)
    for label, data in plists:
        install(label, data)
    names = " and the Telegram bridge" if any(label == TELEGRAM for label, _ in plists) else ""
    print(f"\nPerro's heartbeat{names} are running now, with no window, and start at every login.\n"
          "Watch it:  tail -f home/heartbeat.log\n"
          "Stop it all:  python3 scripts/install_mac.py --remove\n"
          "After changing a key or setting, run this again so the background copy gets it.")


if __name__ == "__main__":
    main()
