"""Deterministic keyword matching between JD skills and CV text."""

import re

from rapidfuzz import fuzz, process

from ats.config import FUZZY_MATCH_THRESHOLD, FUZZY_MIN_TERM_LENGTH
from ats.models import KeywordMatchResult, ParsedDocument, Skill, SkillMatch
from ats.parsing import normalize_text

# Hyphens, slashes and underscores are treated as spaces, so that
# "Power-BI", "Power BI" and "Power_BI" all become "power bi".
_SEPARATORS = re.compile(r"[\s\-_/]+")

# Characters that belong to a word. "+", "#" and "&" keep "c++", "c#" and
# "r&d" whole, so that the skills "C" and "R" do not match inside them.
_WORD_CHAR = r"a-z0-9+#&"
_WORD_TOKEN = re.compile(rf"[{_WORD_CHAR}.]+")


def prepare_for_matching(text: str) -> str:
    """Normalize text and turn hyphens, slashes and underscores into spaces."""
    return _SEPARATORS.sub(" ", normalize_text(text)).strip()


def parse_skill_lines(text: str) -> list[Skill]:
    """Turn typed skills into Skill objects: one skill per line, synonyms
    after a pipe, e.g. "Lean Six Sigma | LSS | Six Sigma".

    Blank lines and blank entries are ignored.
    """
    skills: list[Skill] = []
    for line in text.splitlines():
        parts = [part.strip() for part in line.split("|")]
        parts = [part for part in parts if part]
        if parts:
            skills.append(Skill(name=parts[0], synonyms=parts[1:]))
    return skills


def merge_skills(base_skills: list[Skill], extra_skills: list[Skill]) -> list[Skill]:
    """Add `extra_skills` to `base_skills` without listing a skill twice.

    Skills with the same name (ignoring case, spaces, and hyphens) are
    combined, keeping every synonym once.
    """
    merged: dict[str, Skill] = {}
    for skill in [*base_skills, *extra_skills]:
        key = prepare_for_matching(skill.name)
        if not key:
            continue
        existing = merged.get(key)
        if existing is None:
            merged[key] = skill.model_copy(deep=True)
            continue
        known = {
            prepare_for_matching(term) for term in [existing.name, *existing.synonyms]
        }
        for synonym in skill.synonyms:
            if prepare_for_matching(synonym) not in known:
                existing.synonyms.append(synonym)
                known.add(prepare_for_matching(synonym))
    return list(merged.values())


def match_keywords(
    required_skills: list[Skill],
    preferred_skills: list[Skill],
    cv: ParsedDocument,
    fuzzy_threshold: float = FUZZY_MATCH_THRESHOLD,
) -> KeywordMatchResult:
    """Check every required and preferred skill against the CV text."""
    cv_text = prepare_for_matching(cv.normalized_text)
    return KeywordMatchResult(
        required=[
            match_skill(skill, cv_text, fuzzy_threshold) for skill in required_skills
        ],
        preferred=[
            match_skill(skill, cv_text, fuzzy_threshold) for skill in preferred_skills
        ],
    )


def match_skill(skill: Skill, cv_text: str, fuzzy_threshold: float) -> SkillMatch:
    """Find one skill (or any synonym) in CV text already passed through
    `prepare_for_matching`. Exact matches are preferred over fuzzy ones."""
    terms = _skill_terms(skill)

    for display_term, term in terms:
        found = _find_exact(term, cv_text)
        if found is not None:
            return SkillMatch(
                skill=skill,
                matched=True,
                method="exact",
                matched_term=display_term,
                cv_text_found=found,
                score=100.0,
            )

    cv_words = _split_words(cv_text)
    for display_term, term in terms:
        best = _find_fuzzy(term, cv_words, fuzzy_threshold)
        if best is not None:
            found, score = best
            return SkillMatch(
                skill=skill,
                matched=True,
                method="fuzzy",
                matched_term=display_term,
                cv_text_found=found,
                score=round(score, 1),
            )

    return SkillMatch(skill=skill, matched=False)


def _skill_terms(skill: Skill) -> list[tuple[str, str]]:
    """Return (as written, prepared for matching) for the name and each synonym.

    Blank terms and repeats are dropped.
    """
    terms: dict[str, str] = {}
    for display_term in [skill.name, *skill.synonyms]:
        term = prepare_for_matching(display_term)
        if term and term not in terms:
            terms[term] = display_term.strip()
    return [(display_term, term) for term, display_term in terms.items()]


def _find_exact(term: str, cv_text: str) -> str | None:
    """Return the CV text if `term` appears as a whole word or phrase, else None."""
    # Lookarounds instead of \b, because \b fails next to "+", "#" or ".",
    # e.g. at the end of "c++" or the start of ".net".
    pattern = rf"(?<![{_WORD_CHAR}]){re.escape(term)}(?![{_WORD_CHAR}])"
    found = re.search(pattern, cv_text)
    return found.group(0) if found else None


def _split_words(cv_text: str) -> list[str]:
    """Split CV text into words, dropping sentence-ending full stops."""
    words = (token.rstrip(".") for token in _WORD_TOKEN.findall(cv_text))
    return [word for word in words if word]


def _find_fuzzy(
    term: str, cv_words: list[str], threshold: float
) -> tuple[str, float] | None:
    """Find the CV phrase most similar to `term`, if it scores at least `threshold`.

    Only phrases with about the same number of words as `term` (one fewer to
    one more) are compared, so a skill is never matched against a whole
    sentence. The +/- one word catches "PowerBI" vs "Power BI".
    Terms shorter than FUZZY_MIN_TERM_LENGTH are never fuzzy-matched.
    """
    term_word_count = len(_split_words(term))
    if term_word_count == 0 or len(term) < FUZZY_MIN_TERM_LENGTH:
        return None

    phrases = [
        " ".join(cv_words[start : start + phrase_length])
        for phrase_length in range(max(1, term_word_count - 1), term_word_count + 2)
        for start in range(len(cv_words) - phrase_length + 1)
    ]
    # dict.fromkeys removes repeated phrases but keeps the first-seen order,
    # so results are the same every run.
    unique_phrases = list(dict.fromkeys(phrases))

    # fuzz.ratio compares whole strings. The default scorer (WRatio) also
    # compares substrings, which would score "java" highly against "javascript".
    best = process.extractOne(
        term, unique_phrases, scorer=fuzz.ratio, score_cutoff=threshold
    )
    if best is None:
        return None
    phrase, score, _index = best
    return phrase, score
