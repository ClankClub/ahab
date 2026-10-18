"""Model client: the one place Perro talks to a language model.

Every backend Perro might use (DeepSeek, OpenAI, a local llama.cpp or Ollama
server) speaks the same "OpenAI-compatible chat completions" protocol:
POST a JSON list of messages to <base_url>/chat/completions, get JSON back.
So swapping models means changing a URL and a model name, nothing else.

This module knows nothing about memory, heartbeats, or tools. It takes a
conversation in and gives a reply out. Everything else is built on top.
"""

from __future__ import annotations

import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

# A message is {"role": "system" | "user" | "assistant", "content": "..."}.
Message = dict[str, str]

# Named backends. "local" assumes llama.cpp's server on its default port;
# Ollama would be http://localhost:11434/v1 instead.
BACKENDS = {
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        # DeepSeek-V4.1-Flash. Thinking mode is on by default.
        "model": "deepseek-flash",
        "api_key_env": "DEEPSEEK_API_KEY",
    },
    "local": {
        "base_url": "http://localhost:8080/v1",
        "model": "local",
        "api_key_env": None,
    },
    # OpenRouter resells hundreds of models behind one key. Nemo is Mistral's
    # 12B open model, about $0.03 per million tokens each way (2026-10).
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "model": "mistralai/mistral-nemo",
        "api_key_env": "OPENROUTER_API_KEY",
        # OpenRouter picks among several hosts per model, and not all honor
        # every option. This makes it skip hosts that would ignore JSON mode.
        "extra": {"provider": {"require_parameters": True}},
    },
}

# How many times to ask when JSON mode comes back empty or with leaked markup.
EMPTY_REPLY_ATTEMPTS = 4

# Status codes worth retrying: rate limited, or the server had a bad moment.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class ModelError(Exception):
    """The model call failed in a way the caller should hear about."""


@dataclass
class Reply:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    # Thinking models (DeepSeek in thinking mode) reason before answering and
    # return that reasoning separately from the answer. Empty if there's none.
    reasoning: str = ""


class ModelClient:
    def __init__(self, base_url: str, model: str, api_key: str | None = None,
                 timeout: float = 120.0, max_attempts: int = 3, extra: dict | None = None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        # Backend-specific fields added to every request body.
        self.extra = extra or {}
        self.timeout = timeout
        self.max_attempts = max_attempts

    @classmethod
    def from_backend(cls, name: str = "deepseek", **overrides) -> ModelClient:
        """Build a client from a named backend, reading its key from the environment."""
        if name not in BACKENDS:
            raise ModelError(f"Unknown backend {name!r}; known: {', '.join(BACKENDS)}")
        config = dict(BACKENDS[name])
        key_env = config.pop("api_key_env")
        api_key = os.environ.get(key_env) if key_env else None
        if key_env and not api_key:
            raise ModelError(f"{key_env} is not set. See README.md > Setup.")
        config.update(overrides)
        return cls(api_key=api_key, **config)

    def complete(self, messages: list[Message], temperature: float = 0.7,
                 json_mode: bool = False) -> Reply:
        """Send a conversation, return the model's next message.

        json_mode asks the server to guarantee the reply is valid JSON. The
        prompt itself must still mention JSON and describe the shape wanted.
        """
        payload = {
            **self.extra,
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        # DeepSeek's flash model sometimes answers a JSON-mode request with
        # nothing, or with fragments of its own native tool-call markup
        # ("DSML" tokens) instead of the JSON asked for. That's the model
        # slipping, not an answer, so ask again right away.
        for attempt in range(1, EMPTY_REPLY_ATTEMPTS + 1):
            data = self._post_with_retries(f"{self.base_url}/chat/completions", body, headers)
            try:
                message = data["choices"][0]["message"]
                text = message["content"]
            except (KeyError, IndexError, TypeError):
                raise ModelError(f"Unexpected response shape: {str(data)[:300]}")
            if not json_mode or (text and text.strip() and "DSML" not in text):
                break
        usage = data.get("usage") or {}
        return Reply(
            text=text,
            model=data.get("model", self.model),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            reasoning=message.get("reasoning_content") or "",
        )

    def _post_with_retries(self, url: str, body: bytes, headers: dict) -> dict:
        # Network calls fail sometimes. Retry the transient failures with a
        # growing pause (1s, 2s, ...); give up immediately on ones that won't
        # fix themselves, like a bad API key (401) or a malformed request (400).
        last_error = "no attempts made"
        for attempt in range(1, self.max_attempts + 1):
            request = urllib.request.Request(url, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read())
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", errors="replace")[:300]
                last_error = f"HTTP {e.code}: {detail}"
                if e.code not in RETRYABLE_STATUS:
                    raise ModelError(last_error)
            except (urllib.error.URLError, http.client.IncompleteRead, TimeoutError,
                    ConnectionError) as e:
                # IncompleteRead: the response was cut off mid-download.
                # (This exact exception is what crashed Repo-Rat.)
                last_error = f"{type(e).__name__}: {e}"
            except json.JSONDecodeError:
                raise ModelError("Server returned something that isn't JSON.")
            if attempt < self.max_attempts:
                time.sleep(attempt)
        raise ModelError(f"Gave up after {self.max_attempts} attempts. Last error: {last_error}")


if __name__ == "__main__":
    # Manual check: python -m perro.model "your prompt"   (PERRO_BACKEND=local or openrouter to switch)
    prompt = " ".join(sys.argv[1:]) or "In one sentence, what are you?"
    backend = os.environ.get("PERRO_BACKEND", "deepseek")
    try:
        client = ModelClient.from_backend(backend)
        reply = client.complete([{"role": "user", "content": prompt}])
    except ModelError as e:
        sys.exit(f"Model call failed: {e}")
    print(reply.text)
    print(f"\n[{reply.model} | {reply.prompt_tokens} in, {reply.completion_tokens} out]")
