# Changelog

## [Unreleased]

### Added
- `scripts/plan_worker.py`: Added `MODEL_FALLBACKS` ordered fallback list. When an agent run fails (e.g. transient free-tier 403/429/removed-model), the worker retries the same iteration with the next model before counting it as a failure.

### Changed
- `scripts/plan_worker.py` + `agents/plan_queue.json`: Default plan worker model changed from `opencode/mimo-v2.5-free` to `opencode/nemotron-3.5-lightning-free` (NVIDIA execution tier for long-running agents).
- `cli.py`: `apply --model` is now backend-aware. `--backend opencode` resolves the default to the valid OpenCode `opencode/nemotron-3-ultra-free` (single-pass reasoning quality); `--backend claude` keeps the `haiku` default. An explicit `--model` always wins.
- `README.md` / `CONTRIBUTING.md`: Documented the OpenCode auto-apply default model + override, and the plan worker's model default + fallback list.

### Fixed
- `plan_worker.py`: Removed invalid `--auto` flag from `opencode run` command that caused immediate failures.
- `plan_worker.py` + `plan_queue.json`: Corrected model name from `opencode/mimo-2.5-free` to `opencode/mimo-v2.5-free`.
- LLM Gemini 404/400/403 fallback: `applypilot run score` no longer fails with all-zero scores when using `GEMINI_API_KEY`. The client now falls back from the OpenAI-compat endpoint to native `generateContent` on 400/403/404 errors.

### Added
- `applypilot init` now pre-fills prompts with previously saved values from `profile.json`, `searches.yaml`, and `.env` on re-run, so users only need to update changed fields.
- Added helper functions: `_load_existing_profile()`, `_load_existing_env()`, `_str_to_bool()`, `_join_list()`.
- Re-run banner ("ApplyPilot Reconfigure") shown when an existing profile is detected.
- `tests/test_llm.py`: 15 mocked tests for `LLMClient` Gemini fallback, provider detection, and OpenAI isolation.
- `applypilot doctor` now validates configured `LLM_MODEL` against the Gemini API model list and warns if not found.

### Changed
- Default Gemini model updated from `gemini-2.5-flash` to `gemini-3.6-flash` following retirement of 2.5-flash for new users.
- `scorer.py`: Error logging now includes HTTP status code, truncated response body, and Gemini-specific remediation hints.
