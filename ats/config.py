"""Weights, thresholds, and loading settings from .env."""

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, SecretStr

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"

# --- Input limits -----------------------------------------------------------

# Longest CV or job description accepted, in characters. A long CV is about
# 15,000; the cap stops a huge paste being sent to the API three times
# (slow and costly) and failing late with a "request too large" error.
MAX_DOCUMENT_CHARS = 50_000

# Most pages read from a job description PDF. Job postings are 1-5 pages;
# a much longer PDF is probably the wrong file and would be slow to parse.
MAX_PDF_PAGES = 20

# --- Keyword matching -------------------------------------------------------

# Minimum rapidfuzz `fuzz.ratio` score (0-100) for a fuzzy keyword match.
# 90 accepts typos and plurals ("managment", "dashboards") but rejects
# different words that merely share letters ("java" vs "javascript" = 57).
FUZZY_MATCH_THRESHOLD = 90

# Skills shorter than this (in characters) must match exactly. Short words
# differ by only a letter or two, so fuzzy matching gives false positives
# like "sales" vs "scales" (score 91).
FUZZY_MIN_TERM_LENGTH = 6

# --- Final score (step E) ---------------------------------------------------


class ScoreWeights(BaseModel):
    """How much each part counts toward the final score. Must add up to 1."""

    required_keywords: float = 0.45
    preferred_keywords: float = 0.15
    semantic_fit: float = 0.40


SCORE_WEIGHTS = ScoreWeights()

# --- LLM calls --------------------------------------------------------------

# Upper limit on the reply length. Extraction replies are far shorter; a high
# limit just avoids a cut-off reply that would fail validation.
LLM_MAX_TOKENS = 16000

# How much the model reasons before answering ("low" to "max"). Current models
# do not accept a `temperature` setting; repeatable results come from the
# enforced JSON schema and from caching results in the app instead.
# Extraction (step B) is simple, so "low" keeps it fast and cheap.
LLM_EFFORT_EXTRACTION = "low"
# Judging fit (step D) needs more care and carries 40% of the final score.
LLM_EFFORT_SEMANTIC_FIT = "medium"
# Rewriting (step F) must follow strict no-invented-facts rules.
LLM_EFFORT_REWRITES = "medium"

# --- Rewrite suggestion checks (step F) -------------------------------------

# Most suggestions shown; extra ones the AI returns are dropped with a note.
REWRITE_MAX_SUGGESTIONS = 8

# An "original" bullet must appear in the CV. If it is not found word for
# word, a rapidfuzz `partial_ratio` of at least this much still counts, to
# allow tiny differences like a dropped full stop or a curly quote.
REWRITE_ORIGINAL_MATCH_THRESHOLD = 95

# Bullets shorter than this (in characters) must be found word for word,
# because short text gets high partial_ratio scores by chance.
REWRITE_MIN_FUZZY_LENGTH = 20

# Total tries when the reply does not match the expected JSON (1 retry).
LLM_MAX_ATTEMPTS = 2

# Seconds to wait for one API reply before giving up.
LLM_TIMEOUT_SECONDS = 120.0

# How many times the SDK itself re-sends a request after a network error,
# rate limit, or server error (this is also the SDK's default). Worst-case
# wait for one call is about LLM_TIMEOUT_SECONDS x (1 + this).
LLM_SDK_MAX_RETRIES = 2

# --- Result cache (privacy + memory) ----------------------------------------

# AI results are kept in memory so repeat clicks are instant and free. These
# limits make sure CV text does not stay in memory for the whole session.
CACHE_TTL_SECONDS = 3600
CACHE_MAX_ENTRIES = 20

# Values copied unchanged from .env.example, which mean "not filled in yet".
_PLACEHOLDER_PREFIX = "your-"


class MissingSettingError(Exception):
    """Raised when .env is missing a value. The message is safe to show."""


class LLMSettings(BaseModel):
    """API settings read from .env. SecretStr hides the key if printed."""

    api_key: SecretStr
    model: str


def load_llm_settings(env_file: Path = ENV_FILE) -> LLMSettings:
    """Read ANTHROPIC_API_KEY and ANTHROPIC_MODEL from .env (or the environment)."""
    load_dotenv(env_file)
    api_key = _read_setting("ANTHROPIC_API_KEY")
    model = _read_setting("ANTHROPIC_MODEL")
    _check_model_id(model)
    return LLMSettings(api_key=SecretStr(api_key), model=model)


def _check_model_id(model: str) -> None:
    """Catch common typos in ANTHROPIC_MODEL before they reach the API.

    The value itself is still used exactly as written; this only refuses
    values that cannot be a model ID.
    """
    if model.startswith("="):
        raise MissingSettingError(
            "ANTHROPIC_MODEL in .env starts with '=' (the line probably has "
            "'==' instead of '='). Write it as ANTHROPIC_MODEL=<model-id>, "
            "then restart the app."
        )
    if any(character.isspace() for character in model):
        raise MissingSettingError(
            f"ANTHROPIC_MODEL in .env is '{model}', which contains spaces. "
            "Use the model ID (e.g. claude-sonnet-5-5), not the display name, "
            "then restart the app."
        )


def _read_setting(name: str) -> str:
    """Return a non-empty, non-placeholder environment value or raise."""
    value = os.getenv(name, "").strip()
    if not value or value.startswith(_PLACEHOLDER_PREFIX):
        raise MissingSettingError(
            f"{name} is not set. Open the .env file in the project folder "
            f"(`notepad .env`) and fill it in, then restart the app."
        )
    return value
