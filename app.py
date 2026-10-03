"""Streamlit UI only: no business logic."""

import logging
import re
import traceback
from concurrent.futures import Future, ThreadPoolExecutor
from typing import TypeVar

import streamlit as st

from ats.config import (
    CACHE_MAX_ENTRIES,
    CACHE_TTL_SECONDS,
    LLMSettings,
    MissingSettingError,
    load_llm_settings,
)
from ats.keywords import match_keywords, merge_skills, parse_skill_lines
from ats.llm import (
    LLMError,
    assess_semantic_fit,
    extract_jd_requirements,
    suggest_rewrites,
)
from ats.models import (
    AnalysisOutcome,
    CheckedRewrites,
    JDRequirements,
    KeywordMatchResult,
    Notice,
    ParsedDocument,
    RewriteSuggestions,
    ScoreBreakdown,
    SemanticFit,
    SkillMatch,
)
from ats.parsing import (
    SUPPORTED_JD_EXTENSIONS,
    ParsingError,
    read_cv_docx,
    read_jd_file,
    read_jd_text,
)
from ats.rewrites import PLACEHOLDER, check_suggestions
from ats.scoring import compute_final_score

JD_SOURCE_UPLOAD = "Upload a file"
JD_SOURCE_PASTE = "Paste text"
SKILLS_PLACEHOLDER = "Optional, e.g.\nLean Six Sigma | LSS\nSQL"
# Characters with special meaning in Streamlit markdown ("$" starts LaTeX).
MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+!|~<>$:-])")

# Session keys: the last analysis (redrawn on every rerun) and a counter that,
# when changed, gives the upload boxes new keys so they start empty.
OUTCOME_KEY = "analysis_outcome"
FORM_VERSION_KEY = "form_version"

ResultT = TypeVar("ResultT")

logger = logging.getLogger(__name__)

# Shared settings for every cached AI call. The limits stop CV text staying
# in memory for as long as the app runs.
CACHE_OPTIONS = {
    "show_spinner": False,
    "ttl": CACHE_TTL_SECONDS,
    "max_entries": CACHE_MAX_ENTRIES,
}


@st.cache_data(**CACHE_OPTIONS)
def cached_jd_requirements(
    jd_text: str, model: str, _settings: LLMSettings
) -> JDRequirements:
    """Extract JD requirements once per (job description, model) pair.

    Streamlit reuses the stored result when the same text is analyzed again,
    so repeat clicks give the same answer and cost nothing. The leading "_"
    tells Streamlit not to use the settings (which hold the key) in the
    cache key; `model` is passed separately so a model change is a new entry.
    """
    return extract_jd_requirements(jd_text, settings=_settings)


@st.cache_data(**CACHE_OPTIONS)
def cached_semantic_fit(
    cv_text: str,
    jd_text: str,
    requirements_json: str,
    model: str,
    _settings: LLMSettings,
) -> SemanticFit:
    """Rate the experience fit once per (CV, job description, model) set.

    The requirements are passed as JSON text so they can be part of the
    cache key.
    """
    requirements = JDRequirements.model_validate_json(requirements_json)
    return assess_semantic_fit(cv_text, jd_text, requirements, settings=_settings)


@st.cache_data(**CACHE_OPTIONS)
def cached_rewrites(
    cv_text: str,
    jd_text: str,
    requirements_json: str,
    skills_found: tuple[str, ...],
    skills_missing: tuple[str, ...],
    model: str,
    _settings: LLMSettings,
) -> RewriteSuggestions:
    """Get rewrite suggestions once per set of inputs (not yet fact-checked)."""
    requirements = JDRequirements.model_validate_json(requirements_json)
    return suggest_rewrites(
        cv_text,
        jd_text,
        requirements,
        list(skills_found),
        list(skills_missing),
        settings=_settings,
    )


def show_header() -> None:
    """Show the title and the notes the user must see before analyzing."""
    st.title("CV Pre-Screen ATS")
    st.caption("Compare your CV with a job description before you apply.")
    st.info(
        "**The score is an estimate.** Real ATS products (Workday, Taleo, "
        "Greenhouse, etc.) use their own private logic. This app approximates "
        "common keyword-matching behaviour; it cannot predict what a specific "
        "employer's system will do.",
        icon=":material/info:",
    )
    st.caption(
        ":material/lock: **Privacy:** when you click **Analyze**, your CV text "
        "and the job description text are sent to the Anthropic API to extract "
        "the job's skills, rate how well your experience fits, and suggest "
        "rewrites. Files are read in memory and never saved on this computer."
    )


def show_warnings(document: ParsedDocument, label: str) -> None:
    """Show any problems the parser found in a document."""
    for warning in document.warnings:
        st.warning(f"**{label}:** {warning}", icon=":material/warning:")


def match_rows(matches: list[SkillMatch]) -> list[dict[str, str]]:
    """Turn match results into table rows for display."""
    rows: list[dict[str, str]] = []
    for match in matches:
        rows.append(
            {
                "Skill": match.skill.name,
                "Synonyms": ", ".join(match.skill.synonyms),
                "Status": "✅ Found" if match.matched else "❌ Missing",
                "How": match.method or "",
                "Matched term": match.matched_term or "",
                "Found in CV as": match.cv_text_found or "",
                "Score": f"{match.score:.0f}" if match.score is not None else "",
            }
        )
    return rows


def show_match_table(title: str, matches: list[SkillMatch]) -> None:
    """Show one table of skills, or a note if there are none."""
    st.subheader(title)
    if not matches:
        st.caption("No skills of this kind to check.")
        return
    st.dataframe(match_rows(matches), hide_index=True)


def show_jd_summary(requirements: JDRequirements) -> None:
    """Show what the AI read from the job description."""
    with st.expander("What the AI found in the job description", expanded=True):
        # AI text is escaped so it displays literally (e.g. "$" is not maths).
        st.markdown(
            f"**Job title:** {escape_markdown(requirements.job_title)}  \n"
            f"**Seniority:** {escape_markdown(requirements.seniority)}"
        )
        if requirements.key_responsibilities:
            st.markdown("**Key responsibilities:**")
            st.markdown(
                "\n".join(
                    f"- {escape_markdown(duty)}"
                    for duty in requirements.key_responsibilities
                )
            )


def show_results(
    cv: ParsedDocument, jd: ParsedDocument, result: KeywordMatchResult
) -> None:
    """Show keyword match counts, tables, missing skills, and extracted text."""
    required_column, preferred_column = st.columns(2)
    required_column.metric(
        "Required skills found",
        f"{len(result.matched_required)} of {len(result.required)}",
    )
    preferred_column.metric(
        "Nice-to-have skills found",
        f"{len(result.matched_preferred)} of {len(result.preferred)}",
    )

    missing = result.missing_required + result.missing_preferred
    if missing:
        # Code spans (`...`) already show their text literally.
        missing_names = ", ".join(
            f"`{match.skill.name.replace('`', '')}`" for match in missing
        )
        st.markdown(f"**Missing (add to your CV only if true):** {missing_names}")

    show_match_table("Required skills", result.required)
    show_match_table("Nice-to-have skills", result.preferred)

    # Lets the user check what the parser actually read, e.g. missing sections.
    with st.expander("Text extracted from your CV"):
        st.text(cv.original_text)
    with st.expander("Text extracted from the job description"):
        st.text(jd.original_text)


def breakdown_rows(breakdown: ScoreBreakdown) -> list[dict[str, str]]:
    """Turn the score breakdown into table rows for display."""
    rows: list[dict[str, str]] = []
    for component in breakdown.components:
        measured = component.raw_score is not None
        rows.append(
            {
                "Part": component.name,
                "Score (0-100)": f"{component.raw_score:.0f}" if measured else "n/a",
                "Weight set": f"{component.base_weight:.0%}",
                "Weight used": f"{component.weight:.0%}",
                "Points": f"{component.points:.1f}",
                "Note": component.note,
            }
        )
    return rows


def show_score(breakdown: ScoreBreakdown, semantic_fit: SemanticFit | None) -> None:
    """Show the final score, how it was built, and the AI's reasons."""
    st.header("Match score")
    if breakdown.final_score is None:
        st.warning(
            "No score could be calculated: there were no skills to check and "
            "the AI rating is unavailable.",
            icon=":material/warning:",
        )
        return

    st.metric("Estimated match score", f"{breakdown.final_score:.0f} / 100")
    st.caption(
        "An estimate from this app's own formula, not a prediction of any "
        "employer's ATS. Each part's points = its score × the weight used."
    )
    if breakdown.is_partial:
        st.warning(
            "**Partial score:** the AI experience rating is unavailable, so this "
            "score uses keyword matching only.",
            icon=":material/warning:",
        )
    st.dataframe(breakdown_rows(breakdown), hide_index=True)

    if semantic_fit is not None:
        st.markdown(
            f"**Responsibilities fit: {semantic_fit.responsibilities_fit.score}/100.** "
            f"{escape_markdown(semantic_fit.responsibilities_fit.reason)}"
        )
        st.markdown(
            f"**Seniority fit: {semantic_fit.seniority_fit.score}/100.** "
            f"{escape_markdown(semantic_fit.seniority_fit.reason)}"
        )
        st.caption("Experience fit (AI) = the average of these two ratings.")


def escape_markdown(text: str) -> str:
    """Show text literally: stop "$", "*", "_", etc. acting as formatting."""
    return MARKDOWN_SPECIAL.sub(r"\\\1", text)


def highlight_placeholders(text: str) -> str:
    """Escape text and give "[add ...]" placeholders an orange background."""
    parts: list[str] = []
    position = 0
    for placeholder in PLACEHOLDER.finditer(text):
        parts.append(escape_markdown(text[position : placeholder.start()]))
        inner = escape_markdown(placeholder.group(0)[1:-1])
        parts.append(f":orange-background[**{inner}**]")
        position = placeholder.end()
    parts.append(escape_markdown(text[position:]))
    return "".join(parts)


def show_rewrites(checked: CheckedRewrites) -> None:
    """Show checked rewrite suggestions as original vs. suggested, side by side."""
    st.header("Rewrite suggestions")
    st.caption(
        "Wording ideas for bullets already in your CV. Each suggestion was "
        "checked in code: the original must be in your CV, and the rewrite may "
        "not add numbers or job skills your CV does not show. The checks cannot "
        "catch every reworded claim, so **read each one and use it only if it "
        "is true.** :orange-background[**Highlighted**] parts are placeholders "
        "for you to fill in with real figures, or delete."
    )
    if not checked.kept:
        st.info("No rewrite suggestions passed the checks for this CV and job.")

    for suggestion in checked.kept:
        with st.container(border=True):
            original_column, suggested_column = st.columns(2)
            original_column.markdown("**Original**")
            original_column.markdown(escape_markdown(suggestion.original))
            suggested_column.markdown("**Suggested**")
            suggested_column.markdown(highlight_placeholders(suggestion.suggested))
            st.caption(
                f"Targets: **{escape_markdown(suggestion.jd_keyword)}**. "
                f"{escape_markdown(suggestion.why)}"
            )

    if checked.dropped:
        with st.expander(
            f"{len(checked.dropped)} suggestion(s) removed by the no-invented-facts checks"
        ):
            for item in checked.dropped:
                st.markdown(
                    f"- **{escape_markdown(item.reason)}**  \n"
                    f"  Rejected rewrite: {escape_markdown(item.suggestion.suggested)}"
                )


def get_jd_requirements(
    jd: ParsedDocument, notices: list[Notice]
) -> tuple[JDRequirements | None, LLMSettings | None]:
    """Run AI extraction. On failure, add an error notice and return Nones."""
    try:
        settings = load_llm_settings()
        with st.spinner("Reading the job description with AI..."):
            requirements = cached_jd_requirements(
                jd.original_text, settings.model, settings
            )
    except (MissingSettingError, LLMError) as error:
        notices.append(Notice(level="error", text=str(error)))
        return None, None
    return requirements, settings


def result_or_notice(
    future: Future[ResultT], failure_label: str, notices: list[Notice]
) -> ResultT | None:
    """Wait for a background AI call. On failure, add a warning and return None."""
    try:
        return future.result()
    except LLMError as error:
        notices.append(Notice(level="warning", text=f"{failure_label}: {error}"))
        return None


def get_fit_and_rewrites(
    cv: ParsedDocument,
    jd: ParsedDocument,
    requirements: JDRequirements,
    result: KeywordMatchResult,
    settings: LLMSettings,
    notices: list[Notice],
) -> tuple[SemanticFit | None, CheckedRewrites | None]:
    """Run the fit rating (step D) and rewrites (step F) at the same time.

    Neither needs the other's answer, so running them side by side saves
    the time of one whole AI call. One failing does not stop the other.
    """
    requirements_json = requirements.model_dump_json()
    skills_found = tuple(skill.name for skill in result.found_skills)
    skills_missing = tuple(skill.name for skill in result.missing_skills)
    with (
        st.spinner("Rating your experience and writing rewrite suggestions..."),
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        fit_future = pool.submit(
            cached_semantic_fit,
            cv.original_text,
            jd.original_text,
            requirements_json,
            settings.model,
            settings,
        )
        rewrites_future = pool.submit(
            cached_rewrites,
            cv.original_text,
            jd.original_text,
            requirements_json,
            skills_found,
            skills_missing,
            settings.model,
            settings,
        )
        semantic_fit = result_or_notice(
            fit_future, "The AI experience rating failed", notices
        )
        suggestions = result_or_notice(
            rewrites_future, "Rewrite suggestions failed", notices
        )

    if suggestions is None:
        return semantic_fit, None
    return semantic_fit, check_suggestions(suggestions, cv, result)


def run_analysis(
    cv: ParsedDocument, jd: ParsedDocument, required_text: str, preferred_text: str
) -> AnalysisOutcome:
    """Run every step for parsed documents and collect the results."""
    outcome = AnalysisOutcome(cv=cv, jd=jd)
    extra_required = parse_skill_lines(required_text)
    extra_preferred = parse_skill_lines(preferred_text)
    requirements, settings = get_jd_requirements(jd, outcome.notices)
    outcome.requirements = requirements

    if requirements is None:
        if not (extra_required or extra_preferred):
            return outcome
        outcome.notices.append(
            Notice(
                level="warning", text="Continuing with only the extra skills you typed."
            )
        )
        outcome.notices.append(
            Notice(
                level="info",
                text="Rewrite suggestions need the AI job analysis, which did not run.",
            )
        )

    outcome.keyword_result = match_keywords(
        merge_skills(
            requirements.required_skills if requirements else [], extra_required
        ),
        merge_skills(
            requirements.preferred_skills if requirements else [], extra_preferred
        ),
        cv,
    )
    if requirements is not None and settings is not None:
        outcome.semantic_fit, outcome.rewrites = get_fit_and_rewrites(
            cv, jd, requirements, outcome.keyword_result, settings, outcome.notices
        )
    return outcome


def show_notices(notices: list[Notice]) -> None:
    """Show messages collected during the analysis, e.g. an AI step failing."""
    for notice in notices:
        if notice.level == "error":
            st.error(notice.text, icon=":material/error:")
        elif notice.level == "warning":
            st.warning(notice.text, icon=":material/warning:")
        else:
            st.info(notice.text, icon=":material/info:")


def show_outcome(outcome: AnalysisOutcome) -> None:
    """Draw every result of an analysis."""
    show_warnings(outcome.cv, "CV")
    show_warnings(outcome.jd, "Job description")
    show_notices(outcome.notices)
    if outcome.keyword_result is None:
        return

    show_score(
        compute_final_score(outcome.keyword_result, outcome.semantic_fit),
        outcome.semantic_fit,
    )
    if outcome.requirements is not None:
        show_jd_summary(outcome.requirements)
    st.header("Keyword matching")
    show_results(outcome.cv, outcome.jd, outcome.keyword_result)
    if outcome.rewrites is not None:
        show_rewrites(outcome.rewrites)


def clear_my_data() -> None:
    """Forget all results and uploads: cache, stored analysis, upload boxes."""
    st.cache_data.clear()
    st.session_state.pop(OUTCOME_KEY, None)
    # New widget keys make Streamlit drop the uploaded files.
    st.session_state[FORM_VERSION_KEY] = st.session_state.get(FORM_VERSION_KEY, 0) + 1


def report_unexpected_error(error: Exception) -> None:
    """Show a friendly message for a bug, logging only where it happened.

    The error message itself is not logged, because it could quote CV text.
    """
    location = "".join(traceback.format_tb(error.__traceback__))
    logger.error("Unexpected %s during analysis:\n%s", type(error).__name__, location)
    st.error(
        "Something unexpected went wrong while analyzing. Please try again; "
        "if it keeps happening, try a different file.",
        icon=":material/error:",
    )


def main() -> None:
    """Build the page."""
    st.set_page_config(page_title="CV Pre-Screen ATS", layout="wide")
    show_header()

    # Outside the form so the input box switches as soon as the user picks.
    jd_source = st.radio(
        "Job description source",
        [JD_SOURCE_UPLOAD, JD_SOURCE_PASTE],
        horizontal=True,
    )

    form_version = st.session_state.get(FORM_VERSION_KEY, 0)

    # A form only reruns the app when "Analyze" is clicked, not on every edit.
    with st.form("analyze_form"):
        cv_column, jd_column = st.columns(2)
        with cv_column:
            cv_file = st.file_uploader(
                "Your CV (.docx)", type=["docx"], key=f"cv_file_{form_version}"
            )
        with jd_column:
            if jd_source == JD_SOURCE_UPLOAD:
                jd_file = st.file_uploader(
                    "Job description",
                    type=[
                        extension.lstrip(".") for extension in SUPPORTED_JD_EXTENSIONS
                    ],
                    key=f"jd_file_{form_version}",
                )
                jd_pasted = ""
            else:
                jd_file = None
                jd_pasted = st.text_area(
                    "Job description text", height=200, key=f"jd_text_{form_version}"
                )

        with st.expander("Extra skills to look for (optional)"):
            st.markdown(
                "The AI reads skills from the job description automatically. "
                "Add any it misses here: one per line, with other ways of "
                "writing it after a `|`, e.g. `Lean Six Sigma | LSS`."
            )
            required_column, preferred_column = st.columns(2)
            required_text = required_column.text_area(
                "Extra required skills", placeholder=SKILLS_PLACEHOLDER, height=150
            )
            preferred_text = preferred_column.text_area(
                "Extra nice-to-have skills", placeholder="Optional", height=150
            )
        analyze_clicked = st.form_submit_button("Analyze", type="primary")

    st.button(
        "Clear my data",
        icon=":material/delete:",
        help="Removes your files, results, and saved AI answers from memory.",
        on_click=clear_my_data,
    )

    try:
        if analyze_clicked:
            # Drop the previous result first, so a failed run never shows it.
            st.session_state.pop(OUTCOME_KEY, None)
            if cv_file is None:
                st.error("Please upload your CV (.docx).")
                return
            if jd_file is None and not jd_pasted.strip():
                st.error("Please upload or paste a job description.")
                return
            try:
                cv = read_cv_docx(cv_file.getvalue())
                if jd_file is not None:
                    jd = read_jd_file(jd_file.getvalue(), jd_file.name)
                else:
                    jd = read_jd_text(jd_pasted)
            except ParsingError as error:
                st.error(str(error), icon=":material/error:")
                return
            st.session_state[OUTCOME_KEY] = run_analysis(
                cv, jd, required_text, preferred_text
            )

        # Redrawn on every rerun, so e.g. switching the radio keeps the results.
        outcome: AnalysisOutcome | None = st.session_state.get(OUTCOME_KEY)
        if outcome is not None:
            show_outcome(outcome)
    except Exception as error:  # noqa: BLE001
        # Last line of defence: a bug shows a friendly message, not a traceback.
        report_unexpected_error(error)


main()
