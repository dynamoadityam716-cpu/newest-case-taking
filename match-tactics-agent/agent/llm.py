"""Provider-agnostic LLM client for the Tactical Reasoner / Narrator.

Priority: GEMINI_API_KEY (free tier, per plan) → ANTHROPIC_API_KEY (the
brief's pick). `LLM_PROVIDER=claude|gemini` overrides. Keys come from the
environment or, if absent, are auto-loaded from a git-ignored `.env` file
(project root or repo root — see load_dotenv). With no key at all,
`available_provider()` returns None and callers fall back to the rule-based
mock (agent/mock.py) — the pipeline must run keyless.

Stdlib only (urllib), JSON-mode outputs, retry + model fallback.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

GEMINI_MODELS = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]
CLAUDE_MODELS = ["claude-sonnet-4-5", "claude-3-5-haiku-latest", "claude-3-5-sonnet-latest"]

_TIMEOUT_S = 45
_ATTEMPTS_PER_MODEL = 2


class LLMError(RuntimeError):
    pass


def load_dotenv() -> dict[str, str]:
    """Load KEY=VALUE pairs into os.environ from the first .env found.

    Search order: the project root, then the parent repo root (so the key
    works from either working directory). Existing environment variables
    always win — the file only fills gaps. Only KEY=VALUE lines and #/blank
    lines are handled; values are taken verbatim (quotes stripped). Never
    raises: a missing/unreadable .env just means no keys from this source.
    """
    here = Path(__file__).resolve().parent
    loaded: dict[str, str] = {}
    for candidate in (here.parent, here.parent.parent):
        path = candidate / ".env"
        if not path.is_file():
            continue
        try:
            for raw in path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                if key and key not in os.environ:
                    os.environ[key] = value
                    loaded[key] = value
        except OSError:
            return loaded
        return loaded
    return loaded


load_dotenv()


def available_provider() -> str | None:
    forced = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if forced in ("gemini", "claude"):
        return forced
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "claude"
    return None


def _extract_json(text: str) -> dict:
    """Best-effort: strip code fences / prose, parse the outermost object."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise LLMError(f"no JSON object in model reply: {text[:160]!r}")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as err:
        raise LLMError(f"model reply was not valid JSON: {err}: {text[:160]!r}") from err


class LLMClient:
    def __init__(self, provider: str | None = None, model: str | None = None):
        self.provider = provider or available_provider()
        if self.provider not in ("gemini", "claude"):
            raise LLMError("no LLM provider configured (set GEMINI_API_KEY or ANTHROPIC_API_KEY)")
        self.model = model or os.environ.get("LLM_MODEL", "").strip() or None
        self.calls = 0

    # ------------------------------------------------------------------ #

    def complete_json(self, system: str, user: str) -> dict:
        if self.provider == "gemini":
            return _extract_json(self._gemini(system, user))
        return _extract_json(self._claude(system, user))

    # ------------------------------------------------------------------ #

    def _gemini(self, system: str, user: str) -> str:
        key = os.environ["GEMINI_API_KEY"]
        models = [self.model] if self.model else GEMINI_MODELS
        last_err: Exception | None = None
        for model in models:
            body = {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {
                    "temperature": 0.4,
                    "maxOutputTokens": 900,
                    "responseMimeType": "application/json",
                },
            }
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            for attempt in range(1, _ATTEMPTS_PER_MODEL + 1):
                try:
                    self.calls += 1
                    return self._post(url, body, {"x-goog-api-key": key})
                except urllib.error.HTTPError as err:
                    if err.code in (400, 401, 403):
                        raise LLMError(f"Gemini rejected the request ({err.code}) — check GEMINI_API_KEY") from err
                    last_err = err  # 429 / 5xx → backoff, maybe next model
                    time.sleep(1.5 * attempt)
                except (urllib.error.URLError, TimeoutError, OSError) as err:
                    last_err = err
                    time.sleep(1.5 * attempt)
        raise LLMError(f"Gemini unavailable after retries: {last_err}")

    def _claude(self, system: str, user: str) -> str:
        key = os.environ["ANTHROPIC_API_KEY"]
        models = [self.model] if self.model else CLAUDE_MODELS
        last_err: Exception | None = None
        for model in models:
            body = {
                "model": model,
                "max_tokens": 900,
                "temperature": 0.4,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            }
            for attempt in range(1, _ATTEMPTS_PER_MODEL + 1):
                try:
                    self.calls += 1
                    return self._post("https://api.anthropic.com/v1/messages", body,
                                      {"x-api-key": key, "anthropic-version": "2023-06-01"})
                except urllib.error.HTTPError as err:
                    if err.code in (400, 401, 403):
                        raise LLMError(f"Claude rejected the request ({err.code}) — check ANTHROPIC_API_KEY") from err
                    last_err = err
                    time.sleep(1.5 * attempt)
                except (urllib.error.URLError, TimeoutError, OSError) as err:
                    last_err = err
                    time.sleep(1.5 * attempt)
        raise LLMError(f"Claude unavailable after retries: {last_err}")

    # ------------------------------------------------------------------ #

    @staticmethod
    def _post(url: str, body: dict, headers: dict) -> str:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST", headers={
            "Content-Type": "application/json",
            "User-Agent": "match-tactics-agent/0.1",
            **headers,
        })
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if "candidates" in payload:  # Gemini
            parts = payload["candidates"][0].get("content", {}).get("parts", [])
            return "".join(p.get("text", "") for p in parts)
        if "content" in payload:  # Claude
            return "".join(block.get("text", "") for block in payload["content"])
        raise LLMError(f"unrecognised response shape: {list(payload)}")
