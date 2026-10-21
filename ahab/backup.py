"""Backup: commit and push Ahab's home to its private GitHub repo.

`home/` is its own Git repo (separate from the code), so a backup is just
"commit whatever changed, push". The care is in doing that safely:

  - Only run between wake-ups. Ahab writes to memory.db while it's awake, and
    copying a database mid-write can capture a half-written file.
  - Check the copy we are about to commit is a healthy database, and refuse
    to push it if not. A backup you can't restore is worse than none.
  - Never touch any repo but home's own. If home/.git is missing, git would
    walk up to the code repo and commit *that*; so we check first.

Run by a Windows scheduled task (see scripts/install-backup-task.ps1), or by
hand with `python -m ahab.backup`. Only needs git on the PATH.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ahab import days
from ahab.memory import BACKGROUND_KINDS, DEFAULT_HOME

# A beat that crashed never logs a `sleep` event. If nothing has been logged
# for this long, Ahab isn't mid-wake either way.
QUIET_MINUTES = 10


def between_wakes(home: Path) -> tuple[bool, str]:
    """Is it safe to copy memory.db right now? Returns (safe, reason)."""
    if (home / "memory.db-journal").exists() or (home / "memory.db-wal").exists():
        return False, "database has a write in progress"
    db = home / "memory.db"
    if not db.exists():
        return True, "no database yet"
    try:
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=5)
        try:
            skip = ", ".join("?" * len(BACKGROUND_KINDS))
            row = con.execute(f"SELECT kind, time FROM events WHERE kind NOT IN ({skip}) "
                              "ORDER BY id DESC LIMIT 1", BACKGROUND_KINDS).fetchone()
        finally:
            con.close()
    except sqlite3.OperationalError as e:
        # Busy or locked: someone is writing right now. Try again later.
        # (Anything worse, like a corrupt file, is not caught here on purpose.)
        return False, f"database is busy ({e})"
    if row is None:
        return True, "log is empty"
    kind, when = row
    if kind in ("sleep", "stopped"):
        return True, f"last event is '{kind}'"
    age = datetime.now(timezone.utc) - datetime.fromisoformat(when)
    if age >= timedelta(minutes=QUIET_MINUTES):
        return True, f"last event ('{kind}') was {int(age.total_seconds() // 60)} min ago"
    return False, f"Ahab looks mid-wake (last event '{kind}', {int(age.total_seconds())}s ago)"


def git(home: Path, *args: str, text: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(home), *args], capture_output=True, text=text,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),  # no console flash
    )


def staged_database_ok(home: Path) -> tuple[bool, str]:
    """Open the copy of memory.db that is staged to be committed and check it."""
    blob = git(home, "show", ":memory.db", text=False)
    if blob.returncode != 0:
        return True, "no database in the backup"
    with tempfile.TemporaryDirectory() as d:
        copy = Path(d) / "memory.db"
        copy.write_bytes(blob.stdout)
        try:
            con = sqlite3.connect(copy)
            try:
                result = con.execute("PRAGMA integrity_check").fetchone()[0]
            finally:
                con.close()
        except sqlite3.Error as e:
            return False, str(e)
    return result == "ok", result


def backup(home: Path) -> int:
    """Returns 0 on success or a deliberate skip, 1 on a real failure."""
    if not (home / ".git").is_dir():
        print(f"{home} is not its own Git repo; refusing to touch anything.")
        return 1
    if shutil.which("git") is None:
        print("git is not on the PATH.")
        return 1

    try:
        safe, reason = between_wakes(home)
    except sqlite3.DatabaseError as e:
        print(f"memory.db is unreadable ({e}); not backing up. Look at it before "
              "anything else overwrites the last good backup.")
        return 1
    if not safe:
        print(f"Skipping this run: {reason}. The next scheduled run will catch up.")
        return 0

    # Refresh the readable day files first, so they ride along in this commit.
    days.write_days(home)

    git(home, "add", "-A")
    if git(home, "status", "--porcelain").stdout.strip():
        ok, detail = staged_database_ok(home)
        if not ok:
            print(f"Not committing: the database copy failed its health check ({detail}).")
            git(home, "reset", "-q")
            return 1
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        commit = git(home, "commit", "-q", "-m", f"Automatic backup {now}")
        if commit.returncode != 0:
            print(f"Commit failed:\n{commit.stdout}{commit.stderr}")
            return 1
        print(f"Committed ({reason}).")
    else:
        print("Nothing new since the last backup.")

    # Push even when nothing was committed, so a push that failed last time
    # (network down) gets retried.
    push = git(home, "-c", "push.autoSetupRemote=true", "push", "-q")   # a new repo has no upstream yet
    if push.returncode != 0:
        print(f"Push failed (the commit is kept locally and will be retried):\n{push.stderr}")
        return 1
    print("Pushed.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Back up Ahab's home to Git.")
    parser.add_argument("--home", type=Path, default=DEFAULT_HOME)
    parser.add_argument("--log", action="store_true",
                        help="write output to <home>/backup.log (for running hidden)")
    args = parser.parse_args()
    if args.log:
        args.home.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = open(args.home / "backup.log", "a",
                                       encoding="utf-8", errors="replace", buffering=1)
        print(f"[{datetime.now():%Y-%m-%d %H:%M}] ", end="")
    sys.exit(backup(args.home))


if __name__ == "__main__":
    main()
