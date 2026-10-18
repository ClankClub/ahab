"""CLI backends: Perro thinking through Codex or Claude Code instead of an API.

Both are agents in their own right, with their own tools, memories and
instruction files. Here they're only the model. Perro's loop, tools and
plain-JSON replies stay Perro's; each call is one stateless, non-interactive
run of the CLI with its own tools switched off, in an empty folder, and its
answer is handed back as if an API had sent it.

Each CLI gets its own profile folder instead of your everyday one, so it
never sees your CLAUDE.md or AGENTS.md, your memories, plugins or MCP
servers, and Perro's calls don't land in your session history:

  <PERRO_CLI_HOME>/codex     CODEX_HOME for Perro's Codex
  <PERRO_CLI_HOME>/claude    CLAUDE_CONFIG_DIR for Perro's Claude Code
  <PERRO_CLI_HOME>/scratch   the empty folder each call runs in

PERRO_CLI_HOME defaults to %LOCALAPPDATA%\\Perro on Windows and ~/.perro
elsewhere, outside both repos, because the login tokens live in there. Log
in once per profile (see README.md > Setup); an empty profile just says
"not logged in", it never falls back to yours.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from perro.model import Message, ModelError, Reply

# Codex features that give its model reach beyond writing an answer. All off.
CODEX_FEATURES_OFF = (
    "shell_tool", "unified_exec", "shell_snapshot", "apps", "plugins", "remote_plugin",
    "browser_use", "browser_use_external", "in_app_browser", "computer_use",
    "image_generation", "view_image", "memories", "multi_agent", "hooks",
    "skill_search", "tool_suggest", "sleep_tool",
)
# Codex's own framing, also off: its notes on the sandbox, permissions, apps,
# skills and plugins described a Codex session, and Perro, asked about its
# tools, described that instead of its own.
CODEX_CONTEXT_OFF = (
    "include_environment_context", "include_permissions_instructions",
    "include_apps_instructions", "include_collaboration_mode_instructions",
    "include_skills_usage_instructions", "include_plugin_usage_instructions",
)
# Some tools come with the model, not a feature: Codex's catalog of models
# (cached in the profile as models_cache.json) gives newer ones a JavaScript
# "exec" box with a clock and apply_patch inside, and a team of helper agents,
# and no switch above removes those. Seeing real tools there, and none of
# Perro's, Perro decided its own tools didn't exist (2026-10-18). So each call
# hands Codex a catalog of one: the model's own entry with those set to plain.
CODEX_MODEL_PLAIN = {"tool_mode": "direct", "experimental_supported_tools": [],
                     "apply_patch_tool_type": None}
CODEX_MODEL_DROP = ("multi_agent_version",)
CODEX_SETTINGS_OFF = ("tools.experimental_request_user_input.enabled=false",)
CLI_TIMEOUT_SECONDS = 300
EMPTY_REPLY_ATTEMPTS = 2


def cli_home() -> Path:
    if os.environ.get("PERRO_CLI_HOME"):
        return Path(os.environ["PERRO_CLI_HOME"])
    if os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "Perro"
    return Path.home() / ".perro"


def find_cli(cli: str) -> str:
    """The CLI's executable: PERRO_<CLI>_BIN, the PATH, or (Codex) the copy the
    desktop app keeps in ~/.codex/packages."""
    override = os.environ.get(f"PERRO_{cli.upper()}_BIN")
    if override:
        return override
    found = shutil.which(cli)
    if found:
        return found
    if cli == "codex":
        releases = Path.home() / ".codex" / "packages" / "standalone" / "releases"
        exes = sorted(releases.glob("*/bin/codex.exe")) + sorted(releases.glob("*/bin/codex"))
        if exes:
            return str(exes[-1])
    raise ModelError(f"Can't find the {cli} command. Install it, or set PERRO_{cli.upper()}_BIN.")


def flatten(messages: list[Message]) -> tuple[str, str]:
    """(system text, everything else as one prompt). A CLI call takes a single
    prompt, so the wake-up so far is written out in order, labeled."""
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    rest = [m for m in messages if m["role"] != "system"]
    if len(rest) == 1:
        return system, rest[0]["content"]
    parts = [rest[0]["content"], "\n\n## This wake-up so far, oldest first"]
    for m in rest[1:]:
        who = "Your reply" if m["role"] == "assistant" else "Then"
        parts.append(f"\n--- {who}:\n{m['content']}")
    parts.append("\n\nNow write your next reply: one JSON object, as described.")
    return system, "\n".join(parts)


class CliClient:
    def __init__(self, cli: str, model: str, effort: str | None = None,
                 timeout: float = CLI_TIMEOUT_SECONDS):
        if cli not in ("codex", "claude"):
            raise ModelError(f"Unknown CLI {cli!r}; known: codex, claude.")
        self.cli, self.model, self.effort, self.timeout = cli, model, effort, timeout
        self.home = cli_home()

    def complete(self, messages: list[Message], temperature: float = 0.7,
                 json_mode: bool = False) -> Reply:
        # temperature: neither CLI takes one. json_mode: the prompt asks for
        # JSON, and parse_reply checks it, same as with any backend.
        system, prompt = flatten(messages)
        total = Reply("", self.model, 0, 0)
        for _ in range(EMPTY_REPLY_ATTEMPTS):
            reply = self._codex(system, prompt) if self.cli == "codex" else self._claude(system, prompt)
            total.prompt_tokens += reply.prompt_tokens
            total.completion_tokens += reply.completion_tokens
            total.cached_tokens += reply.cached_tokens
            total.text, total.reasoning, total.model = reply.text, reply.reasoning, reply.model
            if reply.text.strip():
                break
        return total

    def _run(self, args: list[str], stdin: str, env_name: str, profile: str) -> str:
        scratch = self.home / "scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        (self.home / profile).mkdir(parents=True, exist_ok=True)
        env = {**os.environ, env_name: str(self.home / profile)}
        # The installed heartbeat runs hidden; without this, Windows would
        # flash a console window on screen for every model call.
        hidden = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            done = subprocess.run([find_cli(self.cli), *args], input=stdin, capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", cwd=scratch,
                                  env=env, timeout=self.timeout, creationflags=hidden)
        except subprocess.TimeoutExpired:
            raise ModelError(f"{self.cli} took longer than {self.timeout:.0f} seconds.") from None
        except OSError as e:
            raise ModelError(f"Couldn't run {self.cli}: {e}") from None
        if "not logged in" in (done.stdout + done.stderr).lower() or "401 unauthorized" in done.stderr.lower():
            raise ModelError(f"Perro's {self.cli} profile isn't logged in ({self.home / profile}). "
                             "See README.md > Setup.")
        if done.returncode != 0 and not done.stdout.strip():
            raise ModelError(f"{self.cli} failed (exit {done.returncode}): {done.stderr.strip()[-400:]}")
        return done.stdout

    def _plain_model(self) -> dict | None:
        """The model's catalog entry with its built-in tools taken away, or None
        if Codex hasn't fetched its catalog into this profile yet (it does on
        the first call, so only that one runs with them)."""
        try:
            cache = json.loads((self.home / "codex" / "models_cache.json").read_text(encoding="utf-8"))
            entry = next(m for m in cache["models"] if m.get("slug") == self.model)
        except (OSError, ValueError, KeyError, TypeError, StopIteration):
            return None
        entry = {k: v for k, v in entry.items() if k not in CODEX_MODEL_DROP}
        return {**entry, **CODEX_MODEL_PLAIN}

    def _codex(self, system: str, prompt: str) -> Reply:
        # Perro's system prompt replaces Codex's built-in instructions (about
        # 3,500 tokens describing a coding agent) instead of following them.
        temp = []
        with tempfile.NamedTemporaryFile("w", suffix=".md", encoding="utf-8", delete=False) as f:
            f.write(system or "Answer the user.")  # Codex refuses an empty file
        temp.append(f.name)
        try:
            args = ["exec", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config",
                    "--ignore-rules", "--sandbox", "read-only", "--json", "-m", self.model,
                    "-c", 'web_search="disabled"',
                    "-c", f"model_instructions_file={json.dumps(Path(f.name).as_posix())}"]
            plain = self._plain_model()
            if plain:
                with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8", delete=False) as c:
                    json.dump({"models": [plain]}, c)
                temp.append(c.name)
                args += ["-c", f"model_catalog_json={json.dumps(Path(c.name).as_posix())}"]
            for key in CODEX_CONTEXT_OFF:
                args += ["-c", f"{key}=false"]
            for setting in CODEX_SETTINGS_OFF:
                args += ["-c", setting]
            if self.effort:
                args += ["-c", f'model_reasoning_effort="{self.effort}"']
            for feature in CODEX_FEATURES_OFF:
                args += ["--disable", feature]
            out = self._run(args + ["-"], prompt, "CODEX_HOME", "codex")
        finally:
            for name in temp:
                os.unlink(name)
        text, reasoning, usage, errors = "", [], {}, []
        for line in out.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            item = event.get("item") or {}
            if event.get("type") == "item.completed" and item.get("type") == "agent_message":
                text = item.get("text") or ""
            elif event.get("type") == "item.completed" and item.get("type") == "reasoning":
                reasoning.append(item.get("text") or "")
            elif event.get("type") == "turn.completed":
                usage = event.get("usage") or {}
            elif event.get("type") in ("error", "turn.failed"):
                errors.append(str(event.get("message") or event.get("error") or event)[:300])
        if not text and errors:
            raise ModelError(f"codex: {errors[-1]}")
        return Reply(text, self.model, usage.get("input_tokens") or 0,
                     usage.get("output_tokens") or 0, "\n".join(reasoning),
                     usage.get("cached_input_tokens") or 0)

    def _claude(self, system: str, prompt: str) -> Reply:
        # The system prompt goes through a file: a command line has a length
        # limit on Windows, and Perro's prompt is long.
        with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8", delete=False) as f:
            f.write(system)
        try:
            args = ["-p", "--output-format", "json", "--tools", "", "--strict-mcp-config",
                    "--no-session-persistence", "--model", self.model,
                    "--system-prompt-file", f.name]
            if self.effort:
                args += ["--effort", self.effort]
            out = self._run(args, prompt, "CLAUDE_CONFIG_DIR", "claude")
        finally:
            os.unlink(f.name)
        try:
            data = json.loads(out.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            raise ModelError(f"claude sent something that isn't JSON: {out[:300]}") from None
        if data.get("is_error"):
            raise ModelError(f"claude: {str(data.get('result'))[:300]}")
        usage = data.get("usage") or {}
        cached = usage.get("cache_read_input_tokens") or 0
        prompt_tokens = (usage.get("input_tokens") or 0) + cached + (usage.get("cache_creation_input_tokens") or 0)
        model = next(iter(data.get("modelUsage") or {}), self.model)
        return Reply(data.get("result") or "", model, prompt_tokens,
                     usage.get("output_tokens") or 0, "", cached)
