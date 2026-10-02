#!/usr/bin/env bash
# Install ApplyPilot + test dependencies in a Claude Code cloud session (nightly routine).
# Run from the repo root. Network: PyPI only (allowed by the cloud "Trusted" allowlist).
# No browsers, API keys, or ~/.applypilot data are needed: unit tests are hermetic.
set -euo pipefail

python -m pip install --quiet --upgrade pip
python -m pip install --quiet -e ".[dev]"
# python-jobspy pins numpy==1.26.3, which conflicts; install it without deps,
# then add its runtime deps explicitly (see `pip show python-jobspy` -> Requires).
# pip will warn that jobspy wants older numpy/pandas/regex; the local dev .venv runs the same
# newer versions and the suite passes, so the warnings are expected.
python -m pip install --quiet python-jobspy --no-deps
python -m pip install --quiet markdownify pydantic regex requests tls-client numpy

python -c "import applypilot, jobspy; print('applypilot', applypilot.__version__ if hasattr(applypilot, '__version__') else 'ok', '| jobspy ok')"
