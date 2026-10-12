# Plan: Strelitzia pivot (rename, drop auto-apply, bird-of-paradise theme)
**Status:** 🔄 In progress
**Risk:** review (renames the package, CLI, env vars and data dir, deletes the apply stage, and redesigns the dashboard)

## Goal
ApplyPilot becomes **Strelitzia** (v0.4.0), a job-discovery dashboard: discover → enrich → extract → score → tailor → cover, then
track applications by hand in the dashboard. Strelitzia doesn't submit applications. The dashboard gets a "Sunrise" theme built
from the bird-of-paradise flower, plus an original, MIT-licensed animated SVG of the flower in the top bar, the hero, the loading and
empty states, and the favicon. The tagline is **"Bring your search into the light."** Existing deployments keep running with no
config change: the old `applypilot` CLI name, `APPLYPILOT_*` env vars and `~/.applypilot` dir still work as deprecated fallbacks.

## Context
- The upstream is Pickle-Pixel/ApplyPilot under **AGPL-3.0**. About 45% of the current lines are still upstream code. The repo
  stays AGPL. `LICENSE` stays verbatim, and the upstream copyright and credit are kept (see Task 5). The new `NOTICE` and the
  dashboard footer source link satisfy AGPL §5(a) and §13.
- `src/applypilot/apply/` (chrome, cred_server, launcher, prompt, dashboard TUI) is the only auto-apply code. **Playwright stays**:
  `discovery/smartextract.py`, `discovery/jobspy.py`, `enrichment/detail.py` and `scoring/pdf.py` use it. CapSolver, the Claude/OpenCode
  CLI apply engines and Chrome detection go.
- Users still mark jobs "applied" by hand. Keep every DB column and user status. **No schema change.**
- The engineerfamily compose (a separate repo, not touched here) runs `applypilot serve`, mounts `/home/dev/.applypilot`, and sets
  `APPLYPILOT_DATABASE_URL`. Preprod deploys this PR into that unchanged stack, so the deprecated fallbacks are required, not optional.
- Absolute paths under `/home/dev/.applypilot/...` are stored in DB rows. Don't rewrite them. The post-merge migration keeps that
  path valid with a symlink.
- The Postgres DB and role stay named `applypilot` / `applypilot_preprod` (user decision). `scripts/seed_preprod.py` still refuses
  any DB not named exactly `applypilot_preprod`.
- The React app is in `dashboard/`. The built bundle is committed in `src/applypilot/web/static` and moves with the package.
  Theme tokens are in `dashboard/src/styles.css:1-40` (`:root` + `prefers-color-scheme: dark`). Keep that token pattern.
- `pyproject.toml` says 0.3.1 and `__init__.py` says 0.3.0. Set both to **0.4.0** (not 1.0.0: more changes come before going live).
- Upstream's README banner defends the "ApplyPilot" name, so no user-facing text may say "ApplyPilot" except the credit lines.

## Tasks
### Task 1: Delete the auto-apply stage
**Runs:** unattended
**Files:** `src/applypilot/apply/` (delete), `cli.py`, `pipeline.py`, `wizard/init.py`, `config.py`, `.env.example`,
`tests/test_cred_server.py`, `tests/test_prompt.py`, `tests/test_launcher.py` (delete), any other test that covers only apply, `pyproject.toml`
**What:** Remove the `apply` command and its options, the apply-only wizard steps (Claude/OpenCode CLI, CapSolver key, Chrome path),
the apply-only config keys and the `CAPSOLVER_*`/apply env vars. In `doctor`, drop the auto-apply checks. Keep the "applied" user status
and the dashboard's manual status actions. Grep for `apply.` imports, `capsolver` and `launcher` until nothing apply-related is left.
**Validate:** `grep -rniE 'capsolver|cred_server|launcher' src tests` → no hits; `scripts/validate.sh` passes;
the CLI help no longer lists `apply`.
**Status:** ✅ 2026-10-12 (this commit). Also removed the apply-only site passwords (wizard step, `SITE_PASSWORDS`, profile migration) and `sites.yaml` `manual_ats`/`blocked_sso`.

### Task 2: Rename the package and CLI to `strelitzia`, with deprecated fallbacks
**Runs:** unattended
**Files:** `git mv src/applypilot src/strelitzia`, every import, `pyproject.toml`, `Dockerfile`, `.dockerignore`, `tests/`,
`scripts/*.py`, `scripts/validate.sh`, `scripts/agent/gate.py` (`HIGH_RISK` paths)
**What:**
- `[project] name = "strelitzia"`, `version = "0.4.0"`, `authors` = Siddharth Engineer **and** Pickle-Pixel (original author).
  Scripts: `strelitzia = "strelitzia.cli:app"` plus a deprecated alias `applypilot = "strelitzia.cli:app"` (so the
  engineerfamily compose `applypilot serve` keeps working). The alias prints a one-line stderr deprecation notice.
- `strelitzia/config.py`: add `def env(name: str, default: str | None = None) -> str | None` that reads `STRELITZIA_<name>`
  and falls back to `APPLYPILOT_<name>`. Route every `APPLYPILOT_*` read through it (DATABASE_URL, DIR, etc.).
- App dir: `STRELITZIA_DIR`/`APPLYPILOT_DIR` env, else `~/.strelitzia` if it exists, else `~/.applypilot` if it exists
  (legacy, logs once), else create `~/.strelitzia`. Put this in one function, `resolve_app_dir() -> Path`.
- Default SQLite filename: `strelitzia.db`, but use an existing `applypilot.db` in the resolved dir if there's no `strelitzia.db`.
- The `Dockerfile` CMD/ENTRYPOINT uses `strelitzia`.
**Validate:** new `tests/test_legacy_compat.py`: (a) `env()` precedence, (b) `resolve_app_dir()` for all four cases with `tmp_path` HOME,
(c) a legacy `applypilot.db` is picked up, (d) both console scripts resolve to the same Typer app. `scripts/validate.sh --full` passes
(it includes the docker build). `grep -rn "applypilot" src tests | grep -v -i legacy` → only the fallback code and its tests.
**Status:** ✅ 2026-10-12 (this commit). Also: the dashboard CSRF header is now `X-Strelitzia` (the server still accepts `X-ApplyPilot` from open tabs); Drive `appProperties` keep the `applypilot_` prefix so existing files are still found; the default Drive root folder is `Strelitzia` (set `DRIVE_ROOT_FOLDER=ApplyPilot` or rename the folder to keep one tree). `.github/` is outside the agent's permissions: `ci.yml` still names `src/applypilot/web/static` (left for the user).

### Task 3: Rename the runner and tooling with fallbacks
**Runs:** unattended
**Files:** `scripts/agent/{run.sh,preprod.sh,buildqueue.py,inbox.py,usage.py,README.md,settings.json,mcp.json}`, `.claude/commands/*.md`, `.github/workflows/ci.yml`,
`.github/workflows/publish.yml` (delete)
**What:** `GH_REPO` default → `SiddharthEngineer/Strelitzia`. Config dir `~/.config/strelitzia-agent`, state dir
`~/.local/state/strelitzia-agent` and log dir `/var/log/strelitzia-agent`: each uses the new path if it exists, else the old
`applypilot-agent` path. ntfy title is "Strelitzia agent". Delete `publish.yml` (no PyPI publishing).
Don't hardcode `/home/dev/src/ApplyPilot` anywhere. Derive the repo root from the script location.
**Validate:** `bash -n scripts/agent/*.sh`; `python3 scripts/agent/buildqueue.py list` works with only the old dirs present;
`python3 scripts/agent/gate.py agents/plans/strelitzia-pivot.md --plan` runs.
**Status:** 🟡 2026-10-12 (this commit): runner, inbox, usage, preprod, validate.sh and `.claude/commands` renamed with legacy fallbacks (inbox also still recognises the old comment marker). Left for the user (`.github/` is outside the agent's permissions): delete `.github/workflows/publish.yml` and point `ci.yml` at `src/strelitzia/web/static`. No script referenced `/var/log/applypilot-agent`; the migration script prints its `sudo` move.

### Task 4: Sunrise theme and animated Strelitzia flower
**Runs:** unattended
**Files:** `dashboard/src/styles.css`, `dashboard/src/components/Flower.tsx` (new), `dashboard/src/components/Hero.tsx` (new),
`dashboard/src/components/Footer.tsx` (new), `App.tsx`, `pages/JobsPage.tsx`, `pages/JobPage.tsx`, `dashboard/index.html`,
`dashboard/public/favicon.svg` (new), `dashboard/src/__tests__/`, the rebuilt bundle in `src/strelitzia/web/static`
**What:**
- Tokens (keep the variable names, add new ones). Light: bg `#FBF6EE`, surface `#FFFFFF`, surface-2 `#F5EDE0`, primary/accent `#E8711A`,
  secondary `#3A3FA8`, success `#2F7A4F`, gold `#F2B33D`. Dark ("indigo night"): bg `#14122B`, surface `#1D1A3A`, surface-2 `#27234A`,
  accent `#FF9A3C`, secondary `#8F95FF`, success `#6FD49A`. Rework the badge colors to match. Check text/background contrast ≥ 4.5:1
  for body text and buttons. Add a vitest that parses the tokens and asserts the ratios.
- `Flower.tsx`: an original, hand-written inline SVG of a bird-of-paradise in profile (green spathe/beak, flame-orange sepals fanning up,
  royal-blue arrow-shaped petals, a short stem and a leaf), on a 64×64 viewBox, with no external assets. Props:
  `variant: "mark" | "hero" | "loader" | "seedling"` and `size`. Animation is CSS only: `hero` unfolds its petals in sequence on mount and then sways
  gently (about 6s loop) with a soft gold sun glow behind it, `loader` loops a bloom/close, `seedling` is a sprout for empty states, and `mark`
  is static with a small sway on hover. Wrap every animation in `@media (prefers-reduced-motion: no-preference)`. Add a header comment:
  "Original artwork, MIT, © 2026 Siddharth Engineer".
- Top bar: Flower mark + the "Strelitzia" wordmark. Hero banner on the jobs page: the flower, the tagline "Bring your search into the light."
  and pipeline counts from the existing API (no new endpoints). Replace the loading text with `<Flower variant="loader">` and the
  no-results notice with `seedling`. `favicon.svg` is the mark, and `<title>` is "Strelitzia".
- Footer: "Strelitzia is free software (AGPL-3.0) · Source · Based on ApplyPilot by Pickle-Pixel". Source links to
  `https://github.com/SiddharthEngineer/Strelitzia`.
- Phone width (375px): no horizontal page scroll, and the hero collapses to a single column.
**Validate:** `cd dashboard && npm test` → new tests for Flower variants, the reduced-motion CSS guard, the footer source link and token
contrast pass; `npm run build` updates the committed bundle; `scripts/validate.sh --full` passes.
**Status:** ✅ 2026-10-12 (this commit). Links use the secondary indigo (orange on cream is under 4.5:1), primary buttons use dark text on orange. The existing spinner and row-fade transitions also moved inside the reduced-motion guard.

### Task 5: Docs, license notices, version
**Runs:** unattended
**Files:** `README.md` (rewrite), `NOTICE` (new), `AGENTS.md`, `agents/DEPLOY.md`, `agents/ROADMAP.md`, `.env.example`,
`profile.example.json`, `src/strelitzia/__init__.py`
**What:** README: the Strelitzia name and meaning (freedom, joy, royalty, new beginnings; the flower needs light the way a job search
needs exposure), the tagline, the dashboard-first pipeline (no auto-apply), install/serve commands using `strelitzia`, a "Migrating from
ApplyPilot" section (fallbacks, env var rename table), and Credits/License (AGPL-3.0, originally created by Pickle-Pixel, original
repository link). Remove the upstream banner, PyPI/ko-fi/star badges and the demo video. `NOTICE`: the project name, "Copyright (C) 2026
Pickle-Pixel (Henry Muhiar), original ApplyPilot", "Modifications Copyright (C) 2026 Siddharth Engineer", "Modified 2026-10: renamed,
auto-apply removed, dashboard redesigned", AGPL-3.0 reference, and the flower artwork as MIT. `__version__ = "0.4.0"`. AGENTS.md/DEPLOY.md:
the new names. Mark the old `/srv` and `~` paths as "until the post-merge migration (M15)".
**Validate:** `grep -rn "ApplyPilot" README.md AGENTS.md` → only the credit and migration lines; `python -c "import strelitzia; print(strelitzia.__version__)"` → `0.4.0`.
**Status:** ✅ 2026-10-12 (this commit). ROADMAP: M5 marked obsolete, the apply backlog item dropped.

### Task 6: Post-merge VPS migration script (written and dry-run tested here, run later by the user)
**Runs:** unattended (writing and testing); running it for real is M15
**Files:** `scripts/migrate_to_strelitzia.sh` (new), `tests/test_migrate_script.py` (new)
**What:** An idempotent bash script. `--dry-run` is the default and prints every action. `--apply` performs them. Steps, each skipped if already done:
1. Refuse to run if `buildqueue.py list` shows a running item or `preprod.sh status` holds a lock.
2. `~/.applypilot` → `~/.strelitzia`, and `~/.applypilot-preprod` → `~/.strelitzia-preprod`, leaving **symlinks at the old paths** (DB rows hold absolute paths).
3. `~/.config/applypilot-agent` → `~/.config/strelitzia-agent`, and `~/.local/state/applypilot-agent` → `~/.local/state/strelitzia-agent`, with old-path symlinks.
4. `~/src/ApplyPilot` → `~/src/Strelitzia`: remote fetch URL `https://github.com/SiddharthEngineer/Strelitzia.git`, push URL
   `git@github-strelitzia:SiddharthEngineer/Strelitzia.git`. Append a `Host github-strelitzia` block (same key) to `~/.ssh/config` if it's missing.
   Copy `~/.claude/projects/-home-dev-src-ApplyPilot` to `-home-dev-src-Strelitzia` (session memory).
5. Rewrite the crontab line to `~/src/Strelitzia/scripts/agent/run.sh`.
6. `~/applypilot-dashboard-credentials.txt` → `~/strelitzia-dashboard-credentials.txt`.
7. For `/srv/{ApplyPilot-prod,ApplyPilot-preprod}` → `/srv/Strelitzia-*` and `/var/log/applypilot-agent`, which need root:
   **print** the exact `sudo` commands, never run them.
Tests run the script against a fake `HOME` in `tmp_path`: the dry run changes nothing, `--apply` twice gives the same result, and the symlinks resolve.
**Validate:** `pytest tests/test_migrate_script.py -q` passes; `scripts/migrate_to_strelitzia.sh` (dry run) on the VPS prints the plan and exits 0.
**Status:** ✅ 2026-10-12 (this commit). Step 1 reads `preprod.lock` directly (`preprod.sh status` can release the lock and redeploy); a dry run only warns when the runner is busy, `--apply` refuses. The dev clone also gets an old-path symlink (worktrees and the runner env file point at it), and the printed `sudo` lines leave symlinks too so the engineerfamily compose keeps working until M16.

## Validation
- `scripts/validate.sh --full` passes (ruff, pytest, dashboard tests and build, docker build).
- `strelitzia --help` lists no `apply` command. `applypilot --help` works and prints a deprecation line.
- `strelitzia serve` against fixtures: `curl -s localhost:8000/app/ | grep -c Strelitzia` ≥ 1, and `/app/api/health` returns 200.
- Preprod (deployed by the runner into the unchanged engineerfamily stack) is healthy at `/app/api/health`.

## Human test steps (preprod)
1. Open `https://applypilot-preprod.engineerfamily.net/app/` (the URL changes after M16) and log in with the preprod credentials.
2. The top bar shows the flower mark and "Strelitzia". The jobs page hero shows the flower unfolding, then swaying, with the tagline and counts.
3. Switch the OS between light and dark mode: cream/orange becomes indigo night/orange, and badges stay readable.
4. Reload with a slow network (DevTools → Slow 3G): a blooming flower appears while loading. Filter to no results: a seedling appears.
5. Turn on "reduce motion" in the OS: nothing animates.
6. Phone width (DevTools 375px): no horizontal scroll, and the hero stacks.
7. The footer "Source" link opens the GitHub repo. The favicon is the flower.
8. A job detail page has no auto-apply actions. Setting a status (e.g. Applied) still saves.

## Decisions
1. The license stays AGPL-3.0 with upstream credit, because the derived code can't be relicensed. A footer source link meets AGPL §13 for the hosted dashboard.
2. Auto-apply is deleted, not hidden. Playwright stays because discovery, enrichment and PDF use it.
3. The full rename keeps deprecated fallbacks (`applypilot` alias, `APPLYPILOT_*`, `~/.applypilot`) for 0.4.x, so prod and preprod run unchanged until the engineerfamily cutover.
4. The Postgres DB and role names stay `applypilot`. Renaming them adds downtime risk for no visible benefit.
5. The flower is original SVG art (MIT), not third-party, so no attribution or license mixing is needed.
6. Version 0.4.0, not 1.0.0: more changes come before going live (user, 2026-10-12).
7. No PyPI publishing: `publish.yml` is deleted so a tag can't publish under the upstream's package name.
8. Domain `strelitzia.engineerfamily.net`, with a 301 redirect from `applypilot.*`. That's done in engineerfamily (M16), not here.
