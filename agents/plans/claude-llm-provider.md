# Plan: Claude API as the only LLM provider (Batch API for bulk stages, spend tracking)
**Status:** 🚧 In progress
**Risk:** review (replaces `llm.py`, touches `pyproject.toml`, `config.py`, `database.py`, the CLI and every LLM call site)

## Goal
Every LLM call in Strelitzia goes to the Claude API through the official `anthropic` Python SDK. Gemini, OpenAI, OpenCode
and local-endpoint support are deleted, along with the free-tier throttling built around them. Bulk stages (posting
extraction, scoring, smartextract discovery) can run through the Message Batches API at 50% off. Interactive calls
(dashboard generate, one-off rescores) use normal requests. Each call records its token usage and dollar cost in a new
`llm_usage` table. A monthly budget stops a run before it overspends, and `strelitzia doctor` shows month-to-date
spend. Afterwards, `strelitzia run extract` and `strelitzia run score` clear the whole backlog in one run without
quota stops. Scoring keeps today's 1-10 rubric; plan `fit-preferences` replaces it.

## Context
- `src/strelitzia/llm.py` (756 lines): `_detect_provider` (35-110), per-model limits/`llm_limits.yaml` (115-235),
  `DailyUsage` (236-283), `LLMStopRun`/`LLMQuotaExhausted`/`LLMOverloaded` (284-318), `LLMClient` with Gemini-native and
  OpenAI-compat paths, throttling and 503 fallback (353-680), `get_client(purpose)` (696). `PURPOSES = ("discovery",
  "extract", "scoring", "tailor", "cover")`, and `LLM_{PURPOSE}_MODEL` overrides each one. Keep `get_client(purpose)`
  and `LLMStopRun` as the public surface so call sites change as little as possible.
- Call sites:
  - `enrichment/extract.py:72-110`: batched, 5 postings per request with `job_id` matching.
  - `scoring/scorer.py:24-160`: `SCORE_PROMPT`, `SCORE_SCHEMA`, one call per job.
  - `scoring/tailor.py:583,697,754,811`, `scoring/cover_letter.py:196`.
  - `enrichment/detail.py:463` and `discovery/smartextract.py`: `get_client()` / `client.ask`.
  - `web/tasks.py` (dashboard generate).
  - `cli.py:700-790` (`doctor` provider block) and `wizard/init.py` (asks for a Gemini key).
- Schemas are hand-written Gemini `responseSchema` dicts (uppercase `OBJECT`/`STRING`, `propertyOrdering`):
  `posting_model.py:posting_schema/batch_schema`, `scorer.py:SCORE_SCHEMA`, `resume_model.py:RESUME_SCHEMA`, and the
  schema in `tailor.py:601`. Convert them to standard JSON Schema with `additionalProperties: false` and full
  `required` lists, which Claude structured outputs need. Claude doesn't support numeric/string constraints
  (min/max/pattern), so leave those to the lenient `from_dict` coercion that already exists.
- Gotchas for current models (`claude-haiku-5-5`, `claude-sonnet-5-5`):
  - Non-default `temperature` returns a 400. Drop the `temperature` argument everywhere.
  - Assistant prefill returns a 400.
  - Thinking is adaptive and on by default. Control depth with `output_config={"effort": ...}`. Haiku 5.5's default
    effort is `medium`.
  - Use `output_config={"format": {...}}` for structured output (`output_format` is deprecated).
  - Check `stop_reason` before reading content: `refusal` and `max_tokens` become retryable errors.
  - Haiku 5.5 has no server-side refusal fallback.
- Prompt caching: the minimum cacheable prefix is 512 tokens on these models. The extraction system prompt
  (`posting_prompt.py:build_extract_prompt`) qualifies.
  - `build_extract_prompt` embeds `today`, which changes the prefix once a day. That's acceptable. Do not add timestamps
    anywhere else in a system prompt.
  - Put `cache_control: {"type": "ephemeral"}` on the system block. Verify hits with `usage.cache_read_input_tokens`.
  - Cache hits inside a batch are best-effort.
- Batches: `client.messages.batches.create(requests=[{custom_id, params}])`, then poll `retrieve(id).processing_status`
  until `"ended"`, then stream `results(id)`.
  - Results arrive in any order, so key them by `custom_id`.
  - Most batches finish within an hour; the maximum is 24h.
  - The SDK reference is `python/claude-api/batches.md` in the `claude-api` skill. Read it before writing the batch
    code. Don't guess SDK names.
- `anthropic` 1.x is built on `httpx2`, not `httpx`. Discovery code keeps using `httpx`; never pass an `httpx` object
  into the SDK. Pin `anthropic>=1,<2` and check that a published 1.x exists before pinning.
- Tests are hermetic (`tests/conftest.py` blocks network). Mock the SDK client in unit tests. The `-m llm --run-llm`
  tier makes real Claude calls and must stay opt-in.
- **Runner gotcha:** `scripts/agent/run.sh` authenticates `claude -p` with `CLAUDE_CODE_OAUTH_TOKEN`. In `-p` mode,
  Claude Code silently prefers `ANTHROPIC_API_KEY` when it's set. Never put `ANTHROPIC_API_KEY` in
  `~/.config/applypilot-agent/env` or the runner's environment, or the runner bills the API instead of the
  subscription. Strelitzia reads the key only through `load_env()` from `~/.strelitzia/.env`.

## Tasks
### Task 1: Anthropic client, model routing, pricing table
**Runs:** unattended
**Files:** pyproject.toml (modify), src/strelitzia/llm.py (rewrite), src/strelitzia/config/llm_models.yaml (new),
src/strelitzia/config/llm_limits.yaml (delete), src/strelitzia/config.py (modify), tests/test_llm.py (rewrite),
tests/test_quota_stop.py (delete or rewrite), tests/test_structured_output.py (modify), .env.example (modify)
**What:**
- Add `anthropic>=1,<2`.
- `llm_models.yaml` holds the per-purpose defaults and per-model prices (USD per MTok: input, output, cache_read,
  cache_write_5m; batch = 50%):
  - discovery, extract, scoring: `claude-haiku-5-5`, effort `low`
  - tailor, cover: `claude-sonnet-5-5`, effort `medium`
  - `LLM_{PURPOSE}_MODEL` / `LLM_{PURPOSE}_EFFORT` env vars still override.
  - An override that isn't a `claude-*` model id (e.g. a leftover `LLM_SCORING_MODEL=gemini-...` in
    `~/.strelitzia/.env`) is ignored with a one-time warning, so stale Gemini settings can't break the stages after
    this merges. `GEMINI_API_KEY` and other legacy keys are ignored silently.
- New `llm.py` API:
  - `LLMClient.chat(messages, max_tokens=..., schema: dict | None = None, effort: str | None = None) -> str`.
    The system message becomes a cached system block. With `schema`, it sends `output_config.format` and returns the
    JSON text.
  - `LLMClient.request_params(...) -> dict` builds the same params for batch use.
  - `get_client(purpose)` and `ask()` are kept.
  - `LLMStopRun` is kept (now raised for budget exhaustion and auth errors).
  - Delete `DailyUsage`, the RPM/RPD throttles, the Gemini/compat paths, `fallback_model` and `LLM_FALLBACK_MODEL`.
    The SDK's built-in retries (429/5xx) replace the hand-written ones.
- `cost_usd(model, usage, batch: bool) -> float` from the YAML prices.
- `.env.example`: `ANTHROPIC_API_KEY`, `LLM_MONTHLY_BUDGET_USD=20`.
**Validate:** `pytest tests/test_llm.py tests/test_structured_output.py -q` → passes (mocked SDK: system caching block
present, no `temperature` sent, schema forwarded, refusal → error, cost math on a fixed usage object).
**Status:** ✅ 2026-10-13 (this commit)

### Task 2: Usage ledger and monthly budget
**Runs:** unattended
**Files:** src/strelitzia/database.py (modify), src/strelitzia/db_pg.py (modify if the DDL lives there), src/strelitzia/llm.py (modify), tests/test_llm_usage.py (new)
**What:**
- Table `llm_usage(id, ts, purpose, model, batch INTEGER, input_tokens, cache_read_tokens, cache_write_tokens,
  output_tokens, cost_usd REAL, job_url TEXT NULL, user_id TEXT NULL)` in SQLite and Postgres (idempotent `CREATE ...
  IF NOT EXISTS`, like `status_events`). `user_id` stays NULL until there are users; it's there so per-user caps don't
  need a migration later.
- Every `chat()` and every batch result writes a row.
- `month_spend(conn) -> float`.
- `check_budget(estimated_usd)` raises `LLMStopRun("budget")` when month-to-date plus the estimate exceeds
  `LLM_MONTHLY_BUDGET_USD` (unset = no limit, with a one-time warning).
- `estimate_cost(purpose, requests: list[dict], batch: bool) -> float` uses `client.messages.count_tokens` for input,
  plus a per-purpose output-token guess from the YAML. This is the API plan `fit-preferences` will use for its
  "rescore costs ~$X" dialog.
**Validate:** `pytest tests/test_llm_usage.py -q` → rows written with correct cost; budget stop triggers at the limit.
**Status:** ❌

### Task 3: Batch runner
**Runs:** unattended
**Files:** src/strelitzia/llm_batch.py (new), src/strelitzia/database.py (modify), tests/test_llm_batch.py (new)
**What:**
- `run_batch(purpose, items: list[tuple[str, dict]], on_result: Callable[[str, str | None, str | None], None], poll_seconds=30) -> dict`,
  where `items` are (custom_id, params).
  - Submits one Message Batch, splitting at the API's request-count and size limits.
  - Records the batch id in a new `llm_batches(id, purpose, submitted_at, status, request_count)` table.
  - Polls until it ends, then streams results to `on_result(custom_id, text, error)` and writes `llm_usage` rows with
    `batch=1`.
- `resume_open_batches(purpose, on_result)` collects batches left open by a killed run, so nothing paid for is lost.
- `custom_id` must match `^[a-zA-Z0-9_-]{1,64}$`. Use the existing `job_id()` hash, not the URL.
**Validate:** `pytest tests/test_llm_batch.py -q` → out-of-order results matched by custom_id; errored/expired results
reported as errors; resume picks up an open batch.
**Status:** ❌

### Task 4: Extraction and smartextract on Claude
**Runs:** unattended
**Files:** src/strelitzia/enrichment/extract.py (modify), src/strelitzia/enrichment/posting_model.py (modify), src/strelitzia/enrichment/posting_prompt.py (modify),
src/strelitzia/enrichment/detail.py (modify), src/strelitzia/discovery/smartextract.py (modify), tests/test_posting_extract.py (modify), tests/test_posting_model.py (modify)
**What:**
- Change to one posting per request: the cached system prompt makes the per-job overhead small, and `job_id` mismatches
  can no longer happen.
- `posting_schema()` returns JSON Schema. Delete `batch_schema()`, the multi-posting framing in `format_batch`, and the
  `EXTRACT_BATCH_SIZE` setting.
- `run_extraction(..., mode: Literal["batch", "sync"] = "batch")`:
  - `batch` goes through `run_batch`.
  - `sync` loops `chat()` and is used when `limit` ≤ 5 or for the dashboard.
  - Retries and `MAX_EXTRACT_ATTEMPTS` are unchanged.
- Bump `EXTRACT_VERSION` to 4 so every row is re-extracted on Claude. This is about $0.50 per 1,000 rows; show the
  estimate and call `check_budget` first.
- `detail.py`/`smartextract.py` call `get_client("discovery").chat(...)` without `temperature`.
**Validate:** `pytest tests/test_posting_extract.py tests/test_posting_model.py tests/test_smartextract_* -q` → pass.
**Status:** ❌

### Task 5: Scoring, tailoring, cover letters on Claude
**Runs:** unattended
**Files:** src/strelitzia/scoring/scorer.py (modify), src/strelitzia/scoring/tailor.py (modify), src/strelitzia/scoring/resume_model.py (modify),
src/strelitzia/scoring/cover_letter.py (modify), tests/test_scoring_errors.py (modify), tests/test_resume_template.py (modify if schema-dependent)
**What:**
- `SCORE_SCHEMA` becomes JSON Schema. Drop the plain-text `SCORE:` parser fallback, since structured output always
  returns JSON.
- The resume and system prompt go in the cached system block; the job goes in the user turn. This order lets the
  resume prefix be reused across jobs.
- `run_scoring(..., mode="batch"|"sync")`, same rules as extraction.
- Tailor and cover: remove `temperature`, convert `RESUME_SCHEMA`, and keep prompts unchanged (quality work is
  backlog).
- Delete the Gemini-specific error hints (`scorer.py:140-150`).
**Validate:** `pytest tests/test_scoring_errors.py tests/test_scoring_prefilter.py tests/test_content_library_tailor.py -q` → pass.
**Status:** ❌

### Task 6: CLI, doctor, init wizard, docs
**Runs:** unattended
**Files:** src/strelitzia/cli.py (modify), src/strelitzia/pipeline.py (modify), src/strelitzia/wizard/init.py (modify), README.md (modify), AGENTS.md (modify)
**What:**
- `strelitzia run extract|score` gain `--sync` and print the cost estimate before starting a batch.
- `doctor` shows: key present (never the value), the model and effort for each purpose, month-to-date spend against
  the budget, and open batches.
- `init` asks for `ANTHROPIC_API_KEY` instead of Gemini.
- Docs: replace "Gemini free tier" in AGENTS.md and README, and record the runner gotcha in the README's setup notes.
**Validate:** `scripts/validate.sh --full` → green. `strelitzia doctor` with a dummy key prints the Claude block and no Gemini lines.
**Status:** ❌

### Task 7: Live smoke test
**Runs:** interactive (needs `ANTHROPIC_API_KEY`, spends ~$0.05)
**Files:** tests/test_live_scoring_tailoring_cover.py (modify)
**What:** Point the `llm` tier at Claude. Run one sync extraction, one score, one 2-job batch, and one cover letter,
checking `cache_read_input_tokens > 0` on the second same-purpose call.
**Validate:** `pytest -m llm --run-llm tests/test_live_scoring_tailoring_cover.py -q` → pass; `llm_usage` has rows.
**Status:** ❌

## Validation
- `scripts/validate.sh --full` passes.
- `grep -rniE "gemini|openai|opencode|LLM_URL|LLM_RPM|DailyUsage" src/ tests/` → no hits (except the CHANGELOG-style
  mention in `wizard`'s migration note, if any).
- Task 7 passes on the VPS.

## Human test steps (preprod)
1. Put `ANTHROPIC_API_KEY` and `LLM_MONTHLY_BUDGET_USD` in the preprod `.env` (not the runner env), then deploy preprod.
2. `strelitzia doctor` → Claude models per purpose, spend $0.00 of budget.
3. `strelitzia run extract --limit 3 --sync` → 3 extracted; the dashboard job page shows the new fields.
4. `strelitzia run extract` → prints the estimate, submits a batch, finishes, and `pending_left` is 0.
5. `strelitzia run score` → backlog scored in one run; `doctor` spend matches the Anthropic Console usage page within a few cents.
6. Dashboard: generate a resume and a cover letter for one job → both PDFs are produced.

## Decisions
1. Claude only. Gemini, OpenAI, OpenCode and local endpoints are deleted, not kept as fallbacks (user, 2026-10-13).
2. The app uses the API with an API key, never the Pro subscription. Anthropic's docs point products and services to
   API keys (user, 2026-10-13).
3. Haiku 5.5 at low effort for extract, score and discovery; Sonnet 5.5 at medium for tailor and cover. Everything is
   overridable per purpose.
4. Batch API by default for bulk stages, sync for small or interactive runs. The 50% discount is worth up to an hour of latency on cron work.
5. One posting per extraction request. Prompt caching makes batching postings unnecessary and removes cross-posting mix-ups.
6. Spend is tracked per call with a nullable `user_id`. The monthly cap is global for now and becomes per-user when
   other users exist (user, 2026-10-13).
