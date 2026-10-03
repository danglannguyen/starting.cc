"""Pydantic models for all data passed between steps."""

from typing import Literal

from pydantic import BaseModel, Field


class ParsedDocument(BaseModel):
    """Text extracted from a CV or job description, ready for matching."""

    original_text: str = Field(description="Extracted text, kept as-is for display.")
    normalized_text: str = Field(
        description="Lowercased, whitespace-collapsed copy used for matching."
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Problems the user should know about, e.g. text in text boxes.",
    )


class Skill(BaseModel):
    """A skill from the job description, plus other ways of writing it."""

    name: str = Field(description='Main form, e.g. "Lean Six Sigma".')
    synonyms: list[str] = Field(
        default_factory=list, description='Alternatives, e.g. ["LSS"].'
    )


class SkillMatch(BaseModel):
    """Whether one skill was found in the CV, and how."""

    skill: Skill
    matched: bool
    method: Literal["exact", "fuzzy"] | None = Field(
        default=None, description="How it was found; None when missing."
    )
    matched_term: str | None = Field(
        default=None, description="The skill name or synonym that was found."
    )
    cv_text_found: str | None = Field(
        default=None, description="The (normalized) CV text that matched it."
    )
    score: float | None = Field(
        default=None, description="Similarity 0-100: 100 for exact matches."
    )


class KeywordMatchResult(BaseModel):
    """Match results for every required and preferred skill."""

    required: list[SkillMatch]
    preferred: list[SkillMatch]

    @property
    def matched_required(self) -> list[SkillMatch]:
        """Required skills found in the CV."""
        return [match for match in self.required if match.matched]

    @property
    def missing_required(self) -> list[SkillMatch]:
        """Required skills not found in the CV."""
        return [match for match in self.required if not match.matched]

    @property
    def matched_preferred(self) -> list[SkillMatch]:
        """Preferred (nice-to-have) skills found in the CV."""
        return [match for match in self.preferred if match.matched]

    @property
    def missing_preferred(self) -> list[SkillMatch]:
        """Preferred (nice-to-have) skills not found in the CV."""
        return [match for match in self.preferred if not match.matched]

    @property
    def found_skills(self) -> list[Skill]:
        """Every skill found in the CV, required first."""
        return [match.skill for match in self.matched_required + self.matched_preferred]

    @property
    def missing_skills(self) -> list[Skill]:
        """Every skill not found in the CV, required first."""
        return [match.skill for match in self.missing_required + self.missing_preferred]


class JDRequirements(BaseModel):
    """What the LLM extracts from a job description (step B)."""

    job_title: str = Field(description="The job title as written in the posting.")
    seniority: str = Field(
        description='Seniority level, e.g. "junior", "mid", "senior", "lead", '
        'or "not stated".'
    )
    required_skills: list[Skill] = Field(
        description="Skills, tools, or qualifications the posting says are required."
    )
    preferred_skills: list[Skill] = Field(
        description="Skills the posting calls preferred, desirable, or nice to have."
    )
    key_responsibilities: list[str] = Field(
        description="The main duties of the role, one short sentence each."
    )


class SubScore(BaseModel):
    """One part of the semantic fit rating, with the reason for it."""

    score: int = Field(ge=0, le=100, description="0 = no fit, 100 = perfect fit.")
    reason: str = Field(description="One or two sentences citing evidence from the CV.")


class SemanticFit(BaseModel):
    """The LLM's rating of how well the CV's experience fits the job (step D)."""

    responsibilities_fit: SubScore = Field(
        description="How well the CV's experience matches the key responsibilities."
    )
    seniority_fit: SubScore = Field(
        description="How well the CV's level of experience matches the seniority."
    )


class ScoreComponent(BaseModel):
    """One row of the final score breakdown."""

    name: str
    raw_score: float | None = Field(
        description="0-100, or None when this part could not be measured."
    )
    base_weight: float = Field(description="Weight from config.py (0-1).")
    weight: float = Field(
        description="Weight actually used, after sharing out unmeasured parts."
    )
    points: float = Field(description="raw_score x weight: points added to the total.")
    note: str = ""


class ScoreBreakdown(BaseModel):
    """The final score and every part that went into it (step E)."""

    final_score: float | None = Field(
        description="0-100, or None when no part could be measured."
    )
    components: list[ScoreComponent]
    is_partial: bool = Field(
        description="True when the semantic fit is missing (keyword-only score)."
    )


class RewriteSuggestion(BaseModel):
    """One CV bullet with a suggested rewrite (step F)."""

    original: str = Field(
        description="The bullet copied exactly, word for word, from the CV."
    )
    suggested: str = Field(
        description="The rewritten bullet. Uses only facts in the original; "
        'puts "[add % / number]"-style placeholders where a metric would help.'
    )
    jd_keyword: str = Field(
        description="The one skill from the 'found in CV' list this rewrite targets."
    )
    why: str = Field(description="One short sentence on what the rewrite improves.")


class RewriteSuggestions(BaseModel):
    """The LLM's list of rewrite suggestions."""

    suggestions: list[RewriteSuggestion] = Field(
        description="Up to 8 suggestions, most relevant to the job first."
    )


class DroppedSuggestion(BaseModel):
    """A suggestion removed by a safety check, and why."""

    suggestion: RewriteSuggestion
    reason: str


class CheckedRewrites(BaseModel):
    """Suggestions after the no-invented-facts checks."""

    kept: list[RewriteSuggestion]
    dropped: list[DroppedSuggestion]


class Notice(BaseModel):
    """A message for the user produced while analyzing, e.g. an AI step failing."""

    level: Literal["error", "warning", "info"]
    text: str


class AnalysisOutcome(BaseModel):
    """Everything one click of "Analyze" produced, kept so it can be redrawn."""

    cv: ParsedDocument
    jd: ParsedDocument
    notices: list[Notice] = Field(default_factory=list)
    requirements: JDRequirements | None = None
    keyword_result: KeywordMatchResult | None = Field(
        default=None, description="None when there were no skills to check at all."
    )
    semantic_fit: SemanticFit | None = None
    rewrites: CheckedRewrites | None = None
