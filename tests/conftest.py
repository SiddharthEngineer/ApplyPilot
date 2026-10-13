"""Pytest configuration for Strelitzia.

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
# or the real ~/.strelitzia directory.
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
    """Point STRELITZIA_DIR at a session temp dir before strelitzia.config is imported.

    APP_DIR is computed at import time (src/strelitzia/config.py), so this must run
    before any test module imports strelitzia.
    """
    global _ISOLATED_APP_DIR
    _ISOLATED_APP_DIR = tempfile.mkdtemp(prefix="strelitzia-test-")
    os.environ["STRELITZIA_DIR"] = _ISOLATED_APP_DIR


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


@pytest.fixture(autouse=True)
def _no_user_database_url(monkeypatch: pytest.MonkeyPatch):
    """Unit tests never use the developer's Postgres job store (STRELITZIA_DATABASE_URL from a shell or .env).

    Tests that want Postgres use the `pg_db` / `db_target` fixtures (STRELITZIA_TEST_DATABASE_URL).
    """
    monkeypatch.delenv("STRELITZIA_DATABASE_URL", raising=False)
    monkeypatch.delenv("APPLYPILOT_DATABASE_URL", raising=False)  # legacy name


def _test_database_url() -> str | None:
    return os.environ.get("STRELITZIA_TEST_DATABASE_URL") or os.environ.get("APPLYPILOT_TEST_DATABASE_URL") or None


def _reset_pg_schema(url: str) -> None:
    from strelitzia.db_pg import PgConnection

    conn = PgConnection(url)
    try:
        for table in ("jobs", "status_events", "dashboard_tasks", "llm_usage"):
            conn.execute(f"DROP TABLE IF EXISTS {table}")
    finally:
        conn.close()


@pytest.fixture
def pg_db():
    """A fresh `jobs` table in STRELITZIA_TEST_DATABASE_URL (a throwaway DB such as applypilot_test).

    Yields the URL; `init_db(url)` / `get_connection(url)` open it. Skips when the variable is unset.
    """
    url = _test_database_url()
    if not url:
        pytest.skip("STRELITZIA_TEST_DATABASE_URL not set (Postgres test tier)")
    pytest.importorskip("psycopg")
    from strelitzia.database import close_connection

    close_connection(url)
    _reset_pg_schema(url)
    yield url
    close_connection(url)


@pytest.fixture
def db_target(request: pytest.FixtureRequest, tmp_path):
    """A database target for backend-parametrized tests: a temp SQLite path, or the Postgres test URL.

    Use with ``@pytest.mark.parametrize("db_target", ["sqlite", pytest.param("pg", marks=pytest.mark.pg)],
    indirect=True)``.
    """
    if request.param == "pg":
        yield request.getfixturevalue("pg_db")
    else:
        yield tmp_path / "jobs.db"


@pytest.fixture(autouse=True)
def _isolate_env_file(request: pytest.FixtureRequest):
    """Keep load_env() away from the repo-root .env (its CWD fallback) in unit tests.

    Otherwise CLI tests that run _bootstrap() pull the developer's real keys and limits into os.environ.
    Only the isolated STRELITZIA_DIR/.env is loaded. Tests marked live/llm keep the real loader.
    Patched at ``dotenv.load_dotenv`` because config.load_env imports it at call time, which covers
    modules that bound ``load_env`` by name (pipeline.py, cli.py).
    """
    if "live" in request.keywords or "llm" in request.keywords:
        yield
        return
    import dotenv

    real_load_dotenv = dotenv.load_dotenv

    def _no_cwd_fallback(dotenv_path=None, *args, **kwargs):
        # load_env() calls load_dotenv(ENV_PATH) and then load_dotenv() (search from CWD); drop the latter.
        if dotenv_path is None and not kwargs.get("stream"):
            return False
        return real_load_dotenv(dotenv_path, *args, **kwargs)

    with patch.object(dotenv, "load_dotenv", _no_cwd_fallback):
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
        help="Run tests marked 'llm' (real Claude API calls).",
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
    """Check if the Claude API key is configured (mirrors config.py:get_tier)."""
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def requires_api_key() -> None:
    """Skip current test if no Claude API key is configured."""
    if not _has_llm_provider():
        pytest.skip("No Claude API key configured (set ANTHROPIC_API_KEY)")


@pytest.fixture(autouse=False, scope="session")
def _set_llm_defaults():
    """For llm tests: skip without a key, and reset the per-stage clients around the session."""
    if not _has_llm_provider():
        pytest.skip("No Claude API key for llm tests")

    import strelitzia.llm as llm_mod

    llm_mod.reset_clients()

    yield

    llm_mod.reset_clients()


@pytest.fixture(autouse=False)
def _reset_llm_singletons():
    """Per-test reset of LLM singletons to avoid cross-test contamination."""
    import strelitzia.llm as llm_mod

    llm_mod.reset_clients()

    yield

    llm_mod.reset_clients()
