"""Run Ahab unattended on Linux, as systemd services, with a shell of the
agent's own. The Linux counterpart of install-task.ps1 and install_mac.py.

Run it once as root (the one time the admin password is needed):

  sudo python3 scripts/install_linux.py --owner YOU           set it all up
  sudo python3 scripts/install_linux.py --owner YOU --dry-run print what it would do
  sudo python3 scripts/install_linux.py --remove              take the services away

Three Linux users, so that a planted instruction can't reach the keys:

  YOU     the owner: an admin, as before. Gets a password-free way to act as
          ahab (`sudo -u ahab -i`), so managing Ahab never needs the password.
  ahab    runs the code and holds what it needs: the n8n key and Telegram
          token (in /etc/ahab/env), the Claude login, memory.db, n8n via Docker.
          Its home is private to it. It may start and stop its own services
          and run commands as the agent's user, nothing else.
  agent   (--agent-user) the agent's own shell: the internet, its own home and
          whatever it installs there, but no Docker, no sudo, and none of
          ahab's files. home.json's "host_shell" turns it on (hostshell.py).

It also installs git, curl and Docker from Debian's own packages (asking
first unless --yes), clones the code into /home/ahab/ahab if it isn't there,
and writes the services:

  ahab-heartbeat   always; restarts after a crash
  ahab-telegram    once AHAB_TELEGRAM_TOKEN is set and the bridge is paired
  ahab-watchdog    every 10 minutes, with the bridge (it tells you through it)
  ahab-backup      every 6 hours, once home/ is a Git repo with a remote
  ahab-console     the status screen (ahab/console.py) on the machine's own
                   screen, tty1, in place of its login prompt (--no-console to skip;
                   log in on Ctrl+Alt+F2)

Safe to run again: after setting the token, pairing, or making home/ a repo,
run it again and the services that were waiting get switched on. It never
touches home/ beyond creating it and adding "host_shell" to home.json.
Logs go to /home/ahab/ahab/home/*.log, plus `journalctl -u ahab-heartbeat`.
"""

from __future__ import annotations

import argparse
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

SERVICE_USER = "ahab"
BASE = Path("/home") / SERVICE_USER
CODE = BASE / "ahab"
HOME = CODE / "home"
ENV_DIR = Path("/etc/ahab")
ENV_FILE = ENV_DIR / "env"
SUDOERS = Path("/etc/sudoers.d/ahab")
UNITS = Path("/etc/systemd/system")
REPO_URL = "https://github.com/ClankClub/ahab.git"
PACKAGES = ["git", "curl", "docker.io", "docker-compose"]
LONG_RUNNING = ("ahab-heartbeat", "ahab-telegram")
CONSOLE = "ahab-console"
TIMED = {"ahab-watchdog": ("5min", "10min"), "ahab-backup": ("30min", "6h")}


# --- what gets written: plain functions, so tests can read every file ----------

def unit(name: str, module: str, python: str, long_running: bool) -> str:
    what = {"ahab-heartbeat": "Ahab's heartbeat: wakes the agent and sleeps between wakes",
            "ahab-telegram": "Ahab's Telegram bridge",
            "ahab-watchdog": "Ahab's watchdog: checks the others and n8n, tells the owner",
            "ahab-backup": "Ahab's backup: commits and pushes home/ between wakes"}[name]
    lines = [
        "[Unit]", f"Description={what}",
        "After=network-online.target docker.service", "Wants=network-online.target", "",
        "[Service]", f"User={SERVICE_USER}", f"Group={SERVICE_USER}",
        f"WorkingDirectory={CODE}", f"EnvironmentFile={ENV_FILE}",
        # The Claude CLI installs to ~/.local/bin.
        f"Environment=PATH={BASE}/.local/bin:/usr/local/bin:/usr/bin:/bin",
        "Environment=PYTHONUNBUFFERED=1",
        f"ExecStart={python} -X utf8 -m {module} --log" + (" --scheduled" if name == "ahab-heartbeat" else ""),
    ]
    if long_running:
        lines += ["Restart=on-failure", "RestartSec=30", "", "[Install]", "WantedBy=multi-user.target"]
    else:
        lines += ["Type=oneshot"]
    return "\n".join(lines) + "\n"


def console_unit(python: str) -> str:
    """The status screen on tty1, as ahab. It takes no input (stdin is
    null), so there is no shell behind it for whoever is at the keyboard."""
    return "\n".join([
        "[Unit]", "Description=Ahab's status screen on the machine's own display",
        "After=systemd-user-sessions.service", "Conflicts=getty@tty1.service", "",
        "[Service]", f"User={SERVICE_USER}", f"Group={SERVICE_USER}", "SupplementaryGroups=tty",
        f"WorkingDirectory={CODE}", "Environment=TERM=linux", "Environment=PYTHONUNBUFFERED=1",
        f"Environment=PATH=/usr/local/bin:/usr/bin:/bin",
        f"ExecStart={python} -X utf8 -m ahab.console",
        "StandardInput=null", "StandardOutput=tty", "StandardError=journal", "TTYPath=/dev/tty1",
        "TTYReset=yes", "TTYVHangup=yes", "Restart=always", "RestartSec=5", "",
        "[Install]", "WantedBy=multi-user.target"]) + "\n"


def timer(name: str) -> str:
    first, every = TIMED[name]
    return "\n".join([
        "[Unit]", f"Description=Run {name} every {every}", "",
        "[Timer]", f"OnBootSec={first}", f"OnUnitActiveSec={every}", "Persistent=true", "",
        "[Install]", "WantedBy=timers.target"]) + "\n"


def sudoers(owner: str | None, agent_user: str, systemctl: str) -> str:
    services = ", ".join(f"{systemctl} {verb} {u}.service"
                         for u in LONG_RUNNING for verb in ("start", "stop", "restart"))
    lines = [
        "# Written by Ahab's scripts/install_linux.py. Each line is one narrow permission.",
        f"# The service user may run commands as the agent's shell user (hostshell.py), and nothing else as it.",
        f"{SERVICE_USER} ALL=({agent_user}) NOPASSWD: ALL",
        "# ...and start and stop its own long-running services (heartbeat --restart).",
        f"{SERVICE_USER} ALL=(root) NOPASSWD: {services}",
    ]
    if owner:
        lines += ["# The owner may act as the service user without a password, to manage Ahab.",
                  f"{owner} ALL=({SERVICE_USER}) NOPASSWD: ALL"]
    return "\n".join(lines) + "\n"


def env_file(existing: str = "") -> str:
    """The services' environment. Keeps whatever is already set. A new file
    gets a fresh n8n key, commented out: setting AHAB_N8N_KEY is what tells
    Ahab (and its watchdog) that this install uses n8n, so it's switched on
    when n8n is set up, with the same value in n8n's "Ahab relay key"."""
    if existing.strip():
        return existing if existing.endswith("\n") else existing + "\n"
    return ("# Ahab's settings and keys, read by its services. Readable by root and ahab only.\n"
            "# After changing anything, run the installer again (or: sudo systemctl restart ahab-heartbeat).\n"
            "# Uncomment once n8n is running (n8n/README.md); the same value goes in its \"Ahab relay key\".\n"
            f"# AHAB_N8N_KEY={secrets.token_hex(32)}\n"
            "AHAB_TELEGRAM_TOKEN=\n"
            "# GROQ_API_KEY=        voice notes through Groq's Whisper, instead of on this machine\n")


def env_value(text: str, name: str) -> str:
    for line in text.splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip()
    return ""


def wanted(env_text: str, home: Path) -> list[str]:
    """Which services to switch on, given what's set up so far."""
    on = ["ahab-heartbeat"]
    if env_value(env_text, "AHAB_TELEGRAM_TOKEN") and (home / "telegram.json").is_file():
        on += ["ahab-telegram", "ahab-watchdog"]
    if (home / ".git").is_dir():
        on.append("ahab-backup")
    return on


def with_host_shell(settings: dict, agent_user: str) -> dict:
    return {**settings, "host_shell": {"user": agent_user}}


# --- doing it --------------------------------------------------------------------

def run(*argv: str, user: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    if user:
        argv = ("runuser", "-u", user, "--", *argv)
    print("  $ " + " ".join(argv))
    return subprocess.run(argv, check=check)


def exists(name: str) -> bool:
    return subprocess.run(["id", "-u", name], capture_output=True).returncode == 0


def write(path: Path, text: str, mode: int, group: str | None = None) -> bool:
    """Write a file in one step; returns whether its content changed."""
    try:
        if path.read_text(encoding="utf-8") == text:
            return False
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".new")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, mode)
    if group:
        shutil.chown(tmp, "root", group)
    tmp.replace(path)
    print(f"  wrote {path}")
    return True


def ask(question: str, yes: bool) -> bool:
    if yes:
        return True
    return input(f"{question} [Y/n] ").strip().lower() in ("", "y", "yes")


def install(args) -> None:
    import json
    python = shutil.which("python3") or sys.executable
    systemctl = shutil.which("systemctl") or "/usr/bin/systemctl"

    print("1. Packages")
    missing = [p for p in PACKAGES if subprocess.run(["dpkg", "-s", p], capture_output=True).returncode]
    if missing and ask(f"Install {', '.join(missing)} from Debian's packages?", args.yes):
        run("apt-get", "update")
        run("apt-get", "install", "-y", *missing)

    print("2. Users")
    for name, groups in ((SERVICE_USER, ["docker"]), (args.agent_user, [])):
        if not exists(name):
            run("useradd", "--create-home", "--shell", "/bin/bash", name)
        for g in groups:
            run("usermod", "-aG", g, name, check=False)
        os.chmod(Path("/home") / name, 0o700)      # private: nobody else reads in
    if args.owner:
        mode = (Path("/home") / args.owner).stat().st_mode & 0o077
        if mode:
            print(f"  Note: /home/{args.owner} can be read by other users; `chmod 700 /home/{args.owner}` "
                  f"keeps the agent's shell user out of it.")

    print("3. Code")
    if not (CODE / ".git").is_dir():
        run("git", "clone", REPO_URL, str(CODE), user=SERVICE_USER)
    HOME.mkdir(exist_ok=True)
    shutil.chown(HOME, SERVICE_USER, SERVICE_USER)
    settings_path = HOME / "home.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.is_file() else {}
    settings_path.write_text(json.dumps(with_host_shell(settings, args.agent_user), indent=2) + "\n",
                             encoding="utf-8")
    shutil.chown(settings_path, SERVICE_USER, SERVICE_USER)

    print("4. Keys and permissions")
    env_text = env_file(ENV_FILE.read_text(encoding="utf-8") if ENV_FILE.is_file() else "")
    env_changed = write(ENV_FILE, env_text, 0o640, group=SERVICE_USER)
    rules = sudoers(args.owner, args.agent_user, systemctl)
    check = SUDOERS.with_name("ahab.check")
    check.write_text(rules, encoding="utf-8")
    os.chmod(check, 0o440)
    try:
        run("visudo", "-cf", str(check))                 # a broken rule could lock sudo; check first
        check.replace(SUDOERS)
        print(f"  wrote {SUDOERS}")
    finally:
        check.unlink(missing_ok=True)

    print("5. Services")
    modules = {"ahab-heartbeat": "ahab.heartbeat", "ahab-telegram": "ahab.telegram",
               "ahab-watchdog": "ahab.watchdog", "ahab-backup": "ahab.backup"}
    changed = set()
    for name, module in modules.items():
        if write(UNITS / f"{name}.service", unit(name, module, python, name in LONG_RUNNING), 0o644) or env_changed:
            changed.add(name)
        if name in TIMED and write(UNITS / f"{name}.timer", timer(name), 0o644):
            changed.add(name)
    run(systemctl, "daemon-reload")
    on = wanted(env_text, HOME)
    for name in modules:
        target = f"{name}.timer" if name in TIMED else f"{name}.service"
        if name in on:
            # Restart only what changed: a restart wakes the agent, and a wake costs tokens.
            # Anything else is started if it isn't running and left alone if it is.
            run(systemctl, "enable", target)
            run(systemctl, "restart" if name in changed else "start", target)
        else:
            run(systemctl, "disable", "--now", target, check=False)

    console = UNITS / f"{CONSOLE}.service"
    if args.no_console:
        if console.exists():
            run(systemctl, "disable", "--now", console.name, check=False)
            console.unlink()
            run(systemctl, "daemon-reload")
            run(systemctl, "enable", "--now", "getty@tty1.service", check=False)
    else:
        fresh = write(console, console_unit(python), 0o644)
        run(systemctl, "daemon-reload")
        run(systemctl, "disable", "getty@tty1.service", check=False)   # the screen takes tty1
        run(systemctl, "enable", console.name)
        run(systemctl, "restart" if fresh else "start", console.name)
        on.append(CONSOLE)

    waiting = [n for n in modules if n not in on]
    print("\nDone. Running: " + ", ".join(on) + ".")
    print("Next, as ahab (`sudo -u ahab -i`, no password needed):")
    if not shutil.which("claude", path=f"{BASE}/.local/bin"):
        print("  - the Claude CLI: curl -fsSL https://claude.ai/install.sh | bash")
    print("  - sign it in: mkdir -p ~/.ahab/claude && CLAUDE_CONFIG_DIR=~/.ahab/claude claude auth login")
    if not env_value(env_text, "AHAB_N8N_KEY"):
        print("  - n8n, if you want it: cd ~/ahab/n8n && docker compose up -d, then n8n/README.md; "
              f"uncomment AHAB_N8N_KEY in {ENV_FILE} and run this installer again")
    if "ahab-telegram" in waiting:
        print(f"  - Telegram: put the bot token in {ENV_FILE}, pair with "
              "`cd ~/ahab && python3 -m ahab.telegram --pair`, then run this installer again")
    if "ahab-backup" in waiting:
        print("  - backups: make home/ a Git repo with a private remote, then run this installer again")


def remove() -> None:
    systemctl = shutil.which("systemctl") or "/usr/bin/systemctl"
    for name in (*LONG_RUNNING, *TIMED, CONSOLE):
        for kind in ("timer", "service"):
            path = UNITS / f"{name}.{kind}"
            if path.exists():
                run(systemctl, "disable", "--now", path.name, check=False)
                path.unlink()
    run(systemctl, "daemon-reload")
    run(systemctl, "enable", "--now", "getty@tty1.service", check=False)   # the login prompt back
    SUDOERS.unlink(missing_ok=True)
    print(f"Removed the services and {SUDOERS}. The users, /home/ahab and {ENV_FILE} are left as they were.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Install Ahab's systemd services and users (Linux).")
    parser.add_argument("--owner", help="your own Linux user name (gets password-free `sudo -u ahab`)")
    parser.add_argument("--agent-user", default="agent", help="the agent's shell user (default: agent)")
    parser.add_argument("--no-console", action="store_true", help="leave the screen's login prompt alone")
    parser.add_argument("--yes", action="store_true", help="install packages without asking")
    parser.add_argument("--remove", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.agent_user in ("root", SERVICE_USER) or (args.owner and args.owner == args.agent_user):
        sys.exit("The agent's shell user has to be its own user, not root, ahab or you.")
    if args.dry_run:
        python, systemctl = shutil.which("python3") or "/usr/bin/python3", "/usr/bin/systemctl"
        print(f"--- {SUDOERS}\n{sudoers(args.owner, args.agent_user, systemctl)}")
        print(f"--- {ENV_FILE} (if new; an existing one is kept)\n{env_file()}")
        for name, module, long in (("ahab-heartbeat", "ahab.heartbeat", True), ("ahab-telegram", "ahab.telegram", True),
                                   ("ahab-watchdog", "ahab.watchdog", False), ("ahab-backup", "ahab.backup", False)):
            print(f"--- {UNITS / (name + '.service')}\n{unit(name, module, python, long)}")
            if name in TIMED:
                print(f"--- {UNITS / (name + '.timer')}\n{timer(name)}")
        return
    if not sys.platform.startswith("linux") or not shutil.which("systemctl"):
        sys.exit("This installer is for Linux with systemd. On Windows use scripts/install-task.ps1, on a "
                 "Mac scripts/install_mac.py.")
    if os.geteuid() != 0:
        sys.exit("Run it as root: sudo python3 scripts/install_linux.py --owner YOU")
    if sys.version_info < (3, 11):
        sys.exit(f"Ahab needs Python 3.11 or newer; this is {sys.version.split()[0]}.")
    if args.remove:
        remove()
    else:
        install(args)


if __name__ == "__main__":
    main()
