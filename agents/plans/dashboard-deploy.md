# Plan: Serve the Dashboard from engineerfamily
**Started:** 2026-10-03
**Status:** 🔄 In Progress

## Goal
The dashboard goes live at `https://applypilot.engineerfamily.net/app/`, behind a password. The ApplyPilot repo owns the code and its
Dockerfile. The engineerfamily repo owns how it's served: one more container (`applypilot`) in the prod compose stack,
built from the sibling checkout `/srv/ApplyPilot`, and an nginx `location /app/` with basic auth. The existing static homepage and
privacy policy at `/` and `/privacy` stay, because the Google OAuth consent screen links to them.

## Success Criteria
1. `curl -s -o /dev/null -w '%{http_code}' https://applypilot.engineerfamily.net/app/` returns 401 without credentials and 200 with them.
2. `curl -u "$U:$P" https://applypilot.engineerfamily.net/app/api/health` returns `{"ok":true,"db":"postgresql",...}`.
3. `https://applypilot.engineerfamily.net/` and `/privacy` still return 200 without auth.
4. Inside the container, a "Generate both" on one job finishes, and its Drive links appear. This proves Chromium, fonts, `~/.applypilot` and the Gemini key all work there.
5. `make applypilot-up` in `/srv/engineerfamily` rebuilds and restarts only the dashboard, so ApplyPilot changes ship without an engineerfamily release.

## Task Chain

### Task 1: Dashboard Docker image (ApplyPilot)
**Runs:** cloud (Dockerfile + `.dockerignore`); local (build on the VPS)
**Files:** `Dockerfile` (new), `.dockerignore` (new), `README.md` (modify: "Dashboard" section)
**What:** Base the image on `mcr.microsoft.com/playwright/python:v<playwright version pinned in the VPS .venv>-noble`, which ships
Chromium and its system deps. Add `apt-get install -y fonts-liberation` (the resume template needs a Times-compatible font, as on the VPS), then
`pip install ".[web,postgres]"` and `python -m playwright install chromium` only if the base image lacks the matching build. Set `WORKDIR /app`, `ENV HOME=/root`,
and `CMD ["applypilot", "serve", "--host", "0.0.0.0", "--port", "8000"]`, with a `HEALTHCHECK` on `/app/api/health`.
`.dockerignore` excludes `.env*`, `.venv`, `personal/`, `.git`, `dashboard/node_modules`, the caches and `tests/`. No secrets or user data
go into the image: everything arrives through mounts at runtime (Task 2).
**Acceptance:**
- `docker build -t applypilot-dashboard /srv/ApplyPilot` succeeds.
- `docker run --rm applypilot-dashboard applypilot --version` works.
- `docker history` shows no `.env`.
**Status:** ❌ Not started

### Task 2: Compose service in engineerfamily
**Runs:** local (VPS; cross-repo: engineerfamily, see `BUILD_AGENT.md` §0)
**Files:** `docker-compose.prod.yml` (modify), `Makefile` (modify: `applypilot-up`, `applypilot-logs`), `README.md` (modify: services
table, architecture diagram, ingress list)
**What:** Add this service:
```yaml
  applypilot:
    build:
      context: ../ApplyPilot        # sibling checkout on the VPS (/srv/ApplyPilot)
    restart: unless-stopped
    environment:
      - APPLYPILOT_DATABASE_URL=postgresql://applypilot:${APPLYPILOT_DB_PASSWORD}@analytics-db:5432/applypilot
    volumes:
      - /home/dev/.applypilot:/root/.applypilot      # host CLI runs as dev; profile, resume template, content library, Drive token, llm_usage.json
      - ../ApplyPilot/.env:/app/.env:ro              # Gemini key + LLM limits; load_env falls back to CWD .env
    depends_on:
      analytics-db: { condition: service_healthy }
    networks: [web]
```
It publishes no host port, so it's reachable only through nginx. Don't use `env_file:` for ApplyPilot's `.env`, because its `LLM_RPM_LIMITS`/`LLM_RPD_LIMITS`
values are multi-line JSON. The compose `environment:` entry overrides the host-only `127.0.0.1` DB URL, because `load_dotenv` doesn't override variables that are already set.
Check this in the container with `python -c "import os; print(os.environ['APPLYPILOT_DATABASE_URL'])"`. `make applypilot-up` = `docker compose -f
docker-compose.base.yml -f docker-compose.prod.yml up -d --build applypilot`. Don't add the service to preprod or local compose.
**Acceptance:**
- `docker compose ... config` is valid.
- After the Task 4 deploy: the `engineerfamily-applypilot-1` container is healthy, and `docker exec engineerfamily-applypilot-1 curl -s localhost:8000/app/api/health` reports postgresql.
**Status:** ❌ Not started

### Task 3: nginx route with basic auth
**Runs:** local (VPS; cross-repo: engineerfamily, see `BUILD_AGENT.md` §0)
**Files:** `services/nginx/nginx.prod.conf` (modify), `docker-compose.prod.yml` (modify: mount `./services/nginx/.htpasswd-applypilot:/etc/nginx/.htpasswd-applypilot:ro`),
`.gitignore` (modify: `services/nginx/.htpasswd*`)
**What:** In the `applypilot.engineerfamily.net` server block, add:
```nginx
location /app/ {
    auth_basic "ApplyPilot";
    auth_basic_user_file /etc/nginx/.htpasswd-applypilot;
    set $upstream_applypilot http://applypilot:8000;
    proxy_pass $upstream_applypilot;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 120s;
    client_max_body_size 2m;
}
```
This uses the `set $upstream` pattern the file already uses, so nginx starts even when the container is down. Create the credentials on the VPS (they're
untracked): user `applypilot`, a random password from `openssl rand -base64 18`, and
`docker run --rm httpd:alpine htpasswd -nbB applypilot "$PW" > /srv/engineerfamily/services/nginx/.htpasswd-applypilot`. Then
save the plain password to `/home/dev/applypilot-dashboard-credentials.txt` (mode 600). Tell the user where it is in MORNING.md, but never put the password itself there.
**Acceptance:** `docker exec engineerfamily-nginx-1 nginx -t` passes after `make up`, and success criteria 1–3 pass.
**Status:** ❌ Not started

### Task 4: Deploy to prod and verify
**Runs:** local (VPS)
**Files:** `agents/MORNING.md`, this plan
**What:** Make sure `/srv/ApplyPilot` is on `trunk` with the dashboard code and the built `web/static`. Create the `.htpasswd-applypilot` file
before deploying, because the nginx mount needs it to exist. Commit engineerfamily on `main`, push, and deploy (BUILD_AGENT §0: fast-forward `prod`, `make up`). Then run success criteria 1–5, including one
real generation from the browser UI (or with `curl` and the header), and record the results.
**Acceptance:** Success criteria 1–5 are recorded in the Historical Record, and MORNING.md has the URL, where the credentials file is, and how to redeploy (`make applypilot-up`).
**Status:** ❌ Not started

## Implementation Order
```
T1 image ─→ T2 compose ─→ T3 nginx ─→ T4 deploy + verify
```
1. Task 1  2. Task 2  3. Task 3  4. Task 4

## Key Design Decisions
1. The dashboard runs as a container in the engineerfamily stack, not as a host process, so it sits behind the existing nginx on the internal `web` network with no published port.
2. The image builds from the sibling checkout `../ApplyPilot` rather than a registry, which avoids publishing an image that runs the user's pipeline; the VPS already has both repos side by side.
3. The dashboard lives under `/app/` on the existing subdomain instead of a new subdomain like `dashboard.applypilot.engineerfamily.net`, which Cloudflare's free universal certificate doesn't cover and which would need DNS work.
4. Access control is nginx basic auth for now. A good follow-up is Cloudflare Access (Zero Trust, free tier) in front of `/app/*`, which adds SSO and makes the password unnecessary.
5. The container mounts `/home/dev/.applypilot` (the host CLI runs as `dev`), so the free-tier usage tally (`llm_usage.json`), Drive token and resume assets stay a single source of truth.

## Historical Record
- 2026-10-03: Plan created.
