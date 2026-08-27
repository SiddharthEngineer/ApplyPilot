# Plan: Migrate Default Gemini Model to 3.6-Flash

**Started:** 2026-08-27
**Status:** ✅ Complete

---

## Goal

Migrate ApplyPilot's default Gemini model from `gemini-2.5-flash` (which is retired and returns 404 for new users) to `gemini-3.6-flash` across all configuration entrypoints, error hints, wizard defaults, and doctor checks. The user-facing outcome is that running `applypilot run` or initializing via the wizard with a Gemini API key works out-of-the-box without manual `LLM_MODEL` overrides or 404 errors.

## Success Criteria

1. `_detect_provider()` in `src/applypilot/llm.py` defaults to `gemini-3.6-flash` when `LLM_MODEL` is unset.
2. The interactive setup wizard (`src/applypilot/wizard/init.py`) pre-fills `gemini-3.6-flash` as the default model prompt.
3. The `applypilot doctor` check (`src/applypilot/cli.py`) validates against `gemini-3.6-flash` by default and successfully verifies the model list from Google API.
4. Error hints in `src/applypilot/scoring/scorer.py` reference `gemini-3.6-flash`.
5. `.env.example` documents `gemini-3.6-flash`.
6. Live API test using `LLMClient.chat()` with `gemini-3.6-flash` returns HTTP 200 and successful completion ("PONG").

## Task Chain

### Task 1: Update core LLM client default model
**Files:**
- `src/applypilot/llm.py` (modify)
- `tests/test_llm.py` (modify)

**What:**
Change the default model fallback in `_detect_provider()` from `"gemini-2.5-flash"` to `"gemini-3.6-flash"`, and update module docstrings and test fixture client instantiation to match.

**Acceptance criteria:**
- `_detect_provider()` returns `gemini-3.6-flash` when `LLM_MODEL` and provider environment keys are set for Gemini.
- Unit tests in `tests/test_llm.py` pass.

**Status:** ✅ Complete (2026-08-27) — Updated `llm.py` and test fixture.

### Task 2: Update CLI doctor and init wizard defaults
**Files:**
- `src/applypilot/cli.py` (modify)
- `src/applypilot/wizard/init.py` (modify)

**What:**
Update the default model string in `applypilot doctor` (when checking Gemini API key validity) and in `applypilot init` (wizard prompt default) from `gemini-2.5-flash` to `gemini-3.6-flash`.

**Acceptance criteria:**
- `applypilot doctor` defaults to validating `gemini-3.6-flash`.
- Setup wizard prompts `gemini-3.6-flash` as the default model choice.

**Status:** ✅ Complete (2026-08-27) — Updated `cli.py` and `init.py`.

### Task 3: Update documentation, configuration examples, and error hints
**Files:**
- `src/applypilot/scoring/scorer.py` (modify)
- `.env.example` (modify)

**What:**
Update error hints in job scoring and system failure logging to reference `gemini-3.6-flash` instead of `gemini-2.5-flash`, and update `.env.example` comment.

**Acceptance criteria:**
- Scorer error logs point users to `gemini-3.6-flash`.
- `.env.example` documents `gemini-3.6-flash`.

**Status:** ✅ Complete (2026-08-27) — Updated `scorer.py` and `.env.example`.

### Task 4: Live integration verification
**Files:**
- None (verification task)

**What:**
Verify the updated LLM client against the live Gemini API using the user's API key to ensure both native and OpenAI-compat endpoints return HTTP 200 without 404 errors.

**Acceptance criteria:**
- `LLMClient.chat()` successfully returns assistant response ("PONG") using `gemini-3.6-flash`.

**Status:** ✅ Complete (2026-08-27) — Live test verified `gemini-3.6-flash` responds successfully with HTTP 200 on the compat endpoint.

---

## Implementation Order

```
Task 1 (LLM Client) → Task 2 (CLI / Wizard) → Task 3 (Hints / Docs) → Task 4 (Live Verification)
```

1. Task 1 — LLM client default update.
2. Task 2 — CLI doctor & setup wizard defaults.
3. Task 3 — Scorer error messages & `.env.example`.
4. Task 4 — Live API verification.

## Key Design Decisions

1. **Direct string default replacement** — Kept the decentralized model default string approach matching the existing codebase pattern, while updating all occurrences to prevent stale 404s.
2. **Preservation of legacy test values** — Kept explicit user-provided legacy env values (`gemini-2.0-flash`) in wizard pre-fill tests to ensure backward compatibility for loaded user configs.

## Historical Record

- 2026-08-27 — Plan created and completed. Migrated default Gemini model from `gemini-2.5-flash` to `gemini-3.6-flash` across `llm.py`, `cli.py`, `init.py`, `scorer.py`, and `.env.example`. Verified live API success.
