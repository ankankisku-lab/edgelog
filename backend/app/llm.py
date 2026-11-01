"""OpenRouter client (OpenAI-compatible API) with model fallback and JSON-output handling."""

import json
import logging
import re
from datetime import datetime, timezone
from collections.abc import Iterator

import openai

from .config import get_settings

log = logging.getLogger(__name__)


class AIUnavailable(RuntimeError):
    """No API key configured, or every model failed for a non-rate-limit reason."""


class AIRateLimited(RuntimeError):
    """Every configured model is rate-limited (or out of credits). Retry later.

    retry_at is set when the limit is OpenRouter's free daily cap, whose reset time is known.
    """

    def __init__(self, message: str, retry_at: datetime | None = None):
        super().__init__(message)
        self.retry_at = retry_at


def _daily_reset(e: Exception) -> datetime | None:
    if "free-models-per-day" not in (getattr(e, "message", None) or str(e)):
        return None
    try:
        return datetime.fromtimestamp(int(e.response.headers.get("x-ratelimit-reset")) / 1000, timezone.utc)  # type: ignore[union-attr]
    except (AttributeError, TypeError, ValueError):
        return None


_client: openai.OpenAI | None = None


def ai_configured() -> bool:
    return bool(get_settings().openrouter_api_key)


def _get_client() -> openai.OpenAI:
    global _client
    s = get_settings()
    if not s.openrouter_api_key:
        raise AIUnavailable("OPENROUTER_API_KEY is not set")
    if _client is None:
        _client = openai.OpenAI(
            base_url=s.openrouter_base_url,
            api_key=s.openrouter_api_key,
            default_headers={"X-Title": "Trading Journal"},
            timeout=180,
            max_retries=1,
        )
    return _client


def _describe(model: str, e: Exception) -> str:
    """One readable line per failed model; OpenRouter's free daily cap gets its reset time spelled out."""
    msg = getattr(e, "message", None) or str(e)
    if "free-models-per-day" in msg:
        reset = ""
        try:
            ms = int(e.response.headers.get("x-ratelimit-reset"))  # type: ignore[union-attr]
            reset = f", resets {datetime.fromtimestamp(ms / 1000, timezone.utc).astimezone():%d %b %H:%M}"
        except (AttributeError, TypeError, ValueError):
            pass
        return f"{model}: free daily limit used up (add $10 of OpenRouter credit for 1,000/day){reset}"
    if isinstance(e, openai.RateLimitError):
        return f"{model}: rate-limited by the provider, try again shortly"
    if isinstance(e, openai.APIStatusError):
        return f"{model}: HTTP {e.status_code} {msg}"
    return f"{model}: {msg}"


def _extract_json(text: str) -> dict:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("model response contained no JSON object")
    return json.loads(text[start:end + 1])


def chat_json(messages: list[dict], schema: dict, models: list[str], max_tokens: int = 4000) -> tuple[dict, str]:
    """Run a chat completion that must return a JSON object. Returns (data, model_used).

    Tries each model in order. Per model, asks for strict JSON-schema output first, then falls back
    to plain JSON mode and finally to prompt-only JSON for providers that support neither.
    """
    client = _get_client()
    rate_limited: list[str] = []
    resets: list[datetime | None] = []
    errors: list[str] = []
    formats = [
        {"type": "json_schema", "json_schema": {"name": "extraction", "strict": True, "schema": schema}},
        {"type": "json_object"},
        None,
    ]
    for model in models:
        for fmt in formats:
            try:
                kwargs = {"response_format": fmt} if fmt else {}
                resp = client.chat.completions.create(
                    model=model, messages=messages, max_tokens=max_tokens, temperature=0, **kwargs
                )
                if not resp.choices or not resp.choices[0].message.content:
                    raise ValueError("empty response")
                return _extract_json(resp.choices[0].message.content), model
            except openai.RateLimitError as e:
                log.warning("rate limited on %s: %s", model, e)
                rate_limited.append(_describe(model, e))
                resets.append(_daily_reset(e))
                break
            except openai.APIStatusError as e:
                if e.status_code == 402:  # out of credits behaves like a rate limit: retry later
                    rate_limited.append(_describe(model, e))
                    resets.append(None)
                    break
                if e.status_code in (400, 422) and fmt is not None:
                    continue  # this provider doesn't accept the response_format; try a looser one
                errors.append(f"{model}: HTTP {e.status_code} {e.message}")
                break
            except openai.APIConnectionError as e:
                errors.append(f"{model}: connection error {e}")
                break
            except (ValueError, json.JSONDecodeError) as e:
                errors.append(f"{model}: unparseable output ({e})")
                continue
    if rate_limited and not errors:
        # every model is capped until a known time: no point retrying before the earliest reset
        retry_at = min(resets) if resets and all(resets) else None
        raise AIRateLimited("; ".join(rate_limited), retry_at)
    raise AIUnavailable("; ".join(errors) or "no models configured")


def stream_chat(messages: list[dict], models: list[str], max_tokens: int = 4000) -> Iterator[str]:
    """Stream a text completion, falling back to the next model if one fails before any output."""
    client = _get_client()
    errors: list[str] = []
    for model in models:
        started = False
        try:
            stream = client.chat.completions.create(
                model=model, messages=messages, max_tokens=max_tokens, temperature=0.2, stream=True
            )
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                    started = True
                    yield chunk.choices[0].delta.content
            if started:
                return
            errors.append(f"{model}: empty response")
        except (openai.APIStatusError, openai.APIConnectionError) as e:
            errors.append(_describe(model, e))
        if started:  # failed mid-answer: don't restart on another model and duplicate text
            yield f"\n\n[stream interrupted: {errors[-1]}]"
            return
    lines = "\n".join(f"- {e}" for e in errors) or "- no models configured"
    yield f"**AI unavailable** — every model failed:\n{lines}"
