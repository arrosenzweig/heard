"""Portkey/OpenAI client with per-call token metering.

Every model call in Heard goes through `complete()`, which records real
usage into the active Trace. Those recorded numbers are the source of the
token figures in the report - nothing is estimated.
"""
import json, os, time, threading
from dataclasses import dataclass, field, asdict
from typing import Any
from openai import OpenAI
import config

_client = None
_lock = threading.Lock()

def client() -> OpenAI:
    global _client
    with _lock:
        if _client is None:
            if config.PORTKEY_API_KEY:
                _client = OpenAI(api_key=config.PORTKEY_API_KEY,
                                 base_url=config.PORTKEY_BASE_URL)
            elif config.OPENAI_API_KEY:
                _client = OpenAI(api_key=config.OPENAI_API_KEY)
            else:
                raise RuntimeError(
                    "No API key found. Put PORTKEY_API_KEY in a .env file in "
                    "this folder or the folder above it. See .env.example.")
        return _client


@dataclass
class Call:
    agent: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    latency_s: float
    cache_hit: bool = False
    note: str = ""

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def usd(self) -> float:
        return config.cost_usd(self.prompt_tokens, self.completion_tokens)


@dataclass
class Trace:
    """Collects every model call made while serving one user request."""
    label: str = ""
    calls: list = field(default_factory=list)
    steps: list = field(default_factory=list)
    t0: float = field(default_factory=time.time)

    def record(self, call: Call):
        self.calls.append(call)

    def step(self, name: str, detail: Any = None):
        self.steps.append({"t": round(time.time() - self.t0, 3),
                           "step": name, "detail": detail})

    def summary(self) -> dict:
        pt = sum(c.prompt_tokens for c in self.calls)
        ct = sum(c.completion_tokens for c in self.calls)
        return {
            "label": self.label,
            "n_calls": len(self.calls),
            "cache_hits": sum(1 for c in self.calls if c.cache_hit),
            "prompt_tokens": pt,
            "completion_tokens": ct,
            "total_tokens": pt + ct,
            "usd": round(config.cost_usd(pt, ct), 6),
            "price_verified": config.PRICE_VERIFIED,
            "wall_s": round(time.time() - self.t0, 3),
            "by_agent": _by_agent(self.calls),
            "calls": [asdict(c) | {"usd": round(c.usd, 6)} for c in self.calls],
            "steps": self.steps,
        }


def _by_agent(calls) -> dict:
    out = {}
    for c in calls:
        d = out.setdefault(c.agent, {"calls": 0, "prompt_tokens": 0,
                                     "completion_tokens": 0, "usd": 0.0})
        d["calls"] += 1
        d["prompt_tokens"] += c.prompt_tokens
        d["completion_tokens"] += c.completion_tokens
        d["usd"] = round(d["usd"] + c.usd, 6)
    return out


def complete(agent: str, messages: list, trace: Trace | None = None,
             max_tokens: int = 700, json_mode: bool = False,
             model: str | None = None, note: str = "") -> str:
    """One metered model call. gpt-6-luna requires max_completion_tokens."""
    model = model or config.MODEL
    t = time.time()

    if config.PROVIDER == "anthropic":
        import llm_anthropic
        text, usage = llm_anthropic.complete(agent, messages, model, json_mode=json_mode)
        dt = time.time() - t
        if trace is not None:
            trace.record(Call(agent=agent, model=model,
                              prompt_tokens=usage["prompt_tokens"],
                              completion_tokens=usage["completion_tokens"],
                              cached_tokens=usage["cached_tokens"],
                              latency_s=round(dt, 3), note=note))
        return text

    kwargs = {"model": model, "messages": messages,
              "max_completion_tokens": max_tokens}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    # The Portkey gateway caches identical requests and replays them in ~0.1s
    # with the original usage numbers. That is a real saving in production, but
    # it silently turns repeated evaluation runs into replays of the first one.
    # Evaluations set HEARD_FORCE_FRESH=1 so every repeat is an independent call.
    if config.PROVIDER == "portkey" and os.getenv("HEARD_FORCE_FRESH") == "1":
        kwargs["extra_headers"] = {"x-portkey-cache-force-refresh": "true"}
    r = client().chat.completions.create(**kwargs)
    dt = time.time() - t
    u = r.usage
    cached = 0
    details = getattr(u, "prompt_tokens_details", None)
    if details is not None:
        cached = getattr(details, "cached_tokens", 0) or 0
    if trace is not None:
        trace.record(Call(agent=agent, model=model,
                          prompt_tokens=u.prompt_tokens,
                          completion_tokens=u.completion_tokens,
                          cached_tokens=cached,
                          latency_s=round(dt, 3), note=note))
    return (r.choices[0].message.content or "").strip()


def complete_json(agent: str, messages: list, trace: Trace | None = None,
                  max_tokens: int = 700, note: str = "") -> dict:
    """JSON call that degrades to {} rather than throwing on malformed output."""
    raw = complete(agent, messages, trace, max_tokens, json_mode=True, note=note)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(raw[start:end + 1])
            except json.JSONDecodeError:
                pass
        return {}
