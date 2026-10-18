"""Run Perro unattended on a Mac, the launchd equivalent of the Windows tasks.

launchd is macOS's built-in service manager. A "LaunchAgent" is a small
.plist file in ~/Library/LaunchAgents describing a program to run as you;
launchd starts it at login and (here) restarts it if it crashes.

  python3 scripts/install_mac.py                 install the heartbeat
  python3 scripts/install_mac.py --with-backup   also back up home/ every 6 hours
                                                 (needs home/ to be a Git repo
                                                 with a remote; see README)
  python3 scripts/install_mac.py --remove        uninstall both
  python3 scripts/install_mac.py --dry-run       print the .plist files, change nothing

Set DEEPSEEK_API_KEY in your shell before installing. launchd doesn't read
your shell's startup files, so the key is copied into the heartbeat's plist
(stored as plain text, readable only by you, like `setx` on Windows).
Output goes to home/heartbeat.log and home/backup.log.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
AGENTS = Path.home() / "Library" / "LaunchAgents"
HEARTBEAT = "perro.heartbeat"
BACKUP = "perro.backup"


def plist(label: str, args: list[str], extra: dict) -> bytes:
    home = REPO / "home"
    return plistlib.dumps({
        "Label": label,
        "ProgramArguments": [sys.executable, "-X", "utf8", "-m", *args],
        "WorkingDirectory": str(REPO),
        # Anything that dies before Python can open its own log lands here.
        "StandardOutPath": str(home / "launchd.log"),
        "StandardErrorPath": str(home / "launchd.log"),
        **extra,
    })


def heartbeat_plist(key: str) -> bytes:
    return plist(HEARTBEAT, ["perro.heartbeat", "--log", "--scheduled"], {
        "RunAtLoad": True,                    # start at login
        "KeepAlive": {"Crashed": True},       # restart after a crash, not after a clean exit
        "EnvironmentVariables": {"DEEPSEEK_API_KEY": key},
    })


def backup_plist() -> bytes:
    # Git usually lives in /usr/bin (Xcode tools) or /opt/homebrew/bin.
    return plist(BACKUP, ["perro.backup", "--log"], {
        "StartInterval": 6 * 60 * 60,         # every 6 hours; missed runs catch up after sleep
        "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"},
    })


def launchctl(*args: str) -> None:
    subprocess.run(["launchctl", *args], check=False)


def install(label: str, data: bytes) -> None:
    path = AGENTS / f"{label}.plist"
    launchctl("bootout", f"gui/{os.getuid()}", str(path))   # if already loaded
    AGENTS.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(0o600)
    launchctl("bootstrap", f"gui/{os.getuid()}", str(path))
    print(f"Installed {label} ({path}).")


def remove(label: str) -> None:
    path = AGENTS / f"{label}.plist"
    if path.exists():
        launchctl("bootout", f"gui/{os.getuid()}", str(path))
        path.unlink()
        print(f"Removed {label}.")


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
        remove(HEARTBEAT)
        remove(BACKUP)
        return

    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key and not args.dry_run:
        sys.exit("Set DEEPSEEK_API_KEY first:  export DEEPSEEK_API_KEY=sk-...")
    if args.with_backup and not (REPO / "home" / ".git").is_dir():
        sys.exit("--with-backup needs home/ to be its own Git repo with a remote (see README).")

    plists = [(HEARTBEAT, heartbeat_plist(key or "<your key here>"))]
    if args.with_backup:
        plists.append((BACKUP, backup_plist()))

    if args.dry_run:
        for label, data in plists:
            print(f"--- {AGENTS / (label + '.plist')}\n{data.decode()}")
        return
    if sys.platform != "darwin":
        sys.exit("This installer is for macOS. On Windows use scripts/install-task.ps1.")
    (REPO / "home").mkdir(exist_ok=True)
    for label, data in plists:
        install(label, data)
    print("Perro's heartbeat is running and will start at every login.\n"
          "Stop it:  launchctl bootout gui/$(id -u)/perro.heartbeat\n"
          "Watch it: tail -f home/heartbeat.log")


if __name__ == "__main__":
    main()
