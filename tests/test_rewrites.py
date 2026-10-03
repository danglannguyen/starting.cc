"""Tests for ats.rewrites (no-invented-facts checks). All CV text is synthetic."""

from ats.config import REWRITE_MAX_SUGGESTIONS
from ats.models import (
    KeywordMatchResult,
    ParsedDocument,
    RewriteSuggestion,
    RewriteSuggestions,
    Skill,
    SkillMatch,
)
from ats.parsing import normalize_text
from ats.rewrites import (
    added_missing_skill,
    check_suggestions,
    find_numbers,
    invented_numbers,
    is_in_cv,
)

CV_TEXT = (
    "Jane Example (synthetic)\n"
    "Analysed weekly sales data in SQL to find delays.\n"
    "• Supported the team on 3 Lean Six Sigma projects in 2022.\n"
    "Reduced invoice errors by 15% through process mapping workshops.\n"
    "Built “Power BI” dashboards for the finance team."
)
CV = ParsedDocument(original_text=CV_TEXT, normalized_text=normalize_text(CV_TEXT))

KEYWORDS = KeywordMatchResult(
    required=[
        SkillMatch(skill=Skill(name="SQL"), matched=True, method="exact"),
        SkillMatch(skill=Skill(name="Kubernetes", synonyms=["K8s"]), matched=False),
    ],
    preferred=[SkillMatch(skill=Skill(name="Tableau"), matched=False)],
)
MISSING = [Skill(name="Kubernetes", synonyms=["K8s"]), Skill(name="Tableau")]


def suggestion(
    original: str, suggested: str, jd_keyword: str = "SQL"
) -> RewriteSuggestion:
    """Build a suggestion with a fixed 'why'."""
    return RewriteSuggestion(
        original=original, suggested=suggested, jd_keyword=jd_keyword, why="w"
    )


GOOD = suggestion(
    "Analysed weekly sales data in SQL to find delays.",
    "SQL analysis of weekly sales data to pinpoint delays, cutting [add % / time].",
)


# --- numbers ----------------------------------------------------------------


def test_find_numbers_handles_separators_and_decimals() -> None:
    assert find_numbers("Saved $1,200 in 2023, up 3.5%") == {"1200", "2023", "3.5"}


def test_find_numbers_ignores_placeholders() -> None:
    assert find_numbers("Cut costs by [add % / number] over [add 12 months?]") == set()


def test_kept_numbers_are_not_invented() -> None:
    original = "Reduced invoice errors by 15% in 2022."
    assert invented_numbers(original, "Cut invoice errors 15% (2022).") == set()


def test_new_number_is_invented() -> None:
    original = "Reduced invoice errors through process mapping."
    assert invented_numbers(original, "Reduced invoice errors by 30%.") == {"30"}


def test_changed_number_is_invented() -> None:
    assert invented_numbers("Led 3 projects.", "Led 5 projects.") == {"5"}


def test_thousands_separator_difference_is_not_invented() -> None:
    assert (
        invented_numbers("Processed 1200 orders.", "Processed 1,200 orders.") == set()
    )


# --- original bullet must be in the CV ---------------------------------------


def test_exact_bullet_is_found() -> None:
    assert is_in_cv(
        "Analysed weekly sales data in SQL to find delays.", CV.normalized_text
    )


def test_bullet_symbol_and_spacing_differences_are_ignored() -> None:
    bullet = "- Supported the  team on 3 Lean Six Sigma projects in 2022."
    assert is_in_cv(bullet, CV.normalized_text)


def test_tiny_difference_like_missing_full_stop_or_quotes_is_accepted() -> None:
    assert is_in_cv(
        'Built "Power BI" dashboards for the finance team', CV.normalized_text
    )


def test_invented_original_is_not_found() -> None:
    bullet = "Managed a team of 12 analysts across three regions."
    assert not is_in_cv(bullet, CV.normalized_text)


def test_reworded_original_is_not_found() -> None:
    bullet = "Led 3 Lean Six Sigma projects in 2022."  # CV says "Supported the team on"
    assert not is_in_cv(bullet, CV.normalized_text)


def test_short_original_must_match_exactly() -> None:
    assert not is_in_cv("Analysed sales", CV.normalized_text)
    assert is_in_cv("weekly sales data", CV.normalized_text)


def test_empty_original_is_not_found() -> None:
    assert not is_in_cv("  • ", CV.normalized_text)


# --- missing skills must not be added ----------------------------------------


def test_targeting_a_missing_skill_is_caught() -> None:
    bad = suggestion(GOOD.original, GOOD.suggested, jd_keyword="Tableau")
    assert added_missing_skill(bad, MISSING) == "Tableau"


def test_targeting_a_missing_skill_synonym_is_caught() -> None:
    bad = suggestion(GOOD.original, GOOD.suggested, jd_keyword="k8s")
    assert added_missing_skill(bad, MISSING) == "Kubernetes"


def test_mentioning_a_missing_skill_in_the_rewrite_is_caught() -> None:
    bad = suggestion(GOOD.original, "SQL and Tableau analysis of weekly sales data.")
    assert added_missing_skill(bad, MISSING) == "Tableau"


def test_good_suggestion_adds_no_missing_skill() -> None:
    assert added_missing_skill(GOOD, MISSING) is None


# --- check_suggestions --------------------------------------------------------


def test_good_suggestion_is_kept() -> None:
    result = check_suggestions(RewriteSuggestions(suggestions=[GOOD]), CV, KEYWORDS)
    assert result.kept == [GOOD]
    assert result.dropped == []


def test_each_rule_break_is_dropped_with_reason() -> None:
    invented_original = suggestion(
        "Managed a team of 12 analysts.", "Led a team of 12 analysts."
    )
    invented_number = suggestion(
        "Analysed weekly sales data in SQL to find delays.",
        "SQL analysis of weekly sales data, cutting delays by 40%.",
    )
    missing_skill = suggestion(
        "Analysed weekly sales data in SQL to find delays.",
        "Analysed weekly sales data in SQL and Tableau to find delays.",
    )
    empty = suggestion("Analysed weekly sales data in SQL to find delays.", "   ")

    result = check_suggestions(
        RewriteSuggestions(
            suggestions=[invented_original, GOOD, invented_number, missing_skill, empty]
        ),
        CV,
        KEYWORDS,
    )
    assert result.kept == [GOOD]
    reasons = [item.reason for item in result.dropped]
    assert "could not be found in your CV" in reasons[0]
    assert "added numbers not in your original bullet: 40" in reasons[1]
    assert "'Tableau'" in reasons[2]
    assert "empty" in reasons[3]


def test_suggestions_over_the_limit_are_dropped_with_note() -> None:
    many = RewriteSuggestions(suggestions=[GOOD] * (REWRITE_MAX_SUGGESTIONS + 2))
    result = check_suggestions(many, CV, KEYWORDS)
    assert len(result.kept) == REWRITE_MAX_SUGGESTIONS
    assert len(result.dropped) == 2
    assert all("extra skipped" in item.reason for item in result.dropped)


def test_no_suggestions_gives_empty_result() -> None:
    result = check_suggestions(RewriteSuggestions(suggestions=[]), CV, KEYWORDS)
    assert result.kept == []
    assert result.dropped == []
