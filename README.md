# CV Pre-Screen ATS

A local Streamlit app that compares your CV (.docx) with a job description and gives you:

1. **A match score (0–100)** with a breakdown that shows exactly how it was calculated
2. **Missing keywords**: required vs. nice-to-have skills from the job that your CV does not show
3. **Rewrite suggestions** for your CV bullets, checked so they never invent facts

> **The score is an estimate.** Real ATS products (Workday, Taleo, Greenhouse, etc.) use their
> own private logic. This app approximates common keyword matching plus an AI judgement of fit;
> it cannot predict what a specific employer's system will do.

> **Privacy:** When you click **Analyze**, your CV text and the job description text are sent
> to the Anthropic API (to extract the job's skills, rate how well your experience fits, and
> suggest rewrites). Uploads are read in memory only: nothing is saved to disk or logged.

---

## Setup on Windows (PowerShell)

### 1. Install Python

You need Python 3.11 or newer. Check what you have:

```powershell
python --version
```

If you don't have it yet, download it from <https://www.python.org/downloads/>.

### 2. Open the project folder

```powershell
cd C:\Users\HP\claude\cv-ats
```

### 3. Create a virtual environment

A virtual environment is a private folder (`.venv`) for this project's packages, so they
don't mix with other projects.

```powershell
python -m venv .venv
```

### 4. Activate it

```powershell
.\.venv\Scripts\Activate.ps1
```

When it works, your prompt starts with `(.venv)`. You need to do this once in every new
PowerShell window.

If you see *"running scripts is disabled on this system"*, run this once, then try again:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### 5. Install the packages

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 6. Add your API key and model

```powershell
Copy-Item .env.example .env
notepad .env
```

Fill in both lines, with exactly one `=` and no quotes or spaces:

```
ANTHROPIC_API_KEY=<your key from https://console.anthropic.com/>
ANTHROPIC_MODEL=claude-sonnet-5-5
```

- `ANTHROPIC_MODEL` must be a model **ID** such as `claude-sonnet-5-5`, not a display name
  like `Sonnet 5.5`.
- `.env` is listed in `.gitignore`. Never share it or paste it anywhere.
- Recommended: in the Anthropic Console, set a monthly **spending limit** on your key.

---

## Run the app

With the virtual environment active:

```powershell
streamlit run app.py
```

Your browser opens at <http://localhost:8501>. Press `Ctrl+C` in PowerShell to stop the app.

**After changing any file in `ats/`, stop the app and start it again.** Streamlit reloads
`app.py` by itself, but keeps old copies of the `ats/` modules in memory.

### Start with a double-click (ats.exe)

Instead of typing the command above, you can double-click **`ats.exe`** in the project
folder. A black window opens and runs the app, and your browser opens on it a few seconds
later. **To stop the app, close that black window.** Keep `ats.exe` in the project folder:
it uses the `.venv` and `app.py` next to it. To put it on the Desktop, right-click
`ats.exe` → **Show more options** → **Send to** → **Desktop (create shortcut)**.

If `ats.exe` is missing, or you changed `launcher\ats_launcher.cs`, rebuild it (this uses the
C# compiler that comes with Windows; nothing extra is installed):

```powershell
powershell -ExecutionPolicy Bypass -File .\launcher\build.ps1
```

You don't need to rebuild after changing the Python code: the launcher just starts
`app.py`, so it always runs the latest version.

### Using it

1. Upload your CV (`.docx`).
2. Upload the job description (`.pdf`, `.docx`, or `.txt`) or choose **Paste text**.
3. Optional: open **Extra skills to look for** to add skills the AI might miss
   (one per line; other spellings after a `|`, e.g. `Lean Six Sigma | LSS`).
4. Click **Analyze**. Three AI steps run: first the job description is read, then the
   experience rating and the rewrite suggestions run at the same time. Repeat runs with the
   same files within an hour are instant and free, because results are cached.
5. Results stay on the page until you analyze again. **Clear my data** removes your files,
   the results, and the cached AI answers from memory.

Limits: files up to 10 MB, documents up to 50,000 characters, and job description PDFs up
to 20 pages (all set in `ats/config.py` and `.streamlit/config.toml`).

The results page shows, from top to bottom:

- **Match score** and its breakdown table, plus the AI's reasons for the experience rating
- **What the AI found in the job description** (title, seniority, responsibilities); check
  that it matches the posting
- **Keyword matching**: skill tables, the "Missing (add to your CV only if true)" list, and
  the exact text the app read from each file
- **Rewrite suggestions**: your original bullet and the suggested version, side by side

---

## How the score works

| Part | Weight | How it is measured |
|---|---|---|
| Required skills | 45% | % of the job's required skills found in your CV |
| Nice-to-have skills | 15% | % of the job's preferred skills found in your CV |
| Experience fit (AI) | 40% | Average of two AI ratings (0–100): responsibilities fit and seniority fit |

**Final score** = sum of (part score × weight). The breakdown table shows every number.

- **Finding skills:** each skill and its synonyms (e.g. "LSS" for "Lean Six Sigma") are
  searched as whole words first ("SQL" does not match "NoSQL"), then with fuzzy matching for
  typos and plurals ("managment", "dashboards"). Skills shorter than 6 characters must match
  exactly.
- **Missing parts:** if a part cannot be measured (e.g. the job lists no nice-to-have skills),
  its weight is shared among the other parts in proportion, and the table says so.
- **Partial score:** if the AI experience rating fails, you get a keyword-only score, clearly
  labelled as partial.

All weights and thresholds are in `ats/config.py`. They are reasonable starting points, not
values tested against real ATS systems.

## How rewrite suggestions are checked

The AI is told to use only facts from each original bullet and to write placeholders such as
`[add % / number]` (shown highlighted in orange) instead of inventing metrics. The app then
**checks every suggestion in code** and removes any where:

- the "original" bullet cannot be found in your CV;
- the rewrite contains a number (digits, %, $, years) that the original does not;
- the rewrite targets or mentions a skill your CV does not show;
- it is empty, or beyond the first 8.

Removed suggestions are listed with the reason, so nothing disappears silently.

**What the checks cannot catch:** reworded claims (e.g. "helped with" becoming "drove") and
numbers written as words ("five"). Read every suggestion, and use it only if it is true.

---

## Privacy and security

- **Sent to Anthropic on Analyze:** your full CV text (including name and contact details;
  the AI is told to ignore them) and the job description text. To avoid sending contact
  details, upload a copy of your CV without them; the score is not affected.
- **Not stored:** uploads stay in memory; nothing is written to disk or logged. Cached AI
  answers are dropped after an hour (or at once with **Clear my data**).
- **Job description text can't give the AI orders:** each input is wrapped in tags, and any
  copy of those tags inside the text is neutralised first, so a posting can't "break out".
- **No raw error screens:** unexpected errors show a short message; details go only to the
  PowerShell window, without your CV text.
- **Local only:** `.streamlit/config.toml` makes the app accept connections only from this
  computer (`localhost`), so others on your network cannot use it or your API key. It also
  turns off Streamlit's anonymous usage statistics.
- **API key:** read from `.env`, never printed or shown in error messages.

---

## Troubleshooting

| Problem | What it means and what to do |
|---|---|
| `ImportError: cannot import name ... from 'ats....'` | An old copy of the app is still running with old code in memory. Stop it (`Ctrl+C`) and start it again. If that does not help, see *Find an old copy of the app* below. |
| "The model '...' was not found" | `ANTHROPIC_MODEL` in `.env` is not a valid model ID. Use an ID like `claude-sonnet-5-5`, then restart the app. |
| "ANTHROPIC_MODEL in .env starts with '='" | The line has `==`. Change it to a single `=`, then restart. |
| "ANTHROPIC_API_KEY is not set" / "API key was rejected" | Check the key line in `.env`, then restart. |
| "running scripts is disabled on this system" | See setup step 4. |
| "This document contains text boxes" | Text inside Word text boxes is not read (many ATS systems skip it too). Move important content into normal paragraphs. |
| "No text was found in the PDF" | The PDF is probably a scanned image. Choose **Paste text** instead. |

### Find an old copy of the app

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -match 'streamlit' } |
  Select-Object ProcessId, CreationDate
```

One running app shows as two or three lines with the same start time. If it started before
your last code change, stop it with `Stop-Process -Id <ProcessId>` for each line, then run
`streamlit run app.py` again.

---

## Run the tests and checks

```powershell
pytest
ruff check .
ruff format .
```

The tests use synthetic documents and a fake AI client, so they need no API key and no
internet connection.

---

## Project layout

```
cv-ats/
├── app.py                 # Streamlit UI only
├── ats/
│   ├── config.py          # weights, thresholds, AI settings; loads .env
│   ├── models.py          # pydantic data models passed between steps
│   ├── parsing.py         # .docx / .pdf / .txt → text (step A)
│   ├── keywords.py        # deterministic keyword matching (step C)
│   ├── llm.py             # all Anthropic API calls (steps B, D, F)
│   ├── scoring.py         # final weighted score (step E)
│   ├── rewrites.py        # no-invented-facts checks for rewrites (step F)
│   └── prompts/           # AI instructions, one file per step
├── tests/
│   ├── fixtures/          # synthetic sample files only
│   ├── helpers.py         # builds synthetic .docx/.pdf files for tests
│   └── test_*.py          # one test file per module
├── .streamlit/config.toml # localhost only, no usage statistics
├── .env.example           # copy to .env and fill in
├── pyproject.toml         # tool settings (pytest)
└── requirements.txt       # pinned package versions
```

## Known limitations

- English only; CV must be `.docx`.
- PDFs with two columns may have their text order mixed; keyword matching still works, but
  the extracted text may look jumbled.
- Keyword matching finds words, not proof: "No SQL experience" still counts as SQL.
- Different word forms are not matched ("project manager" vs. "project management") unless
  the AI lists them as synonyms.
- The AI parts depend on the model's judgement; read its reasons and check them against
  your CV.
