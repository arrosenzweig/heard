"""Heard configuration. No secrets here - keys come from .env."""
import os
from pathlib import Path
from dotenv import load_dotenv

# Assignment rule: look for .env in the repo folder, then one folder above.
HERE = Path(__file__).resolve().parent
for candidate in (HERE / ".env", HERE.parent / ".env"):
    if candidate.exists():
        load_dotenv(candidate)
        break
else:
    load_dotenv()  # fall back to ambient environment

PORTKEY_API_KEY = os.getenv("PORTKEY_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
PORTKEY_BASE_URL = os.getenv("PORTKEY_BASE_URL", "https://api.portkey.ai/v1")

# Provider is chosen by which key is present, unless HEARD_PROVIDER forces
# one. Order matters: an explicit Anthropic key means the user opted into
# Claude, so it wins over the shared course key.
#   anthropic -> Claude via the Anthropic API   (ANTHROPIC_API_KEY)
#   portkey   -> course gateway                 (PORTKEY_API_KEY)
#   openai    -> OpenAI directly                (OPENAI_API_KEY)
DEFAULT_MODELS = {
    "anthropic": "claude-opus-5-5",
    "portkey": "gpt-6-luna",
    "openai": "gpt-4o-mini",
}


def _detect_provider():
    forced = os.getenv("HEARD_PROVIDER", "").strip().lower()
    if forced in DEFAULT_MODELS:
        return forced
    if ANTHROPIC_API_KEY:
        return "anthropic"
    if PORTKEY_API_KEY:
        return "portkey"
    if OPENAI_API_KEY:
        return "openai"
    return "portkey"


PROVIDER = _detect_provider()
MODEL = os.getenv("HEARD_MODEL") or DEFAULT_MODELS[PROVIDER]

# ---------------------------------------------------------------------------
# Pricing, USD per 1M tokens: (input, output, verified?)
#
# Claude rates are Anthropic's published first-party API prices (as listed in
# the Claude API reference, cached 2026-10-06). gpt-6-luna has no published
# rate available to us, so its figures are PLACEHOLDERS and are flagged as
# unverified everywhere a dollar amount is shown. Token COUNTS are always
# measured from the API's own usage report, whatever the provider.
# ---------------------------------------------------------------------------
PRICES = {
    "claude-opus-5-5": (4.00, 20.00, True),
    "claude-sonnet-5-5": (2.00, 10.00, True),
    "claude-haiku-5-5": (0.10, 0.50, True),
    "gpt-6-luna": (0.15, 0.60, False),
}
_p = PRICES.get(MODEL, (0.0, 0.0, False))
USD_PER_1M_INPUT = float(os.getenv("USD_PER_1M_INPUT", _p[0]))
USD_PER_1M_OUTPUT = float(os.getenv("USD_PER_1M_OUTPUT", _p[1]))
PRICE_VERIFIED = _p[2] and not (os.getenv("USD_PER_1M_INPUT") or os.getenv("USD_PER_1M_OUTPUT"))

DB_PATH = os.getenv("HEARD_DB", str(HERE / "heard.db"))
DATA_DIR = HERE / "data"

def cost_usd(prompt_tokens: int, completion_tokens: int) -> float:
    return (prompt_tokens / 1_000_000) * USD_PER_1M_INPUT + \
           (completion_tokens / 1_000_000) * USD_PER_1M_OUTPUT
