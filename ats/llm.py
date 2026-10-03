"""All Anthropic API calls live here, and nowhere else."""

import re
from pathlib import Path
from typing import TypeVar

import anthropic
from anthropic.types import ParsedMessage
from pydantic import BaseModel, ValidationError

from ats.config import (
    LLM_EFFORT_EXTRACTION,
    LLM_EFFORT_REWRITES,
    LLM_EFFORT_SEMANTIC_FIT,
    LLM_MAX_ATTEMPTS,
    LLM_MAX_TOKENS,
    LLM_SDK_MAX_RETRIES,
    LLM_TIMEOUT_SECONDS,
    LLMSettings,
    MissingSettingError,
    load_llm_settings,
)
from ats.models import JDRequirements, RewriteSuggestions, SemanticFit

PROMPTS_DIR = Path(__file__).parent / "prompts"

OutputT = TypeVar("OutputT", bound=BaseModel)

# The XML-style tags that mark each part of a request.
_INPUT_TAGS = (
    "job_description",
    "extracted_requirements",
    "skills_found_in_cv",
    "skills_missing_from_cv",
    "cv",
)
# Finds any of those tags written inside the text itself, e.g. "</cv>".
_INPUT_TAG_PATTERN = re.compile(
    r"<\s*(/?)\s*(" + "|".join(_INPUT_TAGS) + r")\s*>", re.IGNORECASE
)


class LLMError(Exception):
    """Raised when an LLM call fails. The message is safe to show to the user."""


def load_prompt(file_name: str) -> str:
    """Read a prompt template from the prompts folder."""
    return (PROMPTS_DIR / file_name).read_text(encoding="utf-8")


def create_client(settings: LLMSettings) -> anthropic.Anthropic:
    """Create an Anthropic client using the key from .env."""
    return anthropic.Anthropic(
        api_key=settings.api_key.get_secret_value(),
        timeout=LLM_TIMEOUT_SECONDS,
        max_retries=LLM_SDK_MAX_RETRIES,
    )


def extract_jd_requirements(
    jd_text: str,
    client: anthropic.Anthropic | None = None,
    settings: LLMSettings | None = None,
) -> JDRequirements:
    """Ask the LLM for the job title, seniority, skills, and responsibilities.

    `client` and `settings` can be passed in by tests; normally they are
    created from .env.
    """
    client, settings = _resolve_client(client, settings)
    user_message = _wrap_in_tag("job_description", jd_text)
    return _parse_with_retry(
        client=client,
        model=settings.model,
        system_prompt=load_prompt("extract_jd_requirements.md"),
        user_message=user_message,
        output_type=JDRequirements,
        effort=LLM_EFFORT_EXTRACTION,
    )


def assess_semantic_fit(
    cv_text: str,
    jd_text: str,
    requirements: JDRequirements,
    client: anthropic.Anthropic | None = None,
    settings: LLMSettings | None = None,
) -> SemanticFit:
    """Ask the LLM to rate how well the CV's experience and seniority fit the job.

    This sends the CV text to the Anthropic API.
    """
    client, settings = _resolve_client(client, settings)
    user_message = _join_sections(
        _wrap_in_tag("job_description", jd_text),
        _wrap_in_tag("extracted_requirements", requirements.model_dump_json(indent=2)),
        _wrap_in_tag("cv", cv_text),
    )
    return _parse_with_retry(
        client=client,
        model=settings.model,
        system_prompt=load_prompt("semantic_fit.md"),
        user_message=user_message,
        output_type=SemanticFit,
        effort=LLM_EFFORT_SEMANTIC_FIT,
    )


def suggest_rewrites(
    cv_text: str,
    jd_text: str,
    requirements: JDRequirements,
    skills_found: list[str],
    skills_missing: list[str],
    client: anthropic.Anthropic | None = None,
    settings: LLMSettings | None = None,
) -> RewriteSuggestions:
    """Ask the LLM for CV bullet rewrites that target skills the CV already shows.

    This sends the CV text to the Anthropic API. The reply is NOT yet checked
    for invented facts: always pass it through rewrites.check_suggestions().
    """
    client, settings = _resolve_client(client, settings)
    user_message = _join_sections(
        _wrap_in_tag("job_description", jd_text),
        _wrap_in_tag("extracted_requirements", requirements.model_dump_json(indent=2)),
        _wrap_in_tag("skills_found_in_cv", _bullet_list(skills_found)),
        _wrap_in_tag("skills_missing_from_cv", _bullet_list(skills_missing)),
        _wrap_in_tag("cv", cv_text),
    )
    return _parse_with_retry(
        client=client,
        model=settings.model,
        system_prompt=load_prompt("rewrite_suggestions.md"),
        user_message=user_message,
        output_type=RewriteSuggestions,
        effort=LLM_EFFORT_REWRITES,
    )


def _wrap_in_tag(tag: str, text: str) -> str:
    """Put text between <tag> and </tag>, so the model treats it as material.

    A job posting could contain "</job_description>" followed by its own
    instructions, ending the section early. So any of our tags written in the
    text are turned into harmless "[cv]" / "[/cv]"-style brackets first.
    """
    safe_text = _INPUT_TAG_PATTERN.sub(r"[\1\2]", text)
    return f"<{tag}>\n{safe_text}\n</{tag}>"


def _join_sections(*sections: str) -> str:
    """Join tagged sections with a blank line between them."""
    return "\n\n".join(sections)


def _bullet_list(items: list[str]) -> str:
    """Format items one per line, or "(none)" if there are none."""
    return "\n".join(f"- {item}" for item in items) if items else "(none)"


def _resolve_client(
    client: anthropic.Anthropic | None, settings: LLMSettings | None
) -> tuple[anthropic.Anthropic, LLMSettings]:
    """Load settings from .env and create a client, unless tests passed them in."""
    if settings is None:
        try:
            settings = load_llm_settings()
        except MissingSettingError as error:
            raise LLMError(str(error)) from error
    if client is None:
        client = create_client(settings)
    return client, settings


def _parse_with_retry(
    client: anthropic.Anthropic,
    model: str,
    system_prompt: str,
    user_message: str,
    output_type: type[OutputT],
    effort: str,
) -> OutputT:
    """Call the API and validate the reply, retrying once if the JSON is invalid."""
    for _attempt in range(LLM_MAX_ATTEMPTS):
        try:
            response = _call_api(
                client, model, system_prompt, user_message, output_type, effort
            )
        except ValidationError:
            # The SDK validates the reply against `output_type` and raises
            # this when it does not fit. Ask again.
            continue

        if response.stop_reason == "refusal":
            raise LLMError(
                "The AI declined to process this request. Check that the files "
                "are a normal CV and job posting, then try again."
            )
        if response.stop_reason == "max_tokens":
            # The same request would be cut off again, so don't retry.
            raise LLMError(
                "The AI's answer was too long and got cut off. Try a shorter "
                "job description, or click Analyze again."
            )
        if response.parsed_output is not None:
            return response.parsed_output

    raise LLMError(
        "The AI's answer was not in the expected format, even after a retry. "
        "Please click Analyze again."
    )


def _call_api(
    client: anthropic.Anthropic,
    model: str,
    system_prompt: str,
    user_message: str,
    output_type: type[OutputT],
    effort: str,
) -> ParsedMessage[OutputT]:
    """Send one request, turning SDK errors into friendly LLMError messages.

    The SDK already retries rate limits, server errors, and network errors
    twice before raising, so errors that reach here are worth reporting.
    """
    try:
        return client.messages.parse(
            model=model,
            max_tokens=LLM_MAX_TOKENS,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
            output_config={"effort": effort},
            output_format=output_type,
        )
    except anthropic.AuthenticationError as error:
        raise LLMError(
            "Your Anthropic API key was rejected. "
            "Check ANTHROPIC_API_KEY in the .env file."
        ) from error
    except anthropic.PermissionDeniedError as error:
        raise LLMError(
            "Your Anthropic API key is not allowed to use this model or feature."
        ) from error
    except anthropic.NotFoundError as error:
        raise LLMError(
            f"The model '{model}' was not found. Check ANTHROPIC_MODEL in the "
            ".env file: it must be a model ID (like the ones listed in the "
            "Anthropic docs), not a display name."
        ) from error
    except anthropic.RateLimitError as error:
        raise LLMError(
            "Too many requests to the Anthropic API right now. "
            "Wait a minute, then click Analyze again."
        ) from error
    except anthropic.BadRequestError as error:
        raise LLMError(
            f"The Anthropic API rejected the request: {error.message}"
        ) from error
    except anthropic.APIStatusError as error:
        raise LLMError(
            f"The Anthropic API had a problem (error {error.status_code}). "
            "Please try again in a few minutes."
        ) from error
    except anthropic.APIConnectionError as error:
        # Also covers timeouts: APITimeoutError is a subclass of this.
        raise LLMError(
            "Could not reach the Anthropic API. Check your internet connection "
            "and try again."
        ) from error
    except anthropic.APIError as error:
        # Any other SDK error, e.g. a reply the SDK itself could not read.
        raise LLMError(
            "Something went wrong talking to the Anthropic API. Please try again."
        ) from error
