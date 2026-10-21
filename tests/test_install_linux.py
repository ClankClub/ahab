"""The Linux installer's files, read as text: narrow sudo rules, services that
run as the service user with the keys file, timers, a kept env file, and
which services switch on as setup goes along. Nothing here touches the system."""
import importlib.util
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("install_linux", ROOT / "scripts" / "install_linux.py")
inst = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inst)

# Sudo: the service user may become the agent's user and run its own services; nothing broader.
rules = inst.sudoers("owner", "agent", "/usr/bin/systemctl")
lines = [l for l in rules.splitlines() if l and not l.startswith("#")]
assert "ahab ALL=(agent) NOPASSWD: ALL" in lines
assert "owner ALL=(ahab) NOPASSWD: ALL" in lines
service_rule = next(l for l in lines if l.startswith("ahab ALL=(root)"))
assert "*" not in service_rule and "/usr/bin/systemctl restart ahab-heartbeat.service" in service_rule
assert service_rule.count("/usr/bin/systemctl") == 6, service_rule
assert not any("(ALL)" in l or "ALL=(root) NOPASSWD: ALL" in l for l in lines), lines
assert "owner" not in inst.sudoers(None, "agent", "/usr/bin/systemctl")

# Services run as ahab, read the keys file, and never as root or the agent's user.
hb = inst.unit("ahab-heartbeat", "ahab.heartbeat", "/usr/bin/python3", True)
assert "User=ahab" in hb and f"EnvironmentFile={inst.ENV_FILE}" in hb and "Restart=on-failure" in hb
assert hb.rstrip().endswith("WantedBy=multi-user.target") and "-m ahab.heartbeat --log --scheduled" in hb
assert "NoNewPrivileges" not in hb                     # it would stop `sudo -u agent`
wd = inst.unit("ahab-watchdog", "ahab.watchdog", "/usr/bin/python3", False)
assert "Type=oneshot" in wd and "--scheduled" not in wd and "[Install]" not in wd
assert "OnUnitActiveSec=6h" in inst.timer("ahab-backup") and "Persistent=true" in inst.timer("ahab-backup")
assert "OnUnitActiveSec=10min" in inst.timer("ahab-watchdog")

# The env file: a new one gets a fresh key and an empty token; an existing one is kept as is.
fresh = inst.env_file()
key = inst.env_value(fresh, "AHAB_N8N_KEY")
assert len(key) == 64 and inst.env_value(fresh, "AHAB_TELEGRAM_TOKEN") == ""
assert inst.env_value(inst.env_file(), "AHAB_N8N_KEY") != key
kept = "AHAB_N8N_KEY=abc\nAHAB_TELEGRAM_TOKEN=123:xyz"
assert inst.env_file(kept) == kept + "\n"

# What switches on as setup goes along.
home = Path(tempfile.mkdtemp())
assert inst.wanted(fresh, home) == ["ahab-heartbeat"]
assert inst.wanted(kept, home) == ["ahab-heartbeat"]           # a token but not paired yet
(home / "telegram.json").write_text("{}")
assert inst.wanted(fresh, home) == ["ahab-heartbeat"]          # paired but no token
assert inst.wanted(kept, home) == ["ahab-heartbeat", "ahab-telegram", "ahab-watchdog"]
(home / ".git").mkdir()
assert inst.wanted(kept, home)[-1] == "ahab-backup"

# home.json keeps what's there and gains the shell.
assert inst.with_host_shell({"name": "X", "host_shell": {"user": "old"}}, "agent") == \
    {"name": "X", "host_shell": {"user": "agent"}}
# The status screen: on tty1 as ahab, taking no input, in place of the login prompt.
con = inst.console_unit("/usr/bin/python3")
assert "User=ahab" in con and "TTYPath=/dev/tty1" in con and "StandardInput=null" in con
assert "Conflicts=getty@tty1.service" in con and "-m ahab.console" in con and "Restart=always" in con
assert "EnvironmentFile" not in con                    # it needs no keys
print("ok")
