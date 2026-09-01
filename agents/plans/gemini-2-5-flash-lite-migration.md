# Plan: Migrate Discovery/LLM Tests from gemini-2.0-flash-lite to gemini-2.5-flash-lite
**Started:** 2026-09-01
**Status:** 🔄 In Progress (Tasks 1-5 complete, Task 6 pending)

## Goal
Running `source .env && pytest -m llm --run-llm -v` with `GEMINI_API_KEY` succeeds without manual `LLM_MODEL` overrides using cheap `gemini-2.5-flash-lite` for all cheap/discovery/test paths, while real `applypilot run score/tailor/cover` continues to use higher-quality `gemini-3.6-flash` out-of-the-box. `applypilot init` and `applypilot doctor` default to the correct models.

## Success Criteria
1. `rg -n "gemini-2\.0-flash-lite" src/ tests/ .env.example README.md --no-heading` returns 0 hits (history `agents/` excluded).
2. With only `GEMINI_API_KEY` set: `_detect_provider() -> gemini-3.6-flash` and `_detect_provider("discovery") -> gemini-2.5-flash-lite` (`PYTHONPATH=src python -c` assert).
3. `PYTHONPATH=src .venv/bin/python -m pytest tests/test_llm.py tests/test_doctor_content_library.py tests/test_init_wizard.py -v` passes.
4. `source .env && PYTHONPATH=src .venv/bin/python -m pytest -m llm --run-llm -v` makes real Gemini calls (4-6 calls) and all 6 `TestScoreJob/TestTailorResume/TestCoverLetter` `tests/test_live_scoring_tailoring_cover.py:105-238` pass (score 1-10, `Dear` prefix, no leak phrases); `LLMClient.chat()` 200 on both OpenAI-compat and native endpoints.
5. `applypilot doctor` with `GEMINI_API_KEY` prints `Gemini (gemini-3.6-flash)` and `Discovery model: gemini-2.5-flash-lite` without `WARN model not found` when model list from `GET https://generativelanguage.googleapis.com/v1beta/models?key=` contains both.

## Task Chain
### Task 1: Update core LLM client discovery default
**Files:** `src/applypilot/llm.py` (modify), `src/applypilot/config.py` (modify)
**What:** In `_detect_provider(purpose)` `src/applypilot/llm.py:34-77` change discovery fallback from `"gemini-2.0-flash-lite"` to `"gemini-2.5-flash-lite"`, update module docstring `llm.py:5,34-35`, keep `base_model = model_override or "gemini-3.6-flash"` `llm.py:69` unchanged for tailoring/scoring. Add `DEFAULTS["llm_discovery_model"] = "gemini-2.5-flash-lite"` `src/applypilot/config.py:233`.
**Acceptance:** `grep -n gemini-2.5-flash-lite src/applypilot/llm.py src/applypilot/config.py` hits; `tests/test_llm.py -k "TestDetectProvider or TestDiscoveryClient" -v` 6 pass (`test_discovery_default_is_flash_lite` `test_llm.py:82`, `test_discovery_client_uses_flash_lite` `test_llm.py:139`).
**Status:** ✅ Complete

### Task 2: Update CLI doctor and setup wizard defaults
**Files:** `src/applypilot/cli.py` (modify), `src/applypilot/wizard/init.py` (modify)
**What:** `cli.py:492,534` change `LLM_DISCOVERY_MODEL` default and `Discovery model` fallback from `gemini-2.0-flash-lite` to `gemini-2.5-flash-lite`; keep `cli.py:491` `LLM_MODEL=gemini-3.6-flash`. `wizard/init.py:596` `default_discovery_model = "gemini-2.5-flash-lite" if provider=="gemini"` and `init.py:559` stays `gemini-3.6-flash`.
**Acceptance:** `tests/test_init_wizard.py -k "discovery or ai_features" -v` pass; wizard writes `LLM_DISCOVERY_MODEL=gemini-2.5-flash-lite` `tests/test_init_wizard.py:871`; `applypilot doctor` `TestDoctorRateLimitTuning` `tests/test_doctor_content_library.py:184` expects `gemini-2.5-flash-lite`.
**Status:** ✅ Complete

### Task 3: Update env example and docs
**Files:** `.env.example` (modify), `README.md` (modify)
**What:** `.env.example:13` comment `LLM_DISCOVERY_MODEL=gemini-2.5-flash-lite  # cheaper for judge/strategy; tailoring keeps gemini-3.6-flash`. `README.md:101` `Cost & Rate Limits` bullet same.
**Acceptance:** `rg gemini-2.5-flash-lite .env.example README.md` hits; `rg gemini-2.0-flash-lite` 0 outside `agents/`.
**Status:** ✅ Complete

### Task 4: Update LLM test infrastructure
**Files:** `tests/conftest.py` (modify), `tests/test_llm.py` (modify), `tests/test_live_scoring_tailoring_cover.py` (modify)
**What:** `conftest.py:93-94` `setdefault LLM_MODEL/LLM_DISCOVERY_MODEL` → `gemini-2.5-flash-lite`; `test_live_scoring_tailoring_cover.py:6,32-33` doc + `monkeypatch.setenv` → `gemini-2.5-flash-lite` (keep `# live/llm env exception`); `test_llm.py:89,150` assertions → `gemini-2.5-flash-lite` for discovery.
**Acceptance:** `pytest tests/test_llm.py tests/test_live_scoring_tailoring_cover.py --collect-only` shows `@llm` tests; mocked `tests/test_llm.py -v` 28 pass.
**Status:** ✅ Complete

### Task 5: Update remaining wizard/doctor test fixtures
**Files:** `tests/test_init_wizard.py` (modify), `tests/test_doctor_content_library.py` (modify)
**What:** `test_init_wizard.py:789,835` provider default asserts + `tests/test_doctor_content_library.py:185,197` model list expects → `gemini-2.5-flash-lite` (keep `gemini-3.6-flash` for main model). Also `agents/STATE.md` note if needed.
**Acceptance:** `pytest tests/test_init_wizard.py tests/test_doctor_content_library.py -v` all pass; `rg gemini-2.0-flash-lite tests/` 0.
**Status:** ✅ Complete

### Task 6: Live integration verification
**Files:** none (verification)
**What:** `source .env && PYTHONPATH=src .venv/bin/python -m pytest -m llm --run-llm -v` with real `GEMINI_API_KEY`. Confirm `get_client().model == gemini-3.6-flash`, `get_discovery_client().model == gemini-2.5-flash-lite`.
**Acceptance:** 6 llm tests pass; `doctor` validates both models against `https://ai.google.dev/gemini-api/docs/models` list; pricing remains `2.5-flash-lite $0.10/$0.40` vs `3.6-flash $1.50/$7.50` per `ai.google.dev/gemini-api/docs/pricing`.
**Status:** ❌ Not started

## Implementation Order
```
Task1 (llm.py/config) → Task2 (cli/wizard) → Task3 (docs/env) → Task4 (conftest/llm/live) → Task5 (wizard/doctor fixtures) → Task6 (live verify)
```
1. Task1 — core default must land first (others depend on value).
2. Task2 — CLI/wizard read same constant.
3. Task3 — docs mirror code.
4. Task4 → Task5 — tests after code; live file after conftest.
5. Task6 — needs all prior + real API key.

## Key Design Decisions
1. Use `gemini-2.5-flash-lite` not `gemini-3.1-flash-lite` for tests/discovery — absolute cheapest Google model ($0.10/$0.40) per pricing page, keeps original "~5x cheaper" intent (actually 15x vs `3.6-flash`), still GA with free tier.
2. Keep `gemini-3.6-flash` for `get_client()` tailoring/scoring — higher quality for resume rewrite; discovery stays cheap via `get_discovery_client()`.
3. Vendor-recommended `gemini-3.1-flash-lite` ($0.25/$1.50) remains one-line alt if Google deprecates `2.5-flash-lite` — same tier, no code change beyond string.
4. Single-string defaults at `llm.py:77` + `config.py:233` — no new constants, matches `gemini-3.6-flash-migration.md` pattern.

## Historical Record
- 2026-09-01 — Plan drafted from diagnosis: `gemini-2.0-flash-lite` shutdown 2026-06-01 confirmed via `ai.google.dev/gemini-api/docs/deprecations` (+ `aichangewatch` 2027-05-07 for `3.1-flash-lite`). User chose `2.5-flash-lite` tests + `3.6-flash` tailoring.
