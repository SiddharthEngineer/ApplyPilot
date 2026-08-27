# Changelog

## [Unreleased]

### Fixed
- `plan_worker.py`: Removed invalid `--auto` flag from `opencode run` command that caused immediate failures.
- `plan_worker.py` + `plan_queue.json`: Corrected model name from `opencode/mimo-2.5-free` to `opencode/mimo-v2.5-free`.

### Added
- `applypilot init` now pre-fills prompts with previously saved values from `profile.json`, `searches.yaml`, and `.env` on re-run, so users only need to update changed fields.
- Added helper functions: `_load_existing_profile()`, `_load_existing_env()`, `_str_to_bool()`, `_join_list()`.
- Re-run banner ("ApplyPilot Reconfigure") shown when an existing profile is detected.
