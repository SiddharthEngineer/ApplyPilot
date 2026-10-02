"""Pytest configuration for ApplyPilot.

Registers custom marks and provides CLI options to opt-in to expensive tests.
Default `pytest tests/ -v` excludes live, llm, and expensive tests.
"""

import os
import shutil
import socket
import tempfile
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Hermetic guard: unit tests (not marked live/llm) may not reach the network
# or the real ~/.applypilot directory.
# ---------------------------------------------------------------------------

_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}
_ISOLATED_APP_DIR: str | None = None


class NetworkAccessBlocked(RuntimeError):
    """Raised when a unit test tries to open a non-localhost connection."""


def _is_local_address(sock: socket.socket, address: object) -> bool:
    if getattr(socket, "AF_UNIX", None) is not None and sock.family == socket.AF_UNIX:
        return True
    if isinstance(address, tuple) and address:
        host = address[0]
        if isinstance(host, bytes):
            host = host.decode(errors="replace")
        return host in _LOCAL_HOSTS
    return False


def _make_guard(original):
    def guarded(self, address, *args, **kwargs):
        if not _is_local_address(self, address):
            raise NetworkAccessBlocked(
                f"Unit test tried to connect to {address!r}. Mock the call, "
                "or mark the test @pytest.mark.live / @pytest.mark.llm."
            )
        return original(self, address, *args, **kwargs)

    return guarded


def pytest_configure(config: pytest.Config) -> None:
    """Point APPLYPILOT_DIR at a session temp dir before applypilot.config is imported.

    APP_DIR is computed at import time (src/applypilot/config.py), so this must run
    before any test module imports applypilot.
    """
    global _ISOLATED_APP_DIR
    _ISOLATED_APP_DIR = tempfile.mkdtemp(prefix="applypilot-test-")
    os.environ["APPLYPILOT_DIR"] = _ISOLATED_APP_DIR


def pytest_unconfigure(config: pytest.Config) -> None:
    if _ISOLATED_APP_DIR:
        shutil.rmtree(_ISOLATED_APP_DIR, ignore_errors=True)


@pytest.fixture(autouse=True)
def _block_network(request: pytest.FixtureRequest):
    """Block non-localhost socket connections for tests not marked live/llm."""
    if "live" in request.keywords or "llm" in request.keywords:
        yield
        return
    with (
        patch.object(socket.socket, "connect", _make_guard(socket.socket.connect)),
        patch.object(socket.socket, "connect_ex", _make_guard(socket.socket.connect_ex)),
    ):
        yield


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-live",
        action="store_true",
        default=False,
        help="Run tests marked 'live' (real network/Playwright).",
    )
    parser.addoption(
        "--run-llm",
        action="store_true",
        default=False,
        help="Run tests marked 'llm' (real Gemini API calls).",
    )
    parser.addoption(
        "--run-expensive",
        action="store_true",
        default=False,
        help="Run all expensive tests (live + llm).",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    run_live = config.getoption("--run-live") or config.getoption("--run-expensive")
    run_llm = config.getoption("--run-llm") or config.getoption("--run-expensive")

    skip_live = pytest.mark.skip(reason="need --run-live or --run-expensive option to run")
    skip_llm = pytest.mark.skip(reason="need --run-llm or --run-expensive option to run")
    skip_expensive = pytest.mark.skip(reason="need --run-live/--run-llm/--run-expensive to run")

    for item in items:
        is_live = "live" in item.keywords
        is_llm = "llm" in item.keywords
        is_expensive = "expensive" in item.keywords

        if is_expensive and not (run_live or run_llm):
            item.add_marker(skip_expensive)
        elif is_live and not run_live:
            item.add_marker(skip_live)
        elif is_llm and not run_llm:
            item.add_marker(skip_llm)


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Guard: live/llm tests must not use monkeypatch as a direct test parameter."""
    is_live = "live" in item.keywords
    is_llm = "llm" in item.keywords
    if not (is_live or is_llm):
        return

    # Check direct test function parameters (not fixture-injected)
    test_func = item.obj
    params = test_func.__code__.co_varnames[: test_func.__code__.co_argcount]
    if "monkeypatch" in params:
        pytest.fail(
            f"{item.nodeid}: @live/@llm tests must not use monkeypatch as a direct parameter. "
            "Use explicit function args (db_path, employer_keys, etc.) instead."
        )


def _has_llm_provider() -> bool:
    """Check if any LLM provider key is configured (mirrors config.py:get_tier)."""
    return bool(
        os.environ.get("GEMINI_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("OPENCODE_API_KEY")
        or os.environ.get("LLM_URL")
    )


def requires_api_key() -> None:
    """Skip current test if no LLM provider is configured."""
    if not _has_llm_provider():
        pytest.skip("No LLM provider configured (set GEMINI_API_KEY, OPENAI_API_KEY, OPENCODE_API_KEY, or LLM_URL)")


@pytest.fixture(autouse=False, scope="session")
def _set_llm_defaults():
    """For llm tests: force gemini-3.1-flash-lite and reset singletons."""
    if not _has_llm_provider():
        pytest.skip("No LLM provider for llm tests")

    os.environ.setdefault("LLM_MODEL", "gemini-3.1-flash-lite")
    os.environ.setdefault("LLM_DISCOVERY_MODEL", "gemini-3.1-flash-lite")

    import applypilot.llm as llm_mod

    llm_mod.reset_clients()

    yield

    llm_mod.reset_clients()


@pytest.fixture(autouse=False)
def _reset_llm_singletons():
    """Per-test reset of LLM singletons to avoid cross-test contamination."""
    import applypilot.llm as llm_mod

    llm_mod.reset_clients()

    yield

    llm_mod.reset_clients()
