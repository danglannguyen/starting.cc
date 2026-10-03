"""Combine keyword and semantic results into the final score."""

from ats.config import SCORE_WEIGHTS, ScoreWeights
from ats.models import (
    KeywordMatchResult,
    ScoreBreakdown,
    ScoreComponent,
    SemanticFit,
    SkillMatch,
)

REQUIRED_NAME = "Required skills"
PREFERRED_NAME = "Nice-to-have skills"
SEMANTIC_NAME = "Experience fit (AI)"


def keyword_percent(matches: list[SkillMatch]) -> float | None:
    """Return the % of skills found (exact or fuzzy), or None if there are none."""
    if not matches:
        return None
    found = sum(1 for match in matches if match.matched)
    return 100 * found / len(matches)


def semantic_fit_score(fit: SemanticFit) -> float:
    """Average the AI's responsibilities and seniority sub-scores."""
    return (fit.responsibilities_fit.score + fit.seniority_fit.score) / 2


def compute_final_score(
    keyword_result: KeywordMatchResult,
    semantic_fit: SemanticFit | None,
    weights: ScoreWeights = SCORE_WEIGHTS,
) -> ScoreBreakdown:
    """Combine the three parts into one 0-100 score with a full breakdown.

    A part that cannot be measured (no skills of that kind in the job
    description, or no AI rating) gets weight 0, and its weight is shared
    among the measured parts in proportion to their own weights.
    """
    parts: list[tuple[str, float | None, float, str]] = [
        (
            REQUIRED_NAME,
            keyword_percent(keyword_result.required),
            weights.required_keywords,
            "No required skills to check.",
        ),
        (
            PREFERRED_NAME,
            keyword_percent(keyword_result.preferred),
            weights.preferred_keywords,
            "No nice-to-have skills to check.",
        ),
        (
            SEMANTIC_NAME,
            semantic_fit_score(semantic_fit) if semantic_fit is not None else None,
            weights.semantic_fit,
            "AI rating unavailable.",
        ),
    ]

    measured_weight = sum(weight for _, raw, weight, _ in parts if raw is not None)
    components: list[ScoreComponent] = []
    for name, raw_score, base_weight, missing_note in parts:
        if raw_score is None or measured_weight == 0:
            components.append(
                ScoreComponent(
                    name=name,
                    raw_score=None,
                    base_weight=base_weight,
                    weight=0.0,
                    points=0.0,
                    note=f"{missing_note} Its weight was shared out among the other parts.",
                )
            )
            continue
        weight = base_weight / measured_weight
        components.append(
            ScoreComponent(
                name=name,
                raw_score=raw_score,
                base_weight=base_weight,
                weight=weight,
                points=raw_score * weight,
            )
        )

    final_score = (
        sum(component.points for component in components) if measured_weight else None
    )
    return ScoreBreakdown(
        final_score=final_score,
        components=components,
        is_partial=semantic_fit is None,
    )
