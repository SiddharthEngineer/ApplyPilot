#!/usr/bin/env bash
# Install ApplyPilot + test dependencies in a Claude Code cloud session (nightly routine).
# Run from the repo root. Network: PyPI only (allowed by the cloud "Trusted" allowlist).
# No browsers, API keys, or ~/.applypilot data are needed: unit tests are hermetic.
set -euo pipefail

python -m pip install --quiet -e ".[dev,web]"
# python-jobspy 1.2.0 dropped the numpy==1.26.3 pin that used to need --no-deps,
# and adds curl_cffi (browser TLS impersonation). Adopted in R2 Task 0.
python -m pip install --quiet "python-jobspy==1.2.0"

python -c "import applypilot, jobspy; print('applypilot', applypilot.__version__ if hasattr(applypilot, '__version__') else 'ok', '| jobspy ok')"
