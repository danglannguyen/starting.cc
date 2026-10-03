# CLAUDE.md — CV Pre-Screen ATS

## 1. What we are building
A **local Streamlit web app** that runs on a Windows PC. The user uploads **their own CV (.docx)** and a **job description** (PDF, .docx, .txt, or pasted text). The app returns:

1. A **match score (0–100)** with a breakdown that explains it
2. A **missing-keywords list** (required vs. nice-to-have skills in the JD that the CV lacks)
3. **Rewrite suggestions** for CV bullets, tailored to the JD

Scope: one user, English only, runs on localhost. No accounts, no database, no cloud hosting.

**Honesty rule for the product:** real ATS products (Workday, Taleo, Greenhouse, etc.) use their own private logic. This app is an *approximation*. The UI must show a short note saying the score is an estimate, not a prediction of what any specific employer's system will do.

## 2. The user
- **Beginner programmer.** Explain what you are doing in plain language, one short paragraph per step.
- Uses **Windows + PowerShell**. Every command you give must be a PowerShell command (e.g. `.\.venv\Scripts\Activate.ps1`, not `source .venv/bin/activate`).
- Prefers accuracy over speed. If you are unsure whether a library function or parameter exists, check it in the installed package (`python -c "import x; help(x.y)"`) or tell the user to verify in the docs. **Never invent function names, parameters, or model IDs.**
- If a requirement is unclear, **ask before coding**. Do not fill gaps with assumptions.

## 3. Tech stack (do not change without asking)
| Purpose | Choice |
|---|---|
| Language | Python 3.11+ |
| UI | `streamlit` |
| Read .docx (CV and JD) | `python-docx` |
| Read PDF (JD only) | `pdfplumber` |
| Fuzzy matching | `rapidfuzz` |
| LLM | `anthropic` (official Anthropic Python SDK) |
| Data models / validating LLM JSON | `pydantic` v2 |
| Secrets/config | `python-dotenv` + `.env` file |
| Tests | `pytest` |
| Lint + format | `ruff` |
| Environment | built-in `venv` + `requirements.txt` with pinned versions |

Do not add other dependencies (LangChain, spaCy, databases, Docker, React, etc.) unless the user approves. If you think one is needed, explain why and ask.

## 4. LLM settings
- Model: read from `.env` → `ANTHROPIC_MODEL=<MODEL_ID>`. Use **exactly** that value. Never hard-code or substitute a different model name.
- API key: `ANTHROPIC_API_KEY` in `.env`. Never print, log, or commit it.
- Keep `.env` in `.gitignore`. Provide `.env.example` with placeholder values.
- Wrap every API call in error handling: missing key, network error, rate limit, invalid JSON. Show a friendly message in the UI; never crash with a raw traceback.
- Do **not** set `temperature`: the installed `anthropic` 1.x SDK has no such parameter and current models reject it. Make results repeatable with structured outputs (`messages.parse` + a pydantic `output_format`), a low `effort` from `config.py`, and `st.cache_data`.
- Ask the model for **JSON only**, then validate with a pydantic model. On validation failure, retry once, then show an error.
- The API is called **only when the user clicks "Analyze"** — not on every widget change. Cache results with `st.cache_data` keyed on the file contents.

## 5. Hybrid scoring design
Keep deterministic logic and LLM logic in separate modules so each can be tested on its own.

**Step A — Parse (deterministic)**
- CV .docx: extract text from paragraphs, **tables**, and **headers/footers** (in python-docx, `Document.paragraphs` does not include table text). Note: text inside Word text boxes may not be exposed by python-docx's normal API — if text appears missing, flag it to the user as a possible ATS parsing risk rather than silently ignoring it.
- JD: PDF via pdfplumber, .docx via python-docx, .txt read as UTF-8, or pasted text.
- Normalize: lowercase copy for matching, collapse whitespace, keep original text for display.

**Step B — Extract JD requirements (LLM)**
- LLM returns JSON: `required_skills`, `preferred_skills`, `job_title`, `seniority`, `key_responsibilities`. Each skill includes common synonyms/abbreviations (e.g. "Lean Six Sigma" ↔ "LSS").

**Step C — Keyword match (deterministic)**
- Match each skill + synonyms against the CV text: exact/word-boundary regex first, then `rapidfuzz` with a threshold set in `config.py`.
- Output: matched / missing, split into required vs. preferred.

**Step D — Semantic fit (LLM)**
- LLM rates how well the CV's experience matches the JD responsibilities and seniority (0–100), with a short reason for each sub-score.

**Step E — Final score**
- Weighted combination, weights in `config.py` (starting point: required keywords 45%, preferred keywords 15%, semantic fit 40%). Show every component in the UI so the score is never a black box.

**Step F — Rewrite suggestions (LLM)**
- For up to ~8 CV bullets most relevant to the JD, return: original bullet, suggested rewrite, which JD keyword it addresses.
- **Strict rule: never invent facts, employers, titles, dates, numbers, or achievements.** If a rewrite would benefit from a metric the CV does not contain, insert a visible placeholder like `[add % / number]` for the user to fill in.
- Do not suggest adding a skill the CV gives no evidence of. Instead list it under "missing — add only if true".

## 6. Project structure
```
cv-ats/
├── app.py                 # Streamlit UI only — no business logic
├── ats/
│   ├── __init__.py
│   ├── config.py          # weights, thresholds, loads .env
│   ├── models.py          # pydantic models for all data passed between steps
│   ├── parsing.py         # docx / pdf / txt → text
│   ├── keywords.py        # deterministic matching
│   ├── llm.py             # all Anthropic API calls live here, nowhere else
│   ├── scoring.py         # combines results into final score
│   ├── rewrites.py        # deterministic no-invented-facts checks for step F
│   └── prompts/           # LLM prompt templates as .md/.txt files
├── tests/
│   ├── fixtures/          # SYNTHETIC sample CVs/JDs only — no real personal data
│   ├── helpers.py         # builds synthetic .docx/.pdf files in memory
│   ├── test_parsing.py
│   ├── test_keywords.py
│   ├── test_scoring.py
│   ├── test_rewrites.py
│   └── test_llm.py        # LLM calls mocked with a fake client
├── .streamlit/
│   └── config.toml        # localhost only, no usage statistics
├── .env.example
├── .gitignore
├── pyproject.toml         # tool settings only (pytest path)
├── requirements.txt
└── README.md              # Windows setup + run steps for a beginner
```

## 7. Coding style
- Type hints on every function. Short docstring on every public function.
- Functions small and single-purpose. No logic inside `app.py` beyond UI wiring.
- Pass pydantic models between steps, not loose dicts.
- Use `pathlib.Path`, not string paths. Open text files with `encoding="utf-8"`.
- No magic numbers — thresholds and weights go in `config.py`.
- Clear, beginner-readable variable names. Comments explain *why*, not *what*.
- Run `ruff check .` and `ruff format .` before saying a task is done.

## 8. Testing
- Deterministic modules (`parsing`, `keywords`, `scoring`) must have pytest tests.
- LLM calls are **mocked** in tests — tests must pass with no API key and no internet.
- Run `pytest` after every change and report the result honestly. If a test fails, say so; do not claim success.

## 9. Privacy
- CVs contain personal data. Do not log CV or JD text to files or the console.
- Do not save uploads to disk unless the user turns on a feature for that later.
- The UI should state that CV text is sent to the Anthropic API when "Analyze" is clicked.

## 10. How to work with me
1. Before a big change, give a short plan (3–6 bullets) and wait for approval.
2. Build in small, runnable steps. Suggested order:
   parsing → keyword matching → Streamlit UI with keyword results only → LLM extraction → semantic score → rewrite suggestions → polish.
3. After each step, give the exact PowerShell commands to run it, and what I should expect to see.
4. When something fails, show the error, explain it in plain language, then fix it.
5. If you are not certain about a library API, model behavior, or ATS fact, say so explicitly.
