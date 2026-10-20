"""Offline test: the Codex backend hands Codex a one-model catalog with the model's built-in tools taken away."""
import json, os, sys, tempfile
from pathlib import Path
ROOT = str(Path(__file__).resolve().parent.parent)
from pathlib import Path
sys.path.insert(0, ROOT)
home = Path(tempfile.mkdtemp(prefix="clihome-"))
os.environ["AHAB_CLI_HOME"] = str(home)
from ahab import cli_model

seen = {}
OUT = "\n".join(json.dumps(e) for e in (
    {"type": "item.completed", "item": {"type": "agent_message", "text": '{"thought": "hi"}'}},
    {"type": "turn.completed", "usage": {"input_tokens": 600, "output_tokens": 5}}))


def fake_run(self, args, stdin, env_name, profile):
    seen["args"] = args
    catalogs = [a.split("=", 1)[1] for a in args if a.startswith("model_catalog_json=")]
    seen["catalog"] = json.loads(Path(json.loads(catalogs[0])).read_text(encoding="utf-8")) if catalogs else None
    seen["catalog_path"] = json.loads(catalogs[0]) if catalogs else None
    return OUT


cli_model.CliClient._run = fake_run
client = cli_model.CliClient("codex", "gpt-6-luna", "high")
msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hello"}]

# No cache yet (a fresh profile): runs without a catalog rather than failing.
assert client.complete(msgs).text == '{"thought": "hi"}'
assert seen["catalog"] is None
assert "tools.experimental_request_user_input.enabled=false" in seen["args"]

# With Codex's cache: the model's own entry, made plain, and nothing else.
(home / "codex").mkdir(parents=True, exist_ok=True)
entry = {"slug": "gpt-6-luna", "tool_mode": "code_mode_only", "multi_agent_version": "v2",
         "experimental_supported_tools": ["clock"], "apply_patch_tool_type": "freeform",
         "context_window": 272000, "model_messages": {"x": "y"}}
other = dict(entry, slug="gpt-6-sol")
(home / "codex" / "models_cache.json").write_text(json.dumps({"etag": "e", "models": [other, entry]}), encoding="utf-8")
client.complete(msgs)
assert seen["catalog"] == {"models": [{"slug": "gpt-6-luna", "tool_mode": "direct", "experimental_supported_tools": [],
                                       "apply_patch_tool_type": None, "context_window": 272000,
                                       "model_messages": {"x": "y"}}]}, seen["catalog"]
assert not Path(seen["catalog_path"]).exists(), "the catalog file is cleaned up"
assert "--disable" in seen["args"] and seen["args"][-1] == "-"

# A model the cache doesn't know, or a broken cache: no catalog, no crash.
cli_model.CliClient("codex", "gpt-unknown").complete(msgs)
assert seen["catalog"] is None
(home / "codex" / "models_cache.json").write_text("{not json", encoding="utf-8")
client.complete(msgs)
assert seen["catalog"] is None
print("cli catalog ok")
