"""LLM endpoint configuration.

Reads from environment so no secrets live in the repo. Defaults match the
self-hosted 9router gateway on the Pi (exposed via Cloudflare tunnel).
"""
import os

# --- endpoint -------------------------------------------------------------
BASE_URL = os.environ.get("LLM_BASE_URL", "https://rdd784u.abc-tunnel.us/v1").rstrip("/")

# API key. Prefer an explicit LLM_API_KEY; fall back to the gateway's key.
API_KEY = os.environ.get("LLM_API_KEY") or os.environ.get("NINEROUTER_API_KEY") or ""

# --- model ----------------------------------------------------------------
MODEL = os.environ.get(
    "LLM_MODEL",
    "Unsloth/HauhauCS/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive",
)

# This model emits a long reasoning_content preamble before the answer.
# Budget for it, or you truncate real answers away.
REASONING_TOKENS = 2048
MAX_TOKENS = 4096

# --- client behaviour -----------------------------------------------------
TEMPERATURE = 0.2        # low: we want reliable slot extraction, not creativity
REQUEST_TIMEOUT = 120    # seconds; 35B on a Pi needs longer than a datacenter API
JSON_MODE = True         # request structured output where the model supports it


def has_credentials() -> bool:
    """False when no API key is configured - the engine must still work without one."""
    return bool(API_KEY)


def redacted() -> dict:
    """Config snapshot safe to log or show in the UI."""
    return {
        "base_url": BASE_URL,
        "model": MODEL,
        "api_key": ("set (%d chars)" % len(API_KEY)) if API_KEY else "NOT SET",
        "temperature": TEMPERATURE,
    }