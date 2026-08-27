# Changelog

## [Unreleased]

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
- Default Gemini model updated from `gemini-2.0-flash` to `gemini-2.5-flash` (current GA).
- `scorer.py`: Error logging now includes HTTP status code, truncated response body, and Gemini-specific remediation hints.
