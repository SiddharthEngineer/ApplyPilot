"""Message Batches runner for the bulk LLM stages (extract, scoring): half price, results within 24 hours.

`run_batch` submits (custom_id, params) requests, records each batch in `llm_batches`, polls until it ends, then hands
every result to `on_result(custom_id, text, error)` and writes its `llm_usage` row. A run killed while waiting leaves
its batches open; `resume_open_batches` collects them on the next run, so nothing already paid for is lost.
"""

import json
import logging
import re
import time
from collections.abc import Callable
from datetime import datetime, timezone

import anthropic

from strelitzia import llm
from strelitzia.llm import LLMResponseError, get_client, record_usage, response_text

log = logging.getLogger(__name__)

# on_result(custom_id, text, error): exactly one of text and error is set.
OnResult = Callable[[str, str | None, str | None], None]

CUSTOM_ID = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
# API limits per batch: 100,000 requests or 256 MB. The byte cap leaves room for the JSON envelope.
_MAX_REQUESTS = 100_000
_MAX_BYTES = 200 * 1024 * 1024

_sleep = time.sleep  # tests replace this


def _chunks(items: list[tuple[str, dict]]) -> list[list[tuple[str, dict]]]:
    """Split items so each batch stays under the request-count and size limits."""
    chunks: list[list[tuple[str, dict]]] = [[]]
    size = 0
    for item in items:
        item_size = len(json.dumps(item[1]))
        if chunks[-1] and (len(chunks[-1]) >= _MAX_REQUESTS or size + item_size > _MAX_BYTES):
            chunks.append([])
            size = 0
        chunks[-1].append(item)
        size += item_size
    return [c for c in chunks if c]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _set_status(batch_id: str, status: str) -> None:
    conn = llm._ledger_conn()
    conn.execute("UPDATE llm_batches SET status = ? WHERE id = ?", (status, batch_id))
    conn.commit()


def _error_text(result) -> str:
    """A short reason for a result that isn't `succeeded` (errored, canceled, expired)."""
    if result.type != "errored":
        return result.type
    err = getattr(result.error, "error", None)
    return f"errored: {getattr(err, 'type', 'error')}: {getattr(err, 'message', '')}".rstrip(": ")


def _wait(client: llm.LLMClient, batch_id: str, poll_seconds: float) -> None:
    while True:
        batch = client.sdk.messages.batches.retrieve(batch_id)
        if batch.processing_status == "ended":
            return
        counts = batch.request_counts
        log.info("Batch %s: %s (%d processing, %d done)", batch_id, batch.processing_status,
                 counts.processing, counts.succeeded + counts.errored + counts.canceled + counts.expired)
        _sleep(poll_seconds)


def _collect(client: llm.LLMClient, purpose: str, batch_id: str, on_result: OnResult, stats: dict) -> None:
    """Stream an ended batch's results to on_result (any order, keyed by custom_id) and record their usage."""
    for item in client.sdk.messages.batches.results(batch_id):
        text, error = None, None
        result = item.result
        if result.type == "succeeded":
            stats["cost_usd"] += record_usage(purpose, result.message.model, result.message.usage, batch=True)
            try:
                text = response_text(result.message)
            except LLMResponseError as e:
                error = str(e)
        else:
            error = _error_text(result)
        stats["succeeded" if error is None else "errored"] += 1
        try:
            on_result(item.custom_id, text, error)
        except Exception:  # noqa: BLE001 - one bad result must not drop the rest of a paid batch
            log.exception("Handling batch result %s failed", item.custom_id)
            stats["callback_errors"] += 1
    _set_status(batch_id, "collected")


def _new_stats() -> dict:
    return {"batches": 0, "succeeded": 0, "errored": 0, "callback_errors": 0, "cost_usd": 0.0}


def run_batch(purpose: str, items: list[tuple[str, dict]], on_result: OnResult, poll_seconds: float = 30) -> dict:
    """Submit `items` (custom_id, Messages API params) as Message Batches, wait for them, and deliver every result.

    Returns {"batches", "succeeded", "errored", "callback_errors", "cost_usd"}. The caller checks the budget first.
    """
    ids = [cid for cid, _ in items]
    bad = [cid for cid in ids if not CUSTOM_ID.match(cid)]
    if bad:
        raise ValueError(f"Invalid batch custom_id(s) (need [a-zA-Z0-9_-]{{1,64}}): {bad[:3]}")
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate batch custom_ids")
    stats = _new_stats()
    if not items:
        return stats
    client = get_client(purpose)
    batch_ids = []
    for chunk in _chunks(items):
        try:
            batch = client.sdk.messages.batches.create(
                requests=[{"custom_id": cid, "params": params} for cid, params in chunk])
        except anthropic.APIError as e:
            raise llm._api_error(e) from e
        conn = llm._ledger_conn()
        conn.execute(
            "INSERT INTO llm_batches (id, purpose, submitted_at, status, request_count) VALUES (?, ?, ?, ?, ?)",
            (batch.id, purpose, _now(), "submitted", len(chunk)),
        )
        conn.commit()
        log.info("Submitted %s batch %s (%d requests)", purpose, batch.id, len(chunk))
        batch_ids.append(batch.id)
    for batch_id in batch_ids:
        try:
            _wait(client, batch_id, poll_seconds)
            _collect(client, purpose, batch_id, on_result, stats)
        except anthropic.APIError as e:  # the batch stays open; the next run's resume_open_batches collects it
            raise llm._api_error(e) from e
        stats["batches"] += 1
    return stats


def open_batches(purpose: str | None = None) -> list[dict]:
    """Batches submitted but not yet collected (all purposes when `purpose` is None), oldest first."""
    sql = "SELECT * FROM llm_batches WHERE status = 'submitted'"
    params: tuple = ()
    if purpose is not None:
        sql += " AND purpose = ?"
        params = (purpose,)
    rows = llm._ledger_conn().execute(sql + " ORDER BY submitted_at, id", params).fetchall()
    return [dict(r) for r in rows]


def resume_open_batches(purpose: str, on_result: OnResult, poll_seconds: float = 30) -> dict:
    """Wait for and collect the `purpose` batches a killed run left open. Same stats as run_batch."""
    stats = _new_stats()
    pending = open_batches(purpose)
    if not pending:
        return stats
    client = get_client(purpose)
    for row in pending:
        log.info("Collecting %s batch %s left open since %s", purpose, row["id"], row["submitted_at"])
        try:
            _wait(client, row["id"], poll_seconds)
        except anthropic.NotFoundError:
            log.warning("Batch %s no longer exists (results are kept 29 days); dropping it.", row["id"])
            _set_status(row["id"], "lost")
            continue
        except anthropic.APIError as e:
            raise llm._api_error(e) from e
        try:
            _collect(client, purpose, row["id"], on_result, stats)
        except anthropic.APIError as e:
            raise llm._api_error(e) from e
        stats["batches"] += 1
    return stats
