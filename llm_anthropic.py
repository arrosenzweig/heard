"""Claude provider for Heard, via the official Anthropic SDK.

Used when ANTHROPIC_API_KEY is set (see config.PROVIDER). Returns the same
(text, usage) shape the rest of the app expects, so agents do not know or care
which provider answered.

Notes specific to Claude Opus 5.5:
  - Thinking is always on and cannot be disabled; depth is set with
    output_config.effort. Triage is a classification task, so it runs at
    "low"; counsel needs judgement about tone and scripture, so it runs at
    "medium" (the model default, set explicitly).
  - Thinking tokens count against max_tokens and are billed as output, so the
    small ceilings tuned for the course model would truncate replies here.
    A generous ceiling is used instead; only tokens actually produced are billed.
  - Server-side refusal fallbacks are enabled, so a rare classifier decline is
    retried on a fallback model inside the same call instead of failing.
"""
import os

import anthropic

import config

_client = None

# Effort per agent. Anything unlisted runs at the model default.
EFFORT = {
    "triage": "low",
    "beliefs": "low",
    "accountability": "low",
    "baseline": "medium",
    "counsel": "medium",
}

MAX_TOKENS = 16000


def client():
    global _client
    if _client is None:
        # Pin the endpoint. Some environments export ANTHROPIC_BASE_URL for
        # their own tooling, and the SDK would otherwise follow it silently.
        base = os.getenv("HEARD_ANTHROPIC_BASE_URL", "https://api.anthropic.com")
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, base_url=base)
    return _client


def complete(agent, messages, model, json_mode=False):
    """One Claude call. Returns (text, usage_dict)."""
    system_parts = [m["content"] for m in messages if m["role"] == "system"]
    convo = [{"role": m["role"], "content": m["content"]}
             for m in messages if m["role"] != "system"]
    system = "\n\n".join(system_parts)
    if json_mode:
        system += "\n\nRespond with a single JSON object and nothing else."

    kwargs = dict(
        model=model,
        max_tokens=MAX_TOKENS,
        system=system,
        messages=convo,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    effort = EFFORT.get(agent)
    if effort:
        kwargs["output_config"] = {"effort": effort}

    r = client().beta.messages.create(**kwargs)

    if r.stop_reason == "refusal":
        # The whole fallback chain declined. Return nothing; callers already
        # treat an empty reply as "no usable output" and fail closed.
        text = ""
    else:
        text = "".join(b.text for b in r.content if b.type == "text").strip()

    u = r.usage
    usage = {
        "prompt_tokens": (u.input_tokens or 0)
                         + (getattr(u, "cache_read_input_tokens", 0) or 0)
                         + (getattr(u, "cache_creation_input_tokens", 0) or 0),
        "completion_tokens": u.output_tokens or 0,
        "cached_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
    }
    return text, usage
