"""Tests for ats.scoring."""

import pytest

from ats.config import SCORE_WEIGHTS, ScoreWeights
from ats.models import (
    KeywordMatchResult,
    ScoreBreakdown,
    ScoreComponent,
    SemanticFit,
    Skill,
    SkillMatch,
    SubScore,
)
from ats.scoring import (
    PREFERRED_NAME,
    REQUIRED_NAME,
    SEMANTIC_NAME,
    compute_final_score,
    keyword_percent,
    semantic_fit_score,
)


def matches(found: int, missing: int) -> list[SkillMatch]:
    """Build `found` matched and `missing` unmatched skills."""
    result = [
        SkillMatch(skill=Skill(name=f"found {i}"), matched=True, method="exact")
        for i in range(found)
    ]
    result += [
        SkillMatch(skill=Skill(name=f"missing {i}"), matched=False)
        for i in range(missing)
    ]
    return result


def fit(responsibilities: int, seniority: int) -> SemanticFit:
    """Build a SemanticFit with the given sub-scores."""
    return SemanticFit(
        responsibilities_fit=SubScore(score=responsibilities, reason="r"),
        seniority_fit=SubScore(score=seniority, reason="s"),
    )


def component(breakdown: ScoreBreakdown, name: str) -> ScoreComponent:
    """Find a breakdown row by name."""
    return next(row for row in breakdown.components if row.name == name)


# --- building blocks --------------------------------------------------------


def test_default_weights_add_up_to_one() -> None:
    total = (
        SCORE_WEIGHTS.required_keywords
        + SCORE_WEIGHTS.preferred_keywords
        + SCORE_WEIGHTS.semantic_fit
    )
    assert total == pytest.approx(1.0)


def test_keyword_percent() -> None:
    assert keyword_percent(matches(3, 1)) == 75.0
    assert keyword_percent(matches(0, 2)) == 0.0
    assert keyword_percent(matches(2, 0)) == 100.0


def test_keyword_percent_with_no_skills_is_none() -> None:
    assert keyword_percent([]) is None


def test_fuzzy_match_counts_as_full_match() -> None:
    fuzzy = [SkillMatch(skill=Skill(name="x"), matched=True, method="fuzzy")]
    assert keyword_percent(fuzzy) == 100.0


def test_semantic_fit_score_is_average_of_sub_scores() -> None:
    assert semantic_fit_score(fit(80, 60)) == 70.0
    assert semantic_fit_score(fit(75, 80)) == 77.5


# --- final score: all parts measured ----------------------------------------


def test_final_score_uses_configured_weights() -> None:
    # required 75%, preferred 50%, semantic 70
    keyword_result = KeywordMatchResult(required=matches(3, 1), preferred=matches(1, 1))
    breakdown = compute_final_score(keyword_result, fit(80, 60))

    expected = 0.45 * 75 + 0.15 * 50 + 0.40 * 70
    assert breakdown.final_score == pytest.approx(expected)  # 69.25
    assert breakdown.is_partial is False
    assert component(breakdown, REQUIRED_NAME).points == pytest.approx(0.45 * 75)
    assert component(breakdown, PREFERRED_NAME).weight == pytest.approx(0.15)
    assert component(breakdown, SEMANTIC_NAME).raw_score == 70.0
    assert all(row.note == "" for row in breakdown.components)


def test_points_add_up_to_final_score() -> None:
    keyword_result = KeywordMatchResult(required=matches(2, 3), preferred=matches(4, 1))
    breakdown = compute_final_score(keyword_result, fit(55, 90))
    total_points = sum(row.points for row in breakdown.components)
    assert breakdown.final_score == pytest.approx(total_points)


def test_perfect_and_zero_scores() -> None:
    perfect = KeywordMatchResult(required=matches(2, 0), preferred=matches(1, 0))
    assert compute_final_score(perfect, fit(100, 100)).final_score == pytest.approx(100)
    nothing = KeywordMatchResult(required=matches(0, 2), preferred=matches(0, 1))
    assert compute_final_score(nothing, fit(0, 0)).final_score == pytest.approx(0)


def test_custom_weights_are_used() -> None:
    keyword_result = KeywordMatchResult(required=matches(1, 0), preferred=matches(0, 1))
    weights = ScoreWeights(
        required_keywords=0.5, preferred_keywords=0.5, semantic_fit=0
    )
    breakdown = compute_final_score(keyword_result, fit(0, 0), weights)
    assert breakdown.final_score == pytest.approx(50)


# --- final score: parts that cannot be measured -----------------------------


def test_no_preferred_skills_shares_out_its_weight() -> None:
    keyword_result = KeywordMatchResult(required=matches(1, 1), preferred=[])
    breakdown = compute_final_score(keyword_result, fit(80, 80))

    preferred = component(breakdown, PREFERRED_NAME)
    assert preferred.raw_score is None
    assert preferred.weight == 0
    assert preferred.points == 0
    assert "shared out" in preferred.note

    # 0.45 and 0.40 are scaled up so they add to 1: 0.45/0.85 and 0.40/0.85.
    assert component(breakdown, REQUIRED_NAME).weight == pytest.approx(0.45 / 0.85)
    assert component(breakdown, SEMANTIC_NAME).weight == pytest.approx(0.40 / 0.85)
    expected = 50 * 0.45 / 0.85 + 80 * 0.40 / 0.85
    assert breakdown.final_score == pytest.approx(expected)


def test_missing_semantic_fit_gives_partial_keyword_only_score() -> None:
    keyword_result = KeywordMatchResult(required=matches(3, 1), preferred=matches(1, 1))
    breakdown = compute_final_score(keyword_result, None)

    assert breakdown.is_partial is True
    semantic = component(breakdown, SEMANTIC_NAME)
    assert semantic.raw_score is None
    assert "AI rating unavailable" in semantic.note
    expected = 75 * 0.45 / 0.60 + 50 * 0.15 / 0.60
    assert breakdown.final_score == pytest.approx(expected)


def test_used_weights_always_add_up_to_one() -> None:
    keyword_result = KeywordMatchResult(required=[], preferred=matches(1, 2))
    breakdown = compute_final_score(keyword_result, fit(40, 40))
    assert sum(row.weight for row in breakdown.components) == pytest.approx(1.0)


def test_nothing_measurable_gives_no_score() -> None:
    breakdown = compute_final_score(KeywordMatchResult(required=[], preferred=[]), None)
    assert breakdown.final_score is None
    assert all(row.weight == 0 for row in breakdown.components)


def test_base_weights_are_kept_for_display() -> None:
    breakdown = compute_final_score(
        KeywordMatchResult(required=matches(1, 0), preferred=[]), None
    )
    assert [row.base_weight for row in breakdown.components] == [0.45, 0.15, 0.40]
