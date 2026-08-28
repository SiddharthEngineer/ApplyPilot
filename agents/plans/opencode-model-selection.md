# Plan: Select Optimal OpenCode Models for ApplyPilot

**Started:** 2026-08-27
**Status:** 🔄 In Progress

---

## Goal

ApplyPilot drives OpenCode CLI as an agent backend in two places: (1) the
`plan_worker.py` harness that implements plan files, and (2) the auto-apply
pipeline (`apply --backend opencode`). Both currently use suboptimal or broken
default models. This plan sets ApplyPilot to use the best available OpenCode
models for its two use cases, fixes the auto-apply default so it is a valid
OpenCode `provider/model` (not a Claude short name), and adds a model fallback
list to the plan worker so transient free-tier model failures (403/429/removed)
do not dead-end a run.

User-facing outcome: plan workers produce faster, higher-quality
implementations on a purpose-built execution model, auto-apply via OpenCode
works out of the box with a valid model, and a failing free model automatically
falls back to a working one instead of erroring out.

## Success Criteria

1. `scripts/plan_worker.py` defaults to `opencode/nemotron-3.5-lightning-free` (code default, not a stored queue value).
2. `agents/plan_queue.json` `model` field is updated to `opencode/nemotron-3.5-lightning-free`.
3. `apply --backend opencode` (no `--model`) passes a valid OpenCode `provider/model` to `opencode run --model ...`, not `haiku`.
4. `apply --backend claude` keeps `haiku` as its default (zero behavior change).
5. A user-specified `--model` still overrides the default for either backend.
6. `plan_worker.py` retries a failed run with the next model in an ordered fallback list before counting the run as failed, and logs which model was used.
7. All existing tests pass; `ruff check src/ scripts/` reports no new violations.

---

## Task Chain

### Task 1: Change plan worker default model to Nemotron 3.5 Lightning

**Files:**
- `scripts/plan_worker.py` (modify)
- `agents/plan_queue.json` (modify)

**What:**
Update the default `model` used by the plan worker from `opencode/mimo-v2.5-free`
to `opencode/nemotron-3.5-lightning-free`. Lightning is NVIDIA's purpose-built
execution tier for long-running agents — the worker *implements* plans, so it
belongs on the execution tier, where Lightning wins the accuracy-speed Pareto
frontier and completes agentic tasks ~30% faster at comparable accuracy. Faster
iterations also cut wall-clock time and reduce exposure to the worker's
30-minute per-run hard timeout. The default is defined in two places: the
`load_queue()` fallback dict and the `worker_loop()` `state.get("model", ...)`
call. The live queue file also stores a `model` field that must be updated so an
existing queue file reflects the new default.

**Acceptance criteria:**
- `scripts/plan_worker.py` contains `opencode/nemotron-3.5-lightning-free` as the default in both the `load_queue()` and `worker_loop()` fallbacks, with no remaining default reference to `opencode/mimo-v2.5-free`.
- `agents/plan_queue.json` `model` field is `opencode/nemotron-3.5-lightning-free`.
- `./scripts/plan_worker.py --dry-run` logs `Model=opencode/nemotron-3.5-lightning-free`.
- An existing queue **without** a stored `model` still falls back to the new default.

**Status:** ❌ Not started

---

### Task 2: Fix auto-apply OpenCode default model

**Files:**
- `src/applypilot/cli.py` (modify)
- `src/applypilot/apply/launcher.py` (modify)

**What:**
The `apply --model` option defaults to `"haiku"`, a Claude short name that is
meaningless as an OpenCode `provider/model`. When `--backend opencode` is used
without an explicit `--model`, `_build_opencode_cmd()` passes `haiku` verbatim
to `opencode run --model haiku`, which OpenCode cannot resolve. Introduce
backend-aware default resolution: when `backend == "opencode"`, resolve the
default to `opencode/nemotron-3-ultra-free`; when `backend == "claude"`, keep
`"haiku"`. An explicit `--model` always wins. Auto-apply stays on
`nemotron-3-ultra-free` (not Lightning) because it is a single-shot patch
rewrite with no iteration loop, so raw single-pass reasoning quality outweighs
speed; the plan worker's loop is where Lightning's execution speed pays off.
Implement the resolution in `cli.py` at the `apply` command (compute the
effective model before it is passed to `apply_main` / `gen_prompt`), so
`launcher.py` continues to forward the model unchanged.

**Acceptance criteria:**
- `apply --backend opencode` with no `--model` resolves to `opencode/nemotron-3-ultra-free` in the console output and in the built command.
- `apply --backend claude` with no `--model` still reports/uses `haiku`.
- `apply --backend opencode --model opencode/big-pickle` uses `big-pickle` (override respected).
- `apply --backend claude --model sonnet` uses `sonnet` (override respected).
- `--gen` with `--backend opencode` prints the debug hint with the resolved model.

**Status:** ❌ Not started

---

### Task 3: Add model fallback list to plan worker

**Files:**
- `scripts/plan_worker.py` (modify)

**What:**
Add an ordered module-level fallback list of OpenCode model IDs, e.g.
`[opencode/nemotron-3.5-lightning-free, opencode/nemotron-3-ultra-free,
opencode/big-pickle, opencode/mimo-v2.5-free]`.
In the worker loop, when `run_agent()` returns a non-zero exit code, retry the
same plan iteration with the next model in the list before incrementing the
retry/error count for that plan. Log clearly which model was tried and which
fallback (if any) succeeded. If all fallbacks exhaust for one iteration, treat
it as a normal failure (existing retry/error logic applies). This guards against
transient free-tier failures (403/429/removed-model) without changing the
iteration/retry semantics.

**Acceptance criteria:**
- A module constant `MODEL_FALLBACKS` (or similar) holds an ordered list of `opencode/*` model IDs whose first entry matches the default.
- On a non-zero `run_agent()` exit, the worker retries the same plan with the next fallback model and logs the model transition.
- If all models in the list fail for one iteration, existing `retry_counts`/`MAX_RETRIES` behavior is preserved.
- On success, the worker logs the model that actually succeeded.
- `./scripts/plan_worker.py --status` and `--dry-run` still work unchanged.

**Status:** ❌ Not started

---

### Task 4: Document model selection

**Files:**
- `README.md` (modify)
- `CONTRIBUTING.md` (modify)

**What:**
Document the recommended OpenCode models and the fallback behavior. Add a short
note in README's auto-apply section that `--backend opencode` defaults to a
valid OpenCode model (and that `--model provider/model` overrides it). Add a
note to CONTRIBUTING's Apply Backends section describing the plan worker's model
default and fallback list, and how to change them.

**Acceptance criteria:**
- README documents the OpenCode auto-apply default model and the `--model provider/model` override.
- CONTRIBUTING documents the plan worker model default and fallback list.
- No other doc claims `haiku` is the OpenCode default.

**Status:** ❌ Not started

---

## Implementation Order

```
Task 1 (plan worker default) ──→ Task 3 (fallback list)
Task 2 (auto-apply default)            (independent)
Task 4 (docs) ← depends on Tasks 1-2
```

1. Task 1 — plan worker default model (foundation for Task 3).
2. Task 2 — auto-apply OpenCode default (independent of Tasks 1 and 3).
3. Task 3 — model fallback list (builds on Task 1's model constant).
4. Task 4 — documentation (requires Tasks 1-2 finalized).

## Key Design Decisions

1. **`opencode/nemotron-3.5-lightning-free` as the plan worker's primary model** — NVIDIA positions Lightning as the execution/builder tier for long-running agents and Ultra as the planning/orchestration tier. The worker *implements* plans, so it runs on the execution tier: Lightning wins the accuracy-speed Pareto frontier and completes agentic tasks ~30% faster at comparable accuracy. Its 262K context comfortably covers per-run agent sessions, and faster iterations cut wall-clock time while reducing exposure to the worker's 30-minute per-run hard timeout.
2. **`opencode/nemotron-3-ultra-free` stays the auto-apply default and a fallback** — auto-apply is a single-shot patch rewrite (no iteration loop), where raw single-pass reasoning quality outweighs speed; Ultra (1.0M context) also backs up Lightning on large multi-file plans.
3. **Fallbacks ordered by fit, then reliability** — `big-pickle` and `mimo-v2.5-free` follow the two Nemotron models because recent GitHub issues report them as more stable than some free models when the primary fails.
4. **Backend-aware default resolution in `cli.py`, not `launcher.py`** — keeps the launcher model-agnostic (it just forwards `--model`), centralizing backend-aware defaults at the CLI boundary where `backend` is known.
5. **Fallback retries within the same iteration** — alternate-model retries do not consume the per-plan retry/iteration budget, so a transient model outage is invisible to completion tracking.
6. **Persisted `model` in the queue file is updated once** — a stored queue `model` field overrides code defaults, so it must be migrated alongside the code change to avoid stale values.

## Historical Record

No tasks completed yet.
