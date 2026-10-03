"""Deterministic no-invented-facts checks for LLM rewrite suggestions (step F).

The prompt tells the LLM not to invent anything, but these checks do not
trust it: every suggestion is verified against the CV in code, and any that
fails a check is dropped with a reason the user can see.
"""

import re

from rapidfuzz import fuzz

from ats.config import (
    FUZZY_MATCH_THRESHOLD,
    REWRITE_MAX_SUGGESTIONS,
    REWRITE_MIN_FUZZY_LENGTH,
    REWRITE_ORIGINAL_MATCH_THRESHOLD,
)
from ats.keywords import match_skill, prepare_for_matching
from ats.models import (
    CheckedRewrites,
    DroppedSuggestion,
    KeywordMatchResult,
    ParsedDocument,
    RewriteSuggestion,
    RewriteSuggestions,
    Skill,
)
from ats.parsing import normalize_text

# "[add % / number]"-style placeholders the user fills in. Anything inside
# square brackets is treated as a placeholder, not as a claimed fact.
PLACEHOLDER = re.compile(r"\[[^\[\]]*\]")

# A number: digits, optionally with thousands separators or decimals,
# e.g. "5", "2019", "1,200", "3.5".
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")

# Bullet symbols a CV line may start with, which the LLM may or may not copy.
_LEADING_BULLET = re.compile(r"^[\s•·▪●◦‣\-–—*]+")


def find_numbers(text: str) -> set[str]:
    """Return every number in `text`, ignoring placeholders in [brackets].

    Thousands separators are removed, so "1,200" and "1200" count as equal.
    """
    without_placeholders = PLACEHOLDER.sub(" ", text)
    return {number.replace(",", "") for number in _NUMBER.findall(without_placeholders)}


def invented_numbers(original: str, suggested: str) -> set[str]:
    """Return numbers in the suggestion that the original bullet does not contain."""
    return find_numbers(suggested) - find_numbers(original)


def is_in_cv(bullet: str, cv_normalized_text: str) -> bool:
    """Return True if the bullet appears in the CV text (normalized)."""
    bullet_text = normalize_text(_LEADING_BULLET.sub("", bullet))
    if not bullet_text:
        return False
    if bullet_text in cv_normalized_text:
        return True
    if len(bullet_text) < REWRITE_MIN_FUZZY_LENGTH:
        return False
    # partial_ratio finds the best-matching stretch of the CV for the bullet.
    score = fuzz.partial_ratio(bullet_text, cv_normalized_text)
    return score >= REWRITE_ORIGINAL_MATCH_THRESHOLD


def added_missing_skill(
    suggestion: RewriteSuggestion, missing: list[Skill]
) -> str | None:
    """Return the name of a missing skill the suggestion adds, or None.

    A skill counts as added if the suggestion targets it (jd_keyword) or
    mentions it in the rewrite while the original bullet does not.
    """
    target = prepare_for_matching(suggestion.jd_keyword)
    suggested_text = prepare_for_matching(suggestion.suggested)
    original_text = prepare_for_matching(suggestion.original)
    for skill in missing:
        terms = {prepare_for_matching(term) for term in [skill.name, *skill.synonyms]}
        if target in terms:
            return skill.name
        in_suggestion = match_skill(skill, suggested_text, FUZZY_MATCH_THRESHOLD)
        in_original = match_skill(skill, original_text, FUZZY_MATCH_THRESHOLD)
        if in_suggestion.matched and not in_original.matched:
            return skill.name
    return None


def check_suggestions(
    suggestions: RewriteSuggestions,
    cv: ParsedDocument,
    keyword_result: KeywordMatchResult,
) -> CheckedRewrites:
    """Keep only suggestions that pass every no-invented-facts check."""
    missing = keyword_result.missing_skills
    kept: list[RewriteSuggestion] = []
    dropped: list[DroppedSuggestion] = []

    for position, suggestion in enumerate(suggestions.suggestions):
        if position >= REWRITE_MAX_SUGGESTIONS:
            reason = f"More than {REWRITE_MAX_SUGGESTIONS} suggestions; extra skipped."
        else:
            reason = _first_failed_check(suggestion, cv, missing)
        if reason is None:
            kept.append(suggestion)
        else:
            dropped.append(DroppedSuggestion(suggestion=suggestion, reason=reason))
    return CheckedRewrites(kept=kept, dropped=dropped)


def _first_failed_check(
    suggestion: RewriteSuggestion, cv: ParsedDocument, missing: list[Skill]
) -> str | None:
    """Return why the suggestion fails, or None if it passes every check."""
    if not suggestion.suggested.strip():
        return "The suggested rewrite was empty."
    if not is_in_cv(suggestion.original, cv.normalized_text):
        return "The 'original' bullet could not be found in your CV."
    new_numbers = invented_numbers(suggestion.original, suggestion.suggested)
    if new_numbers:
        listed = ", ".join(sorted(new_numbers))
        return f"The rewrite added numbers not in your original bullet: {listed}."
    skill_name = added_missing_skill(suggestion, missing)
    if skill_name is not None:
        return (
            f"The rewrite adds '{skill_name}', which your CV gives no evidence of. "
            "It is listed under 'Missing' instead."
        )
    return None
