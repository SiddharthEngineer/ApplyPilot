"""The llm_usage ledger, month-to-date spend and the monthly budget (claude-llm-provider Task 2)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import anthropic
import httpx2
import pytest

from strelitzia import llm
from strelitzia.database import init_db
from strelitzia.llm import LLMStopRun, check_budget, estimate_cost, get_client, month_spend, record_usage

USAGE = SimpleNamespace(input_tokens=1000, output_tokens=200, cache_read_input_tokens=3000,
                        cache_creation_input_tokens=0)
COST = (1000 * 0.10 + 200 * 0.50 + 3000 * 0.01) / 1e6


@pytest.fixture
def conn(monkeypatch, tmp_path):
    for var in ("LLM_MONTHLY_BUDGET_USD", "LLM_SCORING_MODEL", "LLM_SCORING_EFFORT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    c = init_db(tmp_path / "t.db")
    monkeypatch.setattr(llm, "_ledger_conn", lambda: c)
    llm.reset_clients()
    llm._warned.clear()
    yield c
    llm.reset_clients()


def _rows(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM llm_usage ORDER BY ts, id").fetchall()]


def _client(conn, text="ok", usage=USAGE):
    client = get_client("scoring")
    client._sdk = MagicMock()
    client._sdk.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn", usage=usage)
    return client


def test_chat_writes_a_row_with_its_cost(conn):
    _client(conn).chat([{"role": "user", "content": "q"}], job_url="https://x/job/1")
    (row,) = _rows(conn)
    assert row["purpose"] == "scoring" and row["model"] == "claude-haiku-5-5" and row["batch"] == 0
    assert (row["input_tokens"], row["cache_read_tokens"], row["cache_write_tokens"], row["output_tokens"]) == \
        (1000, 3000, 0, 200)
    assert row["cost_usd"] == pytest.approx(COST)
    assert row["job_url"] == "https://x/job/1" and row["user_id"] is None


def test_batch_rows_cost_half(conn):
    assert record_usage("extract", "claude-haiku-5-5", USAGE, batch=True) == pytest.approx(COST / 2)
    (row,) = _rows(conn)
    assert row["batch"] == 1 and row["cost_usd"] == pytest.approx(COST / 2)


def test_month_spend_ignores_earlier_months(conn):
    record_usage("scoring", "claude-sonnet-5-5", {"input_tokens": 1_000_000})
    conn.execute("INSERT INTO llm_usage (id, ts, cost_usd) VALUES ('old', '2000-01-31T23:59:59+00:00', 99.0)")
    conn.commit()
    assert month_spend(conn) == pytest.approx(2.00)


def test_a_failed_write_does_not_raise(conn, monkeypatch, caplog):
    conn.execute("DROP TABLE llm_usage")
    assert record_usage("scoring", "claude-haiku-5-5", USAGE) == pytest.approx(COST)
    assert "Could not record LLM usage" in caplog.text


def test_no_budget_means_no_limit(conn, caplog):
    record_usage("scoring", "claude-sonnet-5-5", {"output_tokens": 10_000_000})  # $100
    check_budget(1000.0)
    check_budget(1000.0)
    assert caplog.text.count("LLM_MONTHLY_BUDGET_USD is not set") == 1


def test_budget_stop_at_the_limit(conn, monkeypatch):
    monkeypatch.setenv("LLM_MONTHLY_BUDGET_USD", "2")
    record_usage("scoring", "claude-sonnet-5-5", {"input_tokens": 900_000})  # $1.80
    check_budget(0.20)
    with pytest.raises(LLMStopRun) as e:
        check_budget(0.21)
    assert e.value.reason == "budget" and "$1.80" in str(e.value)
    record_usage("scoring", "claude-sonnet-5-5", {"input_tokens": 100_000})  # $2.00 spent
    with pytest.raises(LLMStopRun):
        check_budget()


def test_chat_checks_the_budget_before_sending(conn, monkeypatch):
    monkeypatch.setenv("LLM_MONTHLY_BUDGET_USD", "0.5")
    record_usage("scoring", "claude-sonnet-5-5", {"output_tokens": 50_000})  # $0.50
    client = _client(conn)
    with pytest.raises(LLMStopRun):
        client.ask("q")
    client._sdk.messages.create.assert_not_called()


def test_zero_budget_means_no_limit(conn, monkeypatch):
    monkeypatch.setenv("LLM_MONTHLY_BUDGET_USD", "0")
    record_usage("scoring", "claude-sonnet-5-5", {"output_tokens": 10_000_000})
    check_budget(5.0)
    assert llm.monthly_budget() is None


def test_bad_budget_value_means_no_limit(conn, monkeypatch):
    monkeypatch.setenv("LLM_MONTHLY_BUDGET_USD", "twenty")
    record_usage("scoring", "claude-sonnet-5-5", {"output_tokens": 10_000_000})
    check_budget(5.0)


def test_estimate_cost_counts_tokens_and_guesses_output(conn):
    client = _client(conn)
    client._sdk.messages.count_tokens.return_value = SimpleNamespace(input_tokens=2000)
    requests = [client.request_params([{"role": "system", "content": "s"}, {"role": "user", "content": f"job {i}"}])
                for i in range(50)]
    output = llm.load_models_config()["purposes"]["scoring"]["output_tokens"]
    expected = 50 * (2000 * 0.10 + output * 0.50) / 1e6
    assert estimate_cost("scoring", requests) == pytest.approx(expected)
    assert estimate_cost("scoring", requests, batch=True) == pytest.approx(expected / 2)
    # Large runs are sampled, not counted one by one.
    assert client._sdk.messages.count_tokens.call_count == 2 * llm._ESTIMATE_SAMPLE
    kwargs = client._sdk.messages.count_tokens.call_args.kwargs
    assert set(kwargs) == {"model", "messages", "system", "output_config"}


def test_estimate_cost_falls_back_to_text_length(conn):
    client = _client(conn)
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages/count_tokens")
    client._sdk.messages.count_tokens.side_effect = anthropic.APIConnectionError(request=request)
    params = client.request_params([{"role": "user", "content": "x" * 3000}])
    output = llm.load_models_config()["purposes"]["scoring"]["output_tokens"]
    assert estimate_cost("scoring", [params]) == pytest.approx((1000 * 0.10 + output * 0.50) / 1e6)
    assert estimate_cost("scoring", []) == 0.0


@pytest.mark.parametrize("db_target", ["sqlite", pytest.param("pg", marks=pytest.mark.pg)], indirect=True)
def test_ledger_on_both_backends(db_target):
    c = init_db(db_target)
    init_db(db_target)  # idempotent
    record_usage("tailor", "claude-sonnet-5-5", {"input_tokens": 500_000}, conn=c)
    assert month_spend(c) == pytest.approx(1.00)
