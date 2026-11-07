"""
One place for model calls, so the pipeline isn't tied to a provider.

  Anthropic API (default):   set ANTHROPIC_API_KEY
  Any OpenAI-compatible API: set PROMO_FINDER_BASE_URL and PROMO_FINDER_API_KEY
                             (e.g. an OmniRoute gateway, Gemini's OpenAI endpoint)

Every call returns the Anthropic response shape (.content, .stop_reason, .usage),
so callers don't care which backend ran.
"""
import contextvars
import json
import os
import re
import time
import types
from pathlib import Path

from pydantic import ValidationError


def _load_dotenv():
    """Reads KEY=value lines from .env next to this file. Real environment variables win."""
    path = Path(__file__).resolve().parents[2] / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"'))


_load_dotenv()
BASE_URL = os.environ.get("PROMO_FINDER_BASE_URL", "")
_session = contextvars.ContextVar("llm_session", default=None)  # per-user settings when the app is hosted
STOP_REASONS = {"stop": "end_turn", "length": "max_tokens", "content_filter": "refusal"}
# A hosted copy sets this low, so a visitor gets a clear message instead of a long wait.
MAX_COOLDOWN_WAITS = int(os.environ.get("PROMO_FINDER_COOLDOWN_WAITS", "3"))
MAX_COOLDOWN_SECONDS = 420


def cooldown_seconds(error_text: str) -> int:
    """Uses the gateway's stated cooldown if it gives one."""
    m = re.search(r"reset_seconds['\"]?:\s*(\d+)", error_text)
    return min(int(m.group(1)) + 5, MAX_COOLDOWN_SECONDS) if m else 60


def friendly_error(e: Exception) -> str:
    """Plain words for a visitor. Never includes the key, even if the provider's message does."""
    if getattr(e, "status_code", None) == 429:
        return "the AI limit is used up for now. Try again later, or add your own key in the sidebar."
    if isinstance(e, ValidationError):
        return "the model's answer wasn't in the expected format, even after retries. Try again, or use a stronger model."
    if getattr(e, "status_code", None) in (401, 403):
        return "the API key was rejected. Check the key and the Base URL."
    return f"{type(e).__name__}. Check the model name and Base URL, or try again."


def use_session(base_url: str, api_key: str):
    """Overrides the endpoint for this user's session only, so keys typed in the app are never shared."""
    _session.set({"base_url": base_url, "api_key": api_key})


def base_url() -> str:
    session = _session.get()
    return session["base_url"] if session else BASE_URL


def get_client():
    if base_url():
        from openai import OpenAI

        session = _session.get()
        key = session["api_key"] if session else os.environ["PROMO_FINDER_API_KEY"]
        return OpenAI(base_url=base_url(), api_key=key, timeout=300.0, max_retries=2)
    import anthropic

    session = _session.get()  # a key typed in the app with no base URL means Anthropic's own API
    return anthropic.Anthropic(api_key=session["api_key"]) if session else anthropic.Anthropic()


def _text(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(b["text"] if isinstance(b, dict) else b.text for b in content if (b["type"] if isinstance(b, dict) else b.type) == "text")


def _openai_compatible(client, model, system, messages, schema_cls, max_tokens):
    # Schema goes in the prompt and is enforced by pydantic on our side: strict
    # response_format support varies too much between models behind a gateway.
    instructions = (f"{_text(system)}\n\nReturn only one JSON object, with no markdown, matching this JSON Schema:\n"
                    f"{json.dumps(schema_cls.model_json_schema())}")
    for wait_round in range(MAX_COOLDOWN_WAITS + 1):
        try:
            r = client.chat.completions.create(
                model=model, max_tokens=max_tokens, response_format={"type": "json_object"},
                messages=[{"role": "system", "content": instructions}]
                + [{"role": m["role"], "content": _text(m["content"])} for m in messages],
            )
            break
        except Exception as e:  # noqa: BLE001 - only rate limits are retried here; anything else re-raises
            if getattr(e, "status_code", None) != 429 or wait_round == MAX_COOLDOWN_WAITS:
                raise
            seconds = cooldown_seconds(str(e))
            print(f"  {model} is rate limited; waiting {seconds}s", flush=True)
            time.sleep(seconds)
    # A retired model can return a canned reply with HTTP 200 and no tokens.
    if not r.usage or not r.usage.total_tokens:
        raise RuntimeError(f"{model} returned no token usage; the reply isn't a real completion")
    choice = r.choices[0]
    return types.SimpleNamespace(
        content=[types.SimpleNamespace(type="text", text=choice.message.content or "")],
        stop_reason=STOP_REASONS.get(choice.finish_reason, choice.finish_reason),
        stop_details=None,
        usage=types.SimpleNamespace(input_tokens=r.usage.prompt_tokens, output_tokens=r.usage.completion_tokens,
                                    cache_read_input_tokens=0, cache_creation_input_tokens=0),
    )


def create(client, model, system, messages, schema_cls, effort="high", max_tokens=16000):
    if base_url():
        return _openai_compatible(client, model, system, messages, schema_cls, max_tokens)
    return client.messages.create(
        model=model, max_tokens=max_tokens, system=system, messages=messages,
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema_cls.model_json_schema()}},
    )


def extract_json(text: str) -> str:
    """Tolerates a markdown fence around the JSON."""
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    return (m.group(1) if m else text).strip()


def complete_measured(schema_cls, prompt: str, model: str):
    """One-shot structured call for the ranking and montage steps, with up to two retries on a schema error.
    Returns (parsed, {"seconds", "output_tokens", "attempts"})."""
    client = get_client()
    system = [{"type": "text", "text": "Answer in the requested structure."}]
    messages = [{"role": "user", "content": prompt}]
    start, tokens = time.time(), 0
    for attempt in (1, 2, 3):
        resp = create(client, model, system, messages, schema_cls)
        tokens += resp.usage.output_tokens or 0
        if resp.stop_reason != "end_turn":
            raise RuntimeError(f"stopped with {resp.stop_reason}")
        text = next(b.text for b in resp.content if b.type == "text")
        try:
            parsed = schema_cls.model_validate_json(extract_json(text))
            return parsed, {"seconds": round(time.time() - start, 2), "output_tokens": tokens, "attempts": attempt}
        except ValidationError as e:
            if attempt == 3:
                raise
            messages = messages + [{"role": "assistant", "content": resp.content},
                                   {"role": "user", "content": f"That didn't match the schema: {str(e)[:500]}\nReturn only the corrected JSON."}]


def complete(schema_cls, prompt: str, model: str):
    return complete_measured(schema_cls, prompt, model)[0]
