# Plan: Code health cleanup (from the 2026-10-11 repo audit)
**Status:** ✅ Complete (2026-10-12). The agent did Tasks 1-5 and most of 6 and 8; an interactive session finished the `.github/` and `scripts/agent/` parts (its permissions deny those paths).
**Risk:** review (touches `pyproject.toml`, `.github/`, `scripts/validate.sh`, `scripts/agent/preprod.sh` and `src/applypilot/apply/*`; one PR on preprod, user decision 2026-10-12)

## Goal
Make the codebase cheaper for agents to work in. After this plan, `ruff check` enforces pyflakes (`F`) everywhere, so unused imports and variables
can't come back. The two `extract_json` helpers become one. The test suite loses its 9 seconds of real sleeping. `python-jobspy` is a declared
extra instead of a hand-typed pin in three places. `.env.example` lists every env var the code reads. CI also runs the dashboard tests and
checks that the committed bundle is current. Behavior doesn't change, except that `extract_json` becomes more lenient in both callers.

## Context
- **Pyflakes findings (2026-10-12):** `.venv/bin/ruff check --select F src tests scripts --output-format concise` → 29 findings (20 F401, 5 F541, 4 F841).
  Four F841s need a look, not just `--fix`:
  - `src/applypilot/apply/prompt.py:200` `auth_info = work_auth.get("legally_authorized_to_work", "")`: unused. Delete the line. Don't add it to the prompt.
  - `src/applypilot/scoring/tailor.py:100` `projects_str = …`: unused, so `preserved_projects` never reaches the tailoring prompt. Delete the line
    and list this under "Noticed, not changed" in the PR (it may be a missed prompt input; that's a prompt change for another plan).
  - `tests/test_smartextract_cache.py:227` `r2` and `:258` `r`: keep the call and drop the assignment (`_ = …` is wrong style; just call it).
- **Gotcha (from R14):** a test may `monkeypatch.setattr(module, "name", …)` on a name that only exists because of an unused import.
  E.g. `src/applypilot/apply/launcher.py:29` imports `chrome` and `dashboard`. Grep `tests/` for `launcher.chrome` / `"chrome"` before deleting.
  If a test needs the import, keep it with `# noqa: F401  # patched by tests/<file>` and say so in the PR. Never change the test to make it pass.
- **Ruff rule set:** `pyproject.toml` `[tool.ruff.lint] select = ["E9", "F63", "F7", "F82"]`, used by `scripts/validate.sh` and `.github/workflows/ci.yml`
  (`ruff check src/ tests/ scripts/`). `F` is a superset of `F63`, `F7` and `F82`.
- **Two `extract_json` helpers:**
  - `src/applypilot/scoring/tailor.py:397` `extract_json(raw)`: direct parse → each fenced block (with optional `json` tag) → outermost `{…}`;
    raises `ValueError`. Callers at `tailor.py:652` and `:881` catch `ValueError`. Also imported by `tests/test_content_library_tailor.py:16`.
  - `src/applypilot/discovery/smartextract.py:858` `extract_json(text)`: strips a `<think>…</think>` prefix → first ```` ```json ```` or ```` ``` ```` block →
    removes invalid backslash escapes (`re.sub(r'\\([^"\\\/bfnrtu])', r'\1', text)`) → direct parse → trims trailing chars until it parses;
    raises `json.JSONDecodeError`. Callers: `smartextract.py:535, 591, 1010, 1146` and `src/applypilot/enrichment/detail.py:469`
    (`from applypilot.discovery.smartextract import extract_json`). They catch `Exception` or nothing.
  - `json.JSONDecodeError` subclasses `ValueError`, so one helper that raises `JSONDecodeError` satisfies every caller.
- **Slow tests:** `tests/test_cred_server.py::TestCaptchaSolve::{test_successful_solve_hcaptcha, test_successful_solve_turnstile,
  test_result_never_leaks_key}` each take ~3.0s because `src/applypilot/apply/cred_server.py:225` polls with `await asyncio.sleep(3)`.
  Check with `.venv/bin/pytest tests/test_cred_server.py -q --durations=5`.
- **`.env.example` gaps:** read by the code but not documented: `EXTRACT_BATCH_SIZE` (`config.py`, `enrichment/extract.py`), `LLM_API_KEY` (`llm.py`),
  `APPLYPILOT_WEB_DIR` (`web/app.py`, `cli.py`), `APPLYPILOT_DIR` (`config.py:10`), `CHROME_PATH` (`config.py`, `cli.py`). Read each one's default and meaning from the code.
  R16 (`llm-limits-config`) rewrites the LLM rate-limit block at the top of `.env.example` (lines ~10-23). Add your section **at the end of the file**
  and don't touch that block, so the two PRs don't conflict.
- **jobspy pin:** `"python-jobspy==1.2.0"` is typed in `scripts/validate.sh:52` (`--setup`), `.github/workflows/ci.yml` (Install step) and `README.md:40`
  (`>=1.2.0`). `README.md:28` installs the PyPI `applypilot` package, which won't have a new extra: leave that line alone. The `Dockerfile` doesn't install
  jobspy (the dashboard image doesn't run discovery): leave it alone.
- **Dashboard in CI:** `scripts/validate.sh:62-75` shows the local steps (`npm ci`, `npm test`, `npm run build`, then the committed-bundle check on
  `src/applypilot/web/static`). Node on the VPS is v22. `dashboard/package.json` has no `engines` field.
- **"Dashboard" means three things:** `src/applypilot/apply/dashboard.py` (Rich TUI during `apply`), `src/applypilot/view.py` (static HTML report),
  and `src/applypilot/web/` + `dashboard/` (the React app). Agents mix them up.
- **Out of scope:** splitting the god-files (`smartextract.py`, `scoring/tailor.py`, `cli.py`), and renaming `APPLYPILOT_APP_DIR` (see Decisions).

## Tasks
### Task 1: Fix every pyflakes finding
**Runs:** unattended
**Files:** src/applypilot/apply/dashboard.py, src/applypilot/apply/launcher.py, src/applypilot/apply/prompt.py, src/applypilot/cli.py,
src/applypilot/discovery/jobspy.py, src/applypilot/discovery/smartextract.py, src/applypilot/pipeline.py, src/applypilot/scoring/cover_letter.py,
src/applypilot/scoring/tailor.py, src/applypilot/view.py (modify); tests/test_content_library_e2e.py, tests/test_content_library_tailor.py, tests/test_jobspy.py,
tests/test_pdf_overflow.py, tests/test_smartextract_cache.py, tests/test_smartextract_heuristic.py (modify)
**What:** Run `.venv/bin/ruff check --select F src tests scripts --fix`, review the diff by hand (it must only delete imports and turn
placeholder-less f-strings into plain strings), then fix the 4 F841s as described in Context. Apply the monkeypatch gotcha.
In tests, only unused imports and unused assignments change: no test function, assert or call is removed.
If the finding list differs from the 29 in Context (trunk moved), fix whatever `--select F` reports now.
**Validate:** `.venv/bin/ruff check --select F src tests scripts` → `All checks passed!`; `scripts/validate.sh` → ALL PASS.
**Status:** ✅ 2026-10-12 (this commit)

### Task 2: Enforce pyflakes in the pinned rule set
**Runs:** unattended
**Files:** pyproject.toml (modify)
**What:** Change `select` to `["E9", "F"]` and update its comment ("Real errors and pyflakes (unused imports/variables)…"). Nothing else in `pyproject.toml`.
**Validate:** `.venv/bin/ruff check src tests scripts` → `All checks passed!`; `scripts/validate.sh` → ALL PASS.
**Status:** ✅ 2026-10-12 (this commit)

### Task 3: One `extract_json`
**Runs:** unattended
**Files:** src/applypilot/llm_json.py (new), src/applypilot/scoring/tailor.py (modify), src/applypilot/discovery/smartextract.py (modify), tests/test_llm_json.py (new)
**What:** New module `applypilot/llm_json.py` with `extract_json(text: str) -> dict | list`, the union of both helpers, in this order, returning the first success:
1. strip; if `</think>` is present and text follows it, keep only what follows;
2. `json.loads` as is;
3. each fenced block (```` ``` ```` pairs, with an optional leading `json` tag), as is;
4. the same candidates (whole text, then each fenced block) after the invalid-backslash-escape fix;
5. the outermost `{…}` slice, then the outermost `[…]` slice;
6. the smartextract trailing-trim loop on the last candidate;
7. raise `json.JSONDecodeError("Could not parse JSON", text, 0)`.
Delete both old bodies. In `tailor.py` and `smartextract.py`, `from applypilot.llm_json import extract_json` so the existing names
(`tailor.extract_json`, `smartextract.extract_json`, used by `enrichment/detail.py` and tests) keep working. Don't change any caller.
`tests/test_llm_json.py`: plain JSON object and array; ```` ```json ```` fence; bare fence; prose before and after an object; a `<think>` block before
the JSON; an invalid `\_` escape; trailing junk after the closing brace; nothing parseable → `json.JSONDecodeError`, which is also a `ValueError`.
**Validate:** `.venv/bin/pytest tests/test_llm_json.py tests/test_content_library_tailor.py tests/test_smartextract_cache.py tests/test_smartextract_heuristic.py -q` → pass;
`grep -rn "def extract_json" src` → only `src/applypilot/llm_json.py`; `scripts/validate.sh` → ALL PASS.
**Status:** ✅ 2026-10-12 (this commit)

### Task 4: Stop the captcha tests sleeping
**Runs:** unattended
**Files:** tests/test_cred_server.py (modify)
**What:** In `TestCaptchaSolve`, patch the poll delay away for the tests that reach the polling loop, e.g. a class-level autouse fixture
`patch("applypilot.apply.cred_server.asyncio.sleep", new=AsyncMock())`. If `cred_server` imports `asyncio` differently, patch where it's looked up.
Don't change `cred_server.py`. Every existing assert stays.
**Validate:** `.venv/bin/pytest tests/test_cred_server.py -q --durations=3` → all pass, and no listed duration is above 0.5s; `scripts/validate.sh` → ALL PASS.
**Status:** ✅ 2026-10-12 (this commit)

### Task 5: Document every env var in `.env.example`
**Runs:** unattended
**Files:** .env.example (modify)
**What:** Append a `# Advanced / per-host settings (optional)` section at the end of the file with commented-out entries for `EXTRACT_BATCH_SIZE`,
`LLM_API_KEY`, `APPLYPILOT_WEB_DIR`, `APPLYPILOT_DIR` and `CHROME_PATH`, each with its default and a one-line meaning taken from the code.
Don't edit the existing LLM rate-limit block (R16 owns it).
**Validate:** `for v in EXTRACT_BATCH_SIZE LLM_API_KEY APPLYPILOT_WEB_DIR APPLYPILOT_DIR CHROME_PATH; do grep -q "$v" .env.example || echo missing $v; done` → no output;
`git diff origin/trunk -- .env.example | grep '^-[^-]'` → no output (only additions); `scripts/validate.sh` → ALL PASS.
**Status:** ✅ 2026-10-12 (this commit)

### Task 6: `python-jobspy` as a declared extra
**Runs:** unattended
**Files:** pyproject.toml, scripts/validate.sh, .github/workflows/ci.yml, README.md (modify)
**What:** Add `jobspy = ["python-jobspy==1.2.0"]` to `[project.optional-dependencies]`. Install `-e ".[dev,web,jobspy]"` instead of the separate pin in
`scripts/validate.sh --setup` and the CI Install step (update the step's comment). `README.md:40` becomes `uv pip install -e ".[dev,jobspy]"`.
Leave `README.md:28` and the `Dockerfile` alone (see Context).
**Validate:** `grep -rn "python-jobspy" pyproject.toml scripts/validate.sh .github/workflows/ci.yml README.md` → the pin appears only in `pyproject.toml`
(README may keep the "Older installs" note); in a scratch venv, `uv venv -q /tmp/jp && uv pip install -q --python /tmp/jp/bin/python -e ".[jobspy]" && /tmp/jp/bin/python -c "import jobspy"`
→ exits 0 (use the scratch dir your session allows if `/tmp` is not writable); `scripts/validate.sh` → ALL PASS.
**Status:** ✅ 2026-10-12: extra, `validate.sh`, README (agent); CI Install step (interactive session).

### Task 7: Dashboard job in CI
**Runs:** unattended
**Files:** .github/workflows/ci.yml (modify)
**What:** Add a second job `dashboard` (ubuntu-latest, 10-minute timeout): checkout, `actions/setup-node@v4` with `node-version: "22"` and npm caching on
`dashboard/package-lock.json`, then `npm ci --prefix dashboard`, `npm test --prefix dashboard`, `npm run build --prefix dashboard`, and the same bundle check as
`scripts/validate.sh` (`test -z "$(git status --porcelain -- src/applypilot/web/static)"`, with a message saying to run `npm run build --prefix dashboard` and commit).
Keep the existing `test` job as it is.
**Validate:** `.venv/bin/python -c "import yaml,sys; d=yaml.safe_load(open('.github/workflows/ci.yml')); assert set(d['jobs'])=={'test','dashboard'}"` → exits 0;
`scripts/validate.sh --full` → ALL PASS (proves the local bundle is current). On the PR, both CI jobs must be green before you write `ready`
(`gh pr checks <n> --repo SiddharthEngineer/ApplyPilot --watch` if your permissions allow it; otherwise say in the PR that the dashboard job is unverified).
If only the bundle check fails in CI while it passes locally on a clean tree (a non-deterministic build), drop that one step, keep test and build, and explain it in the PR.
**Status:** ✅ 2026-10-12 (interactive session): `dashboard` job with Node 22, npm test, build and the committed-bundle check.

### Task 8: Say which "dashboard" is which, and fix two stale plan paths
**Runs:** unattended
**Files:** AGENTS.md, scripts/validate.sh, scripts/agent/preprod.sh (modify)
**What:** `scripts/validate.sh:40` and `scripts/agent/preprod.sh:73` point at `agents/plans/vps-layout-migration.md`, which moved to `agents/plans/done/`.
Point both messages at `agents/DEPLOY.md` (the preprod section) instead. Change only those message strings. Then, in AGENTS.md, add one line in the Map section: `apply/dashboard.py` = Rich TUI during `apply`; `view.py` = static HTML report; `web/` + `dashboard/` = the React
app served at `/app/`. Keep AGENTS.md short (it's loaded every session): one line, no new section.
**Validate:** `grep -n "view.py" AGENTS.md` → the new line; `grep -rn "vps-layout-migration" scripts` → no output; `scripts/validate.sh` → ALL PASS.
**Status:** ✅ 2026-10-12: AGENTS.md line and `validate.sh` message (agent); `preprod.sh` message (interactive session).

## Validation
- `scripts/validate.sh --full` → ALL PASS.
- `.venv/bin/ruff check src tests scripts` → `All checks passed!` with `F` in the pinned rule set.
- `.venv/bin/pytest tests/ -q --durations=5` → passes; no `test_cred_server.py` test in the top 5.
- `grep -rn "def extract_json" src` → one hit, `src/applypilot/llm_json.py`.
- The PR's CI shows two green jobs, `test` and `dashboard`.

## Human test steps (preprod)
1. Open `https://applypilot-preprod.engineerfamily.net/app/` (preprod login). The job list loads with the seeded jobs, and a job's detail page opens. (Nothing in
   the dashboard should look different; this checks the cleanup didn't break the served app.)
2. On the PR, the Checks tab shows two green jobs: `test` and `dashboard`.
3. Read the PR's "Noticed, not changed" list (at least the `preserved_projects` / `projects_str` note from Task 1) and decide whether it needs its own plan.

## Decisions
1. One review plan, not a low/review split (user, 2026-10-12).
2. Splitting the god-files is out of scope. It changes many lines and needs its own review plan.
3. `APPLYPILOT_APP_DIR` stays. It's the explicit hand-off of the app dir to the apply MCP subprocess, and `cred_server` reads a password from
   `profile.json` only when it is set (`agents/plans/done/secure-password-at-rest.md`). Renaming it to `APPLYPILOT_DIR`, which tests and hosts set globally,
   would change that security behavior.
4. Unused variables that look like missed features (`projects_str`) are deleted and reported, not wired in: wiring them in changes LLM prompts.
5. The merged `extract_json` raises `json.JSONDecodeError`, a `ValueError` subclass, so callers of both old helpers keep working unchanged.
