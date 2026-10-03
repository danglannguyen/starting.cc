"""Tests for ats.keywords."""

import pytest

from ats.config import FUZZY_MATCH_THRESHOLD
from ats.keywords import (
    match_keywords,
    match_skill,
    merge_skills,
    parse_skill_lines,
    prepare_for_matching,
)
from ats.models import ParsedDocument, Skill
from ats.parsing import normalize_text


def make_cv(text: str) -> ParsedDocument:
    """Build a ParsedDocument from plain text, as the parser would."""
    return ParsedDocument(original_text=text, normalized_text=normalize_text(text))


def find(skill: Skill, cv_text: str) -> bool:
    """Return True if `skill` is found in `cv_text`."""
    prepared = prepare_for_matching(cv_text)
    return match_skill(skill, prepared, FUZZY_MATCH_THRESHOLD).matched


# --- exact matching ---------------------------------------------------------


def test_exact_match_is_case_insensitive() -> None:
    match = match_skill(Skill(name="SQL"), prepare_for_matching("Advanced sql"), 90)
    assert match.matched
    assert match.method == "exact"
    assert match.score == 100.0
    assert match.matched_term == "SQL"


@pytest.mark.parametrize(
    "cv_text",
    ["Tools: SQL, Python.", "(SQL)", "SQL/Python", "skills - sql"],
)
def test_exact_match_next_to_punctuation(cv_text: str) -> None:
    assert find(Skill(name="SQL"), cv_text)


def test_word_inside_another_word_is_not_matched() -> None:
    assert not find(Skill(name="SQL"), "Experience with NoSQL databases")


def test_hyphen_slash_and_space_versions_match_each_other() -> None:
    skill = Skill(name="Power BI")
    assert find(skill, "Built Power-BI dashboards")
    assert find(skill, "Built Power_BI dashboards")
    assert find(Skill(name="CI/CD"), "Set up CI CD pipelines")


def test_multi_word_phrase_matches_across_line_breaks() -> None:
    assert find(Skill(name="Process mapping"), "Led process\nmapping workshops")


def test_c_does_not_match_inside_cpp_or_csharp() -> None:
    assert not find(Skill(name="C"), "Languages: C++, C#")
    assert find(Skill(name="C++"), "Languages: C++, C#")
    assert find(Skill(name="C#"), "Languages: C++, C#")
    assert find(Skill(name="C"), "Languages: C, Go")


def test_synonym_match_reports_which_term_was_found() -> None:
    skill = Skill(name="Lean Six Sigma", synonyms=["LSS", "Six Sigma"])
    match = match_skill(skill, prepare_for_matching("LSS Green Belt, 2021"), 90)
    assert match.matched
    assert match.matched_term == "LSS"


def test_exact_synonym_beats_fuzzy_main_name() -> None:
    # "Stakeholder managment" (typo) would fuzzy-match the name, but the exact
    # synonym should win because exact matches are always tried first.
    skill = Skill(name="Stakeholder management", synonyms=["stakeholder engagement"])
    text = "Stakeholder managment; stakeholder engagement"
    match = match_skill(skill, prepare_for_matching(text), 90)
    assert match.method == "exact"
    assert match.matched_term == "stakeholder engagement"


# --- fuzzy matching ---------------------------------------------------------


def test_typo_is_matched_fuzzily() -> None:
    skill = Skill(name="Stakeholder management")
    match = match_skill(skill, prepare_for_matching("Stakeholder managment"), 90)
    assert match.matched
    assert match.method == "fuzzy"
    assert match.cv_text_found == "stakeholder managment"
    assert match.score is not None and FUZZY_MATCH_THRESHOLD <= match.score < 100


def test_plural_is_matched_fuzzily() -> None:
    assert find(Skill(name="Dashboard"), "Built dashboards for finance")


@pytest.mark.parametrize(
    ("skill_name", "cv_text"),
    [
        ("Java", "Expert in JavaScript"),
        ("SQL", "NoSQL"),
        ("Lean Six Sigma", "lean sigma six"),
    ],
)
def test_different_words_are_not_fuzzy_matched(skill_name: str, cv_text: str) -> None:
    assert not find(Skill(name=skill_name), cv_text)


def test_short_skill_is_not_fuzzy_matched() -> None:
    # "sales" vs "scales" scores 91, but short skills must match exactly.
    assert not find(Skill(name="Sales"), "Managed scales and weighing equipment")


def test_ampersand_keeps_word_whole() -> None:
    assert not find(Skill(name="R"), "Worked in the R&D team")
    assert find(Skill(name="R&D"), "Worked in the R&D team")


def test_joined_words_match_spaced_skill() -> None:
    match = match_skill(Skill(name="Power BI"), prepare_for_matching("PowerBI"), 90)
    assert match.matched
    assert match.method == "fuzzy"


def test_threshold_can_be_changed() -> None:
    # "power bi" vs "powerbi" scores about 93, so it fails at 95 and passes at 90.
    prepared = prepare_for_matching("PowerBI")
    assert not match_skill(Skill(name="Power BI"), prepared, 95).matched
    assert match_skill(Skill(name="Power BI"), prepared, 90).matched


# --- missing skills and edge cases ------------------------------------------


def test_missing_skill_has_no_match_details() -> None:
    match = match_skill(Skill(name="Kubernetes"), prepare_for_matching("SQL"), 90)
    assert not match.matched
    assert match.method is None
    assert match.matched_term is None
    assert match.cv_text_found is None
    assert match.score is None


def test_blank_synonyms_are_ignored() -> None:
    skill = Skill(name="Kubernetes", synonyms=["", "   "])
    assert not find(skill, "SQL and Python")


def test_skill_longer_than_cv_is_not_matched() -> None:
    assert not find(Skill(name="Advanced statistical process control"), "SQL")


# --- match_keywords ---------------------------------------------------------


def test_match_keywords_splits_required_and_preferred() -> None:
    cv = make_cv("Lean Six Sigma Green Belt. SQL, Excel. Built Power-BI dashboards.")
    result = match_keywords(
        required_skills=[
            Skill(name="Lean Six Sigma", synonyms=["LSS"]),
            Skill(name="SQL"),
            Skill(name="Process mapping"),
        ],
        preferred_skills=[Skill(name="Power BI"), Skill(name="Python")],
        cv=cv,
    )
    assert [m.skill.name for m in result.matched_required] == ["Lean Six Sigma", "SQL"]
    assert [m.skill.name for m in result.missing_required] == ["Process mapping"]
    assert [m.skill.name for m in result.matched_preferred] == ["Power BI"]
    assert [m.skill.name for m in result.missing_preferred] == ["Python"]


def test_match_keywords_with_no_skills_returns_empty_lists() -> None:
    result = match_keywords([], [], make_cv("Anything"))
    assert result.required == []
    assert result.preferred == []


# --- parse_skill_lines ------------------------------------------------------


def test_parse_skill_lines_reads_names_and_synonyms() -> None:
    text = "Lean Six Sigma | LSS | Six Sigma\nSQL\n"
    assert parse_skill_lines(text) == [
        Skill(name="Lean Six Sigma", synonyms=["LSS", "Six Sigma"]),
        Skill(name="SQL", synonyms=[]),
    ]


def test_parse_skill_lines_ignores_blank_lines_and_entries() -> None:
    text = "\n   \nPower BI ||  | PowerBI \n | \n"
    assert parse_skill_lines(text) == [Skill(name="Power BI", synonyms=["PowerBI"])]


def test_parse_skill_lines_handles_windows_line_endings() -> None:
    assert parse_skill_lines("SQL\r\nPython\r\n") == [
        Skill(name="SQL"),
        Skill(name="Python"),
    ]


def test_parse_skill_lines_empty_text_gives_no_skills() -> None:
    assert parse_skill_lines("") == []


# --- merge_skills -----------------------------------------------------------


def test_merge_skills_appends_new_skills_in_order() -> None:
    merged = merge_skills([Skill(name="SQL")], [Skill(name="Python")])
    assert [skill.name for skill in merged] == ["SQL", "Python"]


def test_merge_skills_combines_same_skill_and_its_synonyms() -> None:
    base = [Skill(name="Lean Six Sigma", synonyms=["LSS"])]
    extra = [Skill(name="lean-six sigma", synonyms=["lss", "Six Sigma"])]
    assert merge_skills(base, extra) == [
        Skill(name="Lean Six Sigma", synonyms=["LSS", "Six Sigma"])
    ]


def test_merge_skills_does_not_change_the_input_lists() -> None:
    base = [Skill(name="SQL", synonyms=["T-SQL"])]
    merge_skills(base, [Skill(name="SQL", synonyms=["PostgreSQL"])])
    assert base == [Skill(name="SQL", synonyms=["T-SQL"])]


def test_merge_skills_with_empty_lists() -> None:
    assert merge_skills([], []) == []


# --- KeywordMatchResult helpers ---------------------------------------------


def test_found_and_missing_skills_list_required_first() -> None:
    cv = make_cv("SQL and Python")
    result = match_keywords(
        required_skills=[Skill(name="SQL"), Skill(name="Kubernetes")],
        preferred_skills=[Skill(name="Python"), Skill(name="Tableau")],
        cv=cv,
    )
    assert [skill.name for skill in result.found_skills] == ["SQL", "Python"]
    assert [skill.name for skill in result.missing_skills] == ["Kubernetes", "Tableau"]
