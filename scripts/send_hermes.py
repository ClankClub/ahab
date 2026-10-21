"""Send a copy of a Hermes agent's record to Ahab, as one source delivery.

Runs where Hermes runs, with only Python's standard library, so the machine
doesn't need Ahab installed. It asks Hermes (in its Docker container) for a
read-only export of every conversation, with credentials scrubbed
(`hermes sessions export --redact`), and packs that with Hermes's curated
memory, its skills' SKILL.md files and the text files in its workspaces into
a tar stream. The stream is built inside the container and comes out over
`docker exec`; nothing is mounted or copied with `docker cp`, and nothing in
Hermes is changed. Keys, logins, config files and databases are never packed.

The stream goes to Ahab over SSH. On the Ahab machine, the key it uses is a
forced-command key (see ahab/sources.py), so all it can do is deliver:

  python send_hermes.py --ssh ahab@ahab-machine --key ~/.ssh/ahab_source_hermes

or, with Hermes and Ahab on the same machine, straight into the receiver:

  python send_hermes.py --local /path/to/ahab

Options: --container (default hermes-gateway), --user (the container user
Hermes runs as, default hermes, so nothing it writes is owned by root),
--python (the container's Python, default /opt/hermes/.venv/bin/python),
--hermes (the CLI, default /opt/hermes/bin/hermes), --source (the source's
name in Ahab's home.json, default hermes), --docker (the docker program),
--log (a file each run's outcome is appended to, for a scheduled run).
Schedule it daily with the system's scheduler (Task Scheduler, a systemd
timer or cron).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time

# Runs inside the container, reading its own settings from argv. Writes a tar to stdout.
INNER = r'''
import hashlib, os, pathlib, re, subprocess, sys, tarfile, tempfile
hermes, data = sys.argv[1], sys.argv[2]
workspaces = [w for w in sys.argv[3:] if os.path.isdir(w)]
TEXT = (".md", ".markdown", ".txt", ".json", ".jsonl", ".yaml", ".yml", ".toml", ".csv", ".tsv", ".html",
        ".htm", ".xml", ".py", ".js", ".ts", ".tsx", ".jsx", ".css", ".sh", ".ps1", ".rs", ".go", ".java",
        ".c", ".h", ".cpp", ".rb", ".php", ".sql", ".ini", ".cfg", ".org", ".rst", ".tex")
NEVER = (".env", "auth.json", "config.yaml", "credentials", ".ssh", ".git", "node_modules", "__pycache__",
         ".venv", "venv", "cache")
LIMIT = 2 * 1024 * 1024
out = tarfile.open(fileobj=sys.stdout.buffer, mode="w|")

def add(path, name):
    info = out.gettarinfo(path, arcname=name)
    if not info.isfile() or info.size > LIMIT:
        return
    info.uid = info.gid = 0; info.uname = info.gname = ""; info.mode = 0o644
    with open(path, "rb") as f:
        out.addfile(info, f)

tmp = tempfile.mkdtemp(prefix="ahab-send-")
export = os.path.join(tmp, "sessions.jsonl")
try:
    done = subprocess.run([hermes, "sessions", "export", export, "--redact"], capture_output=True, text=True)
    if done.returncode or not os.path.isfile(export):
        sys.stderr.write("Hermes's export failed: " + (done.stderr or done.stdout)[-500:] + "\n")
        sys.exit(1)
    info = out.gettarinfo(export, arcname="sessions.jsonl")
    info.uid = info.gid = 0; info.uname = info.gname = ""; info.mode = 0o644
    with open(export, "rb") as f:
        out.addfile(info, f)
finally:
    for name in os.listdir(tmp):
        os.remove(os.path.join(tmp, name))
    os.rmdir(tmp)

memories = os.path.join(data, "memories")
for name in sorted(os.listdir(memories)) if os.path.isdir(memories) else []:
    if name.endswith(".md"):
        add(os.path.join(memories, name), "memory/" + name)

skills = os.path.join(data, "skills")
# Hermes ships dozens of skills; only the ones it made or changed say anything about this owner.
# .bundled_manifest has "name:md5" per shipped skill, hashed the way tools/skills_sync.py's dir_hash does.
shipped = {}
try:
    for line in open(os.path.join(skills, ".bundled_manifest"), encoding="utf-8"):
        name, _, digest = line.strip().partition(":")
        shipped[name] = digest
except OSError:
    pass

def dir_hash(folder):
    h = hashlib.md5()
    root = pathlib.Path(folder)
    for p in sorted(root.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            h.update(str(p.relative_to(root)).encode("utf-8"))
            h.update(p.read_bytes())
    return h.hexdigest()

for top, dirs, files in os.walk(skills):
    dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in NEVER)
    name = os.path.basename(top)
    if "SKILL.md" in files and name in shipped and dir_hash(top) == shipped[name]:
        continue
    if "SKILL.md" in files:
        add(os.path.join(top, "SKILL.md"), "skills/" + os.path.relpath(os.path.join(top, "SKILL.md"), skills).replace(os.sep, "/"))

for w in workspaces:
    label = re.sub(r"[^A-Za-z0-9_-]+", "-", w).strip("-") or "root"   # /opt/data/workspace -> opt-data-workspace
    for top, dirs, files in os.walk(w):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in NEVER)
        for name in sorted(files):
            if name.startswith(".") or name in NEVER or not name.lower().endswith(TEXT):
                continue
            path = os.path.join(top, name)
            add(path, "files/" + label + "/" + os.path.relpath(path, w).replace(os.sep, "/"))
out.close()
'''


def main() -> int:
    parser = argparse.ArgumentParser(description="Send a copy of Hermes's record to Ahab.")
    to = parser.add_mutually_exclusive_group(required=True)
    to.add_argument("--ssh", help="user@host of the Ahab machine")
    to.add_argument("--local", help="Ahab's folder, when Hermes runs on the same machine")
    parser.add_argument("--key", help="the SSH key for --ssh (the forced-command one)")
    parser.add_argument("--container", default="hermes-gateway")
    parser.add_argument("--user", default="hermes")
    parser.add_argument("--python", default="/opt/hermes/.venv/bin/python")
    parser.add_argument("--hermes", default="/opt/hermes/bin/hermes")
    parser.add_argument("--data", default="/opt/data", help="Hermes's data folder in the container")
    parser.add_argument("--workspace", action="append",
                        help="a workspace folder in the container (default /opt/data/workspace and /workspace)")
    parser.add_argument("--source", default="hermes")
    parser.add_argument("--log", help="append each run's outcome to this file (for a scheduled, windowless run)")
    parser.add_argument("--docker", default=shutil.which("docker") or
                        r"C:\Program Files\Docker\Docker\resources\bin\docker.exe")
    args = parser.parse_args()
    workspaces = args.workspace or ["/opt/data/workspace", "/workspace"]

    # The packing script goes in on stdin (python -), not as an argument, so no shell or
    # Windows quoting rule ever touches it.
    pack = [args.docker, "exec", "-i", "-u", args.user, args.container, args.python, "-",
            args.hermes, args.data, *workspaces]
    if args.ssh:
        send = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new"]
        if args.key:
            send += ["-i", os.path.expanduser(args.key), "-o", "IdentitiesOnly=yes"]
        send += [args.ssh]   # the key's forced command decides what runs; nothing else is asked for
    else:
        send = [sys.executable, "-X", "utf8", "-m", "ahab.sources", "receive", args.source]
    # Under pythonw (a scheduled task) there's no console: output goes to --log, and the
    # children get no window of their own either.
    log = open(args.log, "a", encoding="utf-8") if args.log else None
    if log is None and sys.stdout is None:
        log = open(os.devnull, "w")
    quiet = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    packer = subprocess.Popen(pack, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, **quiet)
    receiver = subprocess.Popen(send, stdin=packer.stdout, stdout=subprocess.PIPE, stderr=log,
                                cwd=args.local or None, **quiet)
    packer.stdout.close()
    packer.stdin.write(INNER.encode("utf-8"))
    packer.stdin.close()
    reply, _ = receiver.communicate()
    packed = packer.wait()
    text = reply.decode("utf-8", "replace").strip() or "(no reply from the receiver)"
    if packed:
        text += "\nPacking inside the container failed; see the message above."
    if log:
        log.write(f"{time.strftime('%Y-%m-%d %H:%M')} {text}\n")
        log.close()
    else:
        print(text)
    return 1 if packed or receiver.returncode else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # A scheduled run has nowhere to show a crash, so it goes in the log too.
        import traceback
        where = sys.argv[sys.argv.index("--log") + 1] if "--log" in sys.argv[:-1] else None
        if where:
            with open(where, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M')} crashed:\n{traceback.format_exc()}")
        raise
