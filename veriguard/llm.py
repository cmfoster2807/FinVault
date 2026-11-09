"""Thin client for the NaviGator (OpenAI-compatible) endpoint."""
import json
import os
import sys
import threading
import time

from openai import OpenAI

DEFAULT_BASE_URL = "https://api.ai.it.ufl.edu/v1"
DEFAULT_MODEL = os.environ.get("VERIGUARD_MODEL", "")
TIMEOUT = float(os.environ.get("VERIGUARD_TIMEOUT", "300"))      # seconds per call
MAX_TOKENS = int(os.environ.get("VERIGUARD_MAX_TOKENS", "2500"))  # output cap per call
QUIET = os.environ.get("VERIGUARD_QUIET") == "1"                 # set at runtime to silence progress
USAGE_FILE = os.environ.get("VERIGUARD_USAGE_FILE")               # JSONL of per-call token usage
_usage_lock = threading.Lock()

_client = None
_calls = 0
_tokens = 0


def client() -> OpenAI:
    global _client
    if _client is None:
        key = os.environ.get("VERIGUARD_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("Set VERIGUARD_API_KEY (or OPENAI_API_KEY) to your NaviGator key")
        _client = OpenAI(
            base_url=os.environ.get("VERIGUARD_BASE_URL", DEFAULT_BASE_URL),
            api_key=key,
            timeout=TIMEOUT,
            max_retries=6,  # the SDK backs off exponentially on rate limits and 5xx
        )
    return _client


def chat(prompt: str, model: str = None, temperature: float = 0.0, max_tokens: int = None) -> str:
    global _calls, _tokens
    model = model or DEFAULT_MODEL
    if not model:
        raise RuntimeError("No model set: pass --model / --vg-model or set VERIGUARD_MODEL "
                           "(use the exact model id from the NaviGator model list)")
    _calls += 1
    if not QUIET:
        print(f"  [llm #{_calls}] {model}: sending ~{len(prompt) // 4} tokens...", end="",
              file=sys.stderr, flush=True)
    t0 = time.time()
    resp = client().chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=max_tokens or MAX_TOKENS,
    )
    used = getattr(resp, "usage", None)
    total = getattr(used, "total_tokens", 0) or 0
    _tokens += total
    if USAGE_FILE:
        row = {"model": model, "prompt": getattr(used, "prompt_tokens", 0) or 0,
               "completion": getattr(used, "completion_tokens", 0) or 0}
        with _usage_lock, open(USAGE_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    if not QUIET:
        print(f" done in {time.time() - t0:.0f}s, {total} tokens (run total {_tokens})",
              file=sys.stderr, flush=True)
    return resp.choices[0].message.content or ""