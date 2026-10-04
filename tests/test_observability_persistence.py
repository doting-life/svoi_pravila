from __future__ import annotations

import csv
import io
import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.artifacts import AssistRequest, WorkflowName
from app.checkpoints import InMemoryCheckpointStore
from app.observability import RequestTraceSink, TraceEvent
from app.observability.metrics import EVENT_COLUMNS, DateRange, percentile, summarize
from app.observability.postgres_sink import PostgresTraceSink
from app.persistence import ServiceEventModel, build_async_engine, build_session_factory, create_schema
from app.repositories import InMemoryRelationshipRepository
from app.settings import AppSettings
from app.skills import SkillLoader
from app.stages import StageRegistry
from app.tools.llm import LLMGenerateTool
from app.tools.registry import ToolRegistry
from app.workflows.engine import WorkflowDependencies, WorkflowEngine
from scripts.export_metrics import render
from tests.test_self_check import (
    COMMITMENT_FAILURE,
    EN_BAD,
    EN_GOOD,
    EN_SOURCE,
    CountingFakeProvider,
    ScriptedProvider,
    en_request,
)

DAY = date(2026, 10, 1)
WINDOW = DateRange(DAY, DAY)
FORBIDDEN_COLUMNS = {"text", "message", "prompt", "output", "aliases", "rules", "username", "first_name", "init_data"}


@pytest_asyncio.fixture
async def sessions():
    engine = build_async_engine("sqlite+aiosqlite:///:memory:")
    await create_schema(engine)
    try:
        yield build_session_factory(engine)
    finally:
        await engine.dispose()


def make_engine(provider: Any, trace: Any) -> WorkflowEngine:
    tools = ToolRegistry()
    tools.register(LLMGenerateTool(provider, self_check_mode="required"))
    return WorkflowEngine(
        WorkflowDependencies(
            skills=SkillLoader(),
            tools=tools,
            relationships=InMemoryRelationshipRepository.demo(),
            checkpoints=InMemoryCheckpointStore(),
            trace=trace,
            stages=StageRegistry(),
        )
    )


async def stored(sessions: Any) -> list[ServiceEventModel]:
    async with sessions() as session:
        return list((await session.execute(select(ServiceEventModel).order_by(ServiceEventModel.id))).scalars())


def ev(
    user: str | None,
    *,
    stage: str = "request",
    status: str = "ok",
    workflow: str = "soften",
    latency: int = 100,
    request_id: UUID | None = None,
    attempt: int = 1,
    day: date = DAY,
    **extra: Any,
) -> dict[str, Any]:
    row = {name: None for name in EVENT_COLUMNS}
    row.update(
        at=datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc),
        request_id=request_id or uuid4(),
        user_id=user,
        workflow=workflow,
        stage=stage,
        status=status,
        attempt=attempt,
        latency_ms=latency,
    )
    row.update(extra)
    return row


# A / B ---------------------------------------------------------------------------


async def test_postgres_sink_persists_engine_trace_without_raw_text(sessions) -> None:
    sink = PostgresTraceSink(sessions)
    provider = ScriptedProvider([(EN_BAD, COMMITMENT_FAILURE), (EN_GOOD, {"all_passed": True, "failures": [], "violated_rule_ids": []})])
    result = await make_engine(provider, sink).execute(WorkflowName.HELP_SAY, en_request())
    await sink.drain()

    rows = await stored(sessions)
    [request_row] = [row for row in rows if row.stage == "request"]
    assert request_row.request_id == result.request_id
    assert request_row.user_id == "u-1"
    assert request_row.workflow == "help-say"
    assert request_row.status == result.status
    assert request_row.generate_attempts == len(provider.requests) == 2
    generates = [row for row in rows if row.stage == "generate"]
    assert len(generates) == 2 and all(row.provider == "scripted" and row.model == "test" for row in generates)

    columns = set(ServiceEventModel.__table__.columns.keys())
    assert not columns & FORBIDDEN_COLUMNS
    dumped = repr([{name: getattr(row, name) for name in columns} for row in rows])
    for raw in (EN_SOURCE, EN_GOOD, EN_BAD, "invented_commitments"):
        assert raw not in dumped


async def test_sink_ignores_unknown_metadata(sessions) -> None:
    sink = PostgresTraceSink(sessions)
    request_id = uuid4()
    await sink.record(
        TraceEvent(request_id, "soften", "request", "ok", 1, 10, metadata={"text": EN_SOURCE, "prompt": "p", "provider": "x"})
    )
    await sink.drain()
    [row] = await stored(sessions)
    assert row.provider == "x"
    assert EN_SOURCE not in repr({name: getattr(row, name) for name in EVENT_COLUMNS})


# C -------------------------------------------------------------------------------


class BrokenSessions:
    def __call__(self) -> Any:
        raise ConnectionError("database down")


async def test_persistence_failure_does_not_fail_request(caplog) -> None:
    sink = PostgresTraceSink(BrokenSessions())  # type: ignore[arg-type]
    caplog.set_level(logging.WARNING, logger="svoi_pravila.observability")
    result = await make_engine(CountingFakeProvider(), sink).execute(
        WorkflowName.SOFTEN, AssistRequest(user_id="u-1", text=EN_SOURCE, language="ru")
    )
    await sink.drain()
    assert result.status == "ok"
    assert "service_events write failed" in caplog.text
    assert "ConnectionError" in caplog.text
    assert EN_SOURCE not in caplog.text and "database down" not in caplog.text


# D / E / F / G / H / I -------------------------------------------------------------


def test_dau_counts_distinct_users_per_day() -> None:
    day2 = DAY + timedelta(days=1)
    events = [ev("a"), ev("a"), ev("b"), ev("a", day=day2), ev(None)]
    summary = summarize(events, DateRange(DAY, day2))
    assert summary["dau_daily"] == {DAY.isoformat(): 2, day2.isoformat(): 1}
    assert summary["user_days"] == 3
    assert summary["unique_users"] == 2
    assert summary["dau_avg"] == 1.5
    assert summary["total_requests"] == 5
    assert summary["requests_per_dau"] == round(5 / 3, 6)


def test_stage_and_retry_events_do_not_inflate_request_count() -> None:
    rid = uuid4()
    events = [
        ev("a", stage="receive", status="completed", request_id=rid),
        ev("a", stage="generate", status="completed", request_id=rid, attempt=1, input_tokens=100, output_tokens=40, cost_usd=0.01, provider="p", model="m", provider_latency_ms=300),
        ev("a", stage="validate", status="completed", request_id=rid),
        ev("a", stage="generate", status="completed", request_id=rid, attempt=2, input_tokens=120, output_tokens=50, cost_usd=0.02, provider="p", model="m", provider_latency_ms=500),
        ev("a", stage="validate", status="completed", request_id=rid, attempt=2),
        ev("a", request_id=rid, generate_attempts=2),
        ev("b", generate_attempts=1),
    ]
    summary = summarize(events, WINDOW)
    assert summary["total_requests"] == 2
    assert summary["requests_by_workflow"]["soften"] == 2
    assert summary["generate_retry_rate"] == 0.5
    assert summary["generate_attempts_total"] == 3
    # Retry spend is included: every completed generate row is a real provider call.
    assert summary["llm_calls"] == 2
    assert summary["total_input_tokens"] == 220
    assert summary["total_output_tokens"] == 90
    assert summary["total_cost_usd"] == 0.03
    assert summary["avg_input_tokens_per_request"] == 110
    assert summary["cost_per_dau"] == 0.015
    [provider] = summary["providers"]
    assert provider["calls"] == 2 and provider["provider"] == "p" and provider["model"] == "m"


def test_unreported_cost_is_null_not_zero() -> None:
    events = [ev("a", generate_attempts=1), ev("a", stage="generate", status="completed", provider="g", model="m", input_tokens=10)]
    summary = summarize(events, WINDOW)
    assert summary["cost_reported_calls"] == 0
    assert summary["total_cost_usd"] is None and summary["cost_per_dau"] is None
    assert summary["providers"][0]["cost_usd"] is None
    assert summary["total_input_tokens"] == 10


def test_percentiles_are_nearest_rank() -> None:
    values = list(range(1, 101))
    assert percentile(values, 50) == 50
    assert percentile(values, 95) == 95
    assert percentile(values, 99) == 99
    assert percentile([7], 99) == 7
    assert percentile([], 50) is None
    summary = summarize([ev("u", latency=v) for v in reversed(values)], WINDOW)
    assert (summary["latency_p50_ms"], summary["latency_p95_ms"], summary["latency_p99_ms"]) == (50, 95, 99)


def test_error_blocked_failed_requests_appear_in_metrics() -> None:
    events = [
        ev("a"),
        ev("a", status="error"),
        ev("b", status="blocked"),
        ev("c", status="failed", error_type="RuntimeError"),
        ev("c", stage="generate", status="failed", error_type="TimeoutError"),
    ]
    summary = summarize(events, WINDOW)
    assert summary["requests_by_status"] == {"ok": 1, "error": 1, "blocked": 1, "failed": 1}
    assert summary["error_rate"] == 0.5
    assert summary["blocked_rate"] == 0.25
    assert summary["generate_failures_by_error_type"] == {"TimeoutError": 1}


async def test_engine_failure_and_block_paths_are_persisted(sessions) -> None:
    from tests.test_terminal_paths import FailingProvider

    sink = PostgresTraceSink(sessions)
    with pytest.raises(Exception):
        await make_engine(FailingProvider(), sink).execute(WorkflowName.SOFTEN, en_request())
    await sink.drain()
    rows = await stored(sessions)
    [request_row] = [row for row in rows if row.stage == "request"]
    assert request_row.status == "failed" and request_row.error_type


# Export ---------------------------------------------------------------------------


def test_export_summary_and_events_formats() -> None:
    events = [ev("a", generate_attempts=1), ev("a", stage="generate", status="completed", cost_usd=0.01, input_tokens=5)]
    summary = json.loads(render("summary", "json", events, WINDOW))
    assert summary["total_requests"] == 1
    flat = dict(csv.reader(io.StringIO(render("summary", "csv", events, WINDOW))))
    assert flat["total_requests"] == "1"
    rows = list(csv.DictReader(io.StringIO(render("events", "csv", events, WINDOW))))
    assert len(rows) == 2 and set(rows[0]) == set(EVENT_COLUMNS)
    assert len(json.loads(render("events", "json", events, WINDOW))) == 2


async def test_container_selects_sink_by_backend() -> None:
    from app.container import build_container

    container = build_container(AppSettings(_env_file=None))
    assert isinstance(container.trace, RequestTraceSink)
    container = build_container(
        AppSettings(_env_file=None, relationship_backend="postgres", database_url="sqlite+aiosqlite:///:memory:")
    )
    try:
        assert isinstance(container.trace, PostgresTraceSink)
    finally:
        await container.aclose()
