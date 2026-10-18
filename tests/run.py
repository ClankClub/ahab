"""Run Perro's tests: py tests/run.py (or python3 tests/run.py).

Each test_*.py is a plain script that exits non-zero on failure; nothing here
needs a network, a model, Docker or real keys. The relay checks run the n8n
workflows' allowlist code under Node, and are skipped if node isn't installed.
Some tests read clock times, so they run in US Eastern time on every machine.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
RELAYS = {"relay_vercel_check.js": "perro-vercel.json", "relay_google_cal_check.js": "perro-google.json",
          "relay_email_check.js": "perro-email-send.json", "relay_notion_check.js": "perro-notion.json"}


def main() -> int:
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    if os.name != "nt":                             # Windows ignores TZ; set the clock there instead
        env["TZ"] = "America/New_York"
    env.pop("PERRO_N8N_KEY", None)                  # never let a test near the real relays
    only = sys.argv[1:]
    jobs = [[sys.executable, "-X", "utf8", str(t)] for t in sorted(HERE.glob("test_*.py"))
            if not only or t.stem in only or t.name in only]
    node = shutil.which("node")
    for js, flow in RELAYS.items():
        if only and js not in only:
            continue
        if node:
            jobs.append([node, str(HERE / js), str(ROOT / "n8n" / "workflows" / flow)])
        else:
            print(f"skip {js} (node isn't installed)")
    failed = []
    for job in jobs:
        name = Path(job[1] if job[0] == node else job[-1]).name
        start = time.monotonic()
        done = subprocess.run(job, cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8")
        took = time.monotonic() - start
        if done.returncode == 0:
            print(f"ok    {name} ({took:.1f}s)")
        else:
            failed.append(name)
            print(f"FAIL  {name}\n{done.stdout[-3000:]}{done.stderr[-3000:]}")
    print(f"\n{len(jobs) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
