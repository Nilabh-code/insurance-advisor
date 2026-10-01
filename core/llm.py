"""OpenAI-compatible chat client.

Works against the self-hosted 9router gateway, but nothing here is vendor-specific -
any /v1/chat/completions endpoint works.

Two behaviours that matter for this project:

1. Reasoning models emit a long `reasoning_content` field BEFORE the answer. Truncate
   too early and you get an empty reply that looks like a failure. Budget tokens for
   the reasoning pass, and strip it from what reaches the user.
2. Slot extraction wants JSON. We ask for JSON, then repair it, because a 35B model
   will occasionally wrap JSON in prose or a code fence regardless of what we ask.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

import config


class LLMError(RuntimeError):
    """Any failure talking to or parsing from the model endpoint."""


# The gateway sits behind a Cloudflare tunnel, which bot-scores the User-Agent and
# returns 403 to anything that does not look like a browser. A descriptive
# "insurance-advisor/0.1" UA is enough to get blocked, so we send a normal one.
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


# ---------------------------------------------------------------------------
# JSON repair
# ---------------------------------------------------------------------------

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S | re.I)


def extract_json(text: str) -> dict:
    """Pull a JSON object out of a model reply.

    Tries, in order: direct parse; fenced code block; first balanced {...} span;
    single-quote to double-quote rescue; trailing-comma strip. Raises LLMError if
    nothing parses, so the caller can retry rather than silently get {}.
    """
    if not text or not text.strip():
        raise LLMError("empty response from model")

    candidates: list[str] = []
    candidates.append(text.strip())

    for m in _FENCE.finditer(text):
        candidates.append(m.group(1).strip())

    # Balanced-brace scan, ignoring braces inside strings
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        while start != -1:
            depth, in_str, esc = 0, False, False
            for i in range(start, len(text)):
                ch = text[i]
                if esc:
                    esc = False
                    continue
                if ch == "\\":
                    esc = True
                    continue
                if ch == '"':
                    in_str = not in_str
                    continue
                if in_str:
                    continue
                if ch == opener:
                    depth += 1
                elif ch == closer:
                    depth -= 1
                    if depth == 0:
                        candidates.append(text[start:i + 1])
                        break
            start = text.find(opener, start + 1)

    for cand in candidates:
        for attempt in (cand, _repair(cand)):
            try:
                val = json.loads(attempt)
                if isinstance(val, dict):
                    return val
                if isinstance(val, list):
                    return {"items": val}
            except (json.JSONDecodeError, TypeError):
                continue

    raise LLMError(f"could not parse JSON from model reply: {text[:300]!r}")


def _repair(s: str) -> str:
    """Common malformation fixes, applied in one pass."""
    out = s.strip()
    # strip anything before the first brace
    i = out.find("{")
    if i > 0:
        out = out[i:]
    # trailing commas before } or ]
    out = re.sub(r",\s*([}\]])", r"\1", out)
    # Python literals
    out = re.sub(r"\bNone\b", "null", out)
    out = re.sub(r"\bTrue\b", "true", out)
    out = re.sub(r"\bFalse\b", "false", out)
    # single quotes -> double, when there are no double quotes present
    if '"' not in out:
        out = out.replace("'", '"')
    return out


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class LLMClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None,
                 model: str | None = None, retries: int = 2):
        self.base_url = (base_url or config.BASE_URL).rstrip("/")
        self.api_key = api_key if api_key is not None else config.API_KEY
        self.model = model or config.MODEL
        self.retries = retries

    # -- low level ---------------------------------------------------------
    def _post(self, path: str, payload: dict, timeout: int | None = None) -> dict:
        if not self.api_key:
            raise LLMError("no API key configured (set LLM_API_KEY or NINEROUTER_API_KEY)")

        url = f"{self.base_url}{path}"
        body = json.dumps(payload).encode()
        last_error: Exception | None = None

        # A self-hosted 35B on a Pi returns transient 502/503 while the model is
        # still loading or when two requests collide. Retry those - they are not
        # real failures, and the caller should not have to care.
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(
                url, data=body, method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout or config.REQUEST_TIMEOUT) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")[:300]
                last_error = LLMError(f"HTTP {e.code} from {url}: {detail}")
                # 5xx and 429 are worth another go; 4xx (bad key, no such model) is not.
                if e.code < 500 and e.code != 429:
                    raise last_error from e
            except urllib.error.URLError as e:
                last_error = LLMError(f"cannot reach {url}: {e.reason}")
            except TimeoutError as e:
                last_error = LLMError(
                    f"timeout after {timeout or config.REQUEST_TIMEOUT}s "
                    f"- a 35B model on a Pi can be slow on first load")
            except json.JSONDecodeError as e:
                # Non-JSON body usually means a tunnel/WAF interstitial, not a
                # transient blip. Retrying is pointless.
                raise LLMError(f"non-JSON response from {url}: {e}") from e

            if attempt < self.retries:
                # Back off: the gateway itself advertises a reset window.
                time.sleep(min(2 ** attempt, 8))

        raise last_error or LLMError(f"request to {url} failed")

    # -- public ------------------------------------------------------------
    def chat(self, messages: list[dict], *, temperature: float | None = None,
             max_tokens: int | None = None, json_mode: bool = False,
             timeout: int | None = None) -> str:
        """Return assistant text (reasoning stripped, tool calls not handled)."""
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": config.TEMPERATURE if temperature is None else temperature,
            # Headroom so a reasoning pass cannot eat the whole budget and leave
            # an empty content field.
            "max_tokens": max_tokens or (config.MAX_TOKENS + config.REASONING_TOKENS),
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        data = self._post("/chat/completions", payload, timeout=timeout)

        choices = data.get("choices")
        if not choices:
            # Surface the gateway's own error shape instead of a bare KeyError
            err = data.get("error") or data
            raise LLMError(f"no choices in response: {str(err)[:300]}")

        msg = choices[0].get("message") or {}
        text = (msg.get("content") or "").strip()

        if not text:
            # Reasoning-only response: the token budget was consumed by the
            # thinking pass. Say so plainly rather than returning "".
            reasoning = msg.get("reasoning_content") or ""
            if reasoning:
                raise LLMError(
                    "model spent the whole token budget on reasoning and returned "
                    "no content - raise REASONING_TOKENS or MAX_TOKENS"
                )
            raise LLMError(f"empty message content: {str(msg)[:200]}")
        return text

    def chat_json(self, messages: list[dict], **kw) -> dict:
        """Chat, then parse a JSON object out of the reply."""
        text = self.chat(messages, json_mode=True, **kw)
        try:
            return extract_json(text)
        except LLMError:
            # Some gateways reject response_format; retry once without it.
            if kw.get("json_mode_retry") is False:
                raise
            return extract_json(self.chat(messages, **{**kw, "json_mode": False}))

    def health(self) -> dict:
        """Cheap reachability probe. Never raises."""
        try:
            req = urllib.request.Request(f"{self.base_url}/models",
                                         headers={"Authorization": f"Bearer {self.api_key}",
                                                  "User-Agent": USER_AGENT,
                                                  "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as r:
                models = [m.get("id") for m in json.loads(r.read()).get("data", [])]
            return {"ok": True, "model": self.model, "catalog": models}
        except Exception as e:
            return {"ok": False, "model": self.model, "error": str(e)[:200]}


if __name__ == "__main__":
    client = LLMClient()
    print("config:", config.redacted())
    h = client.health()
    print("health:", h.get("ok"), "-", h.get("error", f"{len(h.get('catalog', []))} models"))
    if h.get("ok"):
        print(client.chat([{"role": "user", "content": "Reply with exactly: PONG"}]))