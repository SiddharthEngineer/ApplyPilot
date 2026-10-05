# ApplyPilot dashboard (`applypilot serve`), deployed by the engineerfamily stack (agents/plans/dashboard-deploy.md).
# No secrets or user data in the image: ~/.applypilot and the .env are mounted at runtime.
# Keep the tag in step with the playwright version in the VPS .venv, so the bundled Chromium matches.
FROM mcr.microsoft.com/playwright/python:v1.63.0-noble

# Times-compatible font for the resume template (as on the VPS).
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-liberation curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir --break-system-packages ".[web,postgres,drive]" "playwright==1.63.0"

# The container runs as the host user that owns ~/.applypilot (compose `user:`), with the same HOME path,
# so absolute paths stored in the DB (tailored resumes, cover letters) resolve inside the container too.
ENV HOME=/home/dev \
    PYTHONUNBUFFERED=1
RUN mkdir -p /home/dev && chmod 1777 /home/dev

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/app/api/health || exit 1
CMD ["applypilot", "serve", "--host", "0.0.0.0", "--port", "8000"]
