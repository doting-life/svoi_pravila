"""Read-side aggregation over service_events. Request-level metrics use only stage='request' rows."""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.persistence.postgres import ServiceEventModel

REQUEST_STATUSES = ("ok", "error", "blocked", "failed")
WORKFLOWS = ("soften", "decode", "help-say")
EVENT_COLUMNS = tuple(column.name for column in ServiceEventModel.__table__.columns)


@dataclass(frozen=True, slots=True)
class DateRange:
    """Inclusive UTC date range [start, end]."""

    start: date
    end: date

    @property
    def start_at(self) -> datetime:
        return datetime.combine(self.start, time.min, tzinfo=timezone.utc)

    @property
    def end_at(self) -> datetime:
        return datetime.combine(self.end + timedelta(days=1), time.min, tzinfo=timezone.utc)


def percentile(values: list[int], pct: float) -> int | None:
    """Nearest-rank percentile (deterministic, no interpolation)."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


def _ratio(numerator: float, denominator: float) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _utc_day(value: datetime) -> date:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).date()


def event_to_dict(row: ServiceEventModel) -> dict[str, Any]:
    return {name: getattr(row, name) for name in EVENT_COLUMNS}


async def load_events(sessions: async_sessionmaker[AsyncSession], window: DateRange) -> list[dict[str, Any]]:
    async with sessions() as session:
        result = await session.execute(
            select(ServiceEventModel)
            .where(ServiceEventModel.at >= window.start_at, ServiceEventModel.at < window.end_at)
            .order_by(ServiceEventModel.at, ServiceEventModel.id)
        )
        return [event_to_dict(row) for row in result.scalars()]


def summarize(events: Iterable[dict[str, Any]], window: DateRange) -> dict[str, Any]:
    events = list(events)
    requests = [e for e in events if e["stage"] == "request"]
    # Every completed generate row is one real provider call, including calls later rejected
    # by validation and retried. Total spend therefore includes retry tokens/cost.
    llm_calls = [e for e in events if e["stage"] == "generate" and e["status"] == "completed"]
    generate_failures = [e for e in events if e["stage"] == "generate" and e["status"] == "failed"]

    users_by_day: dict[date, set[str]] = defaultdict(set)
    for e in requests:
        if e["user_id"]:
            users_by_day[_utc_day(e["at"])].add(e["user_id"])
    days = (window.end - window.start).days + 1
    daily_dau = {
        (window.start + timedelta(days=i)).isoformat(): len(users_by_day.get(window.start + timedelta(days=i), ()))
        for i in range(days)
    }
    user_days = sum(daily_dau.values())
    unique_users = len({e["user_id"] for e in requests if e["user_id"]})

    total_requests = len(requests)
    statuses = Counter(e["status"] for e in requests)
    by_workflow = Counter(e["workflow"] for e in requests)
    generated = [e for e in requests if (e["generate_attempts"] or 0) >= 1]
    retried = [e for e in generated if (e["generate_attempts"] or 0) > 1]

    input_tokens = sum(e["input_tokens"] or 0 for e in llm_calls)
    output_tokens = sum(e["output_tokens"] or 0 for e in llm_calls)
    priced_calls = [e for e in llm_calls if e["cost_usd"] is not None]
    cost = sum((Decimal(str(e["cost_usd"])) for e in priced_calls), Decimal("0"))

    providers: dict[tuple[str, str], dict[str, Any]] = {}
    for e in llm_calls:
        key = (e["provider"] or "unknown", e["model"] or "unknown")
        item = providers.setdefault(
            key,
            {"provider": key[0], "model": key[1], "calls": 0, "input_tokens": 0, "output_tokens": 0,
             "cost_usd": None, "_latencies": []},
        )
        item["calls"] += 1
        item["input_tokens"] += e["input_tokens"] or 0
        item["output_tokens"] += e["output_tokens"] or 0
        if e["cost_usd"] is not None:
            item["cost_usd"] = (item["cost_usd"] or Decimal("0")) + Decimal(str(e["cost_usd"]))
        if e["provider_latency_ms"] is not None:
            item["_latencies"].append(e["provider_latency_ms"])
    provider_rows = []
    for item in sorted(providers.values(), key=lambda value: (value["provider"], value["model"])):
        latencies = item.pop("_latencies")
        item["cost_usd"] = float(item["cost_usd"]) if item["cost_usd"] is not None else None
        item["provider_latency_p50_ms"] = percentile(latencies, 50)
        item["provider_latency_p95_ms"] = percentile(latencies, 95)
        provider_rows.append(item)

    latencies = [e["latency_ms"] for e in requests]
    # None (not 0) when no provider call reported cost: unknown spend must not look free.
    total_cost = float(cost) if priced_calls else None
    return {
        "from": window.start.isoformat(),
        "to": window.end.isoformat(),
        "days": days,
        "dau_daily": daily_dau,
        "dau_avg": _ratio(user_days, days),
        "user_days": user_days,
        "unique_users": unique_users,
        "total_requests": total_requests,
        "requests_per_dau": _ratio(total_requests, user_days),
        "requests_by_workflow": {name: by_workflow.get(name, 0) for name in WORKFLOWS},
        "requests_by_status": {name: statuses.get(name, 0) for name in REQUEST_STATUSES},
        "error_rate": _ratio(statuses.get("error", 0) + statuses.get("failed", 0), total_requests),
        "blocked_rate": _ratio(statuses.get("blocked", 0), total_requests),
        "generate_retry_rate": _ratio(len(retried), len(generated)),
        "generate_attempts_total": sum(e["generate_attempts"] or 0 for e in requests),
        "llm_calls": len(llm_calls),
        "generate_failures_by_error_type": dict(Counter(e["error_type"] or "unknown" for e in generate_failures)),
        "total_input_tokens": input_tokens,
        "total_output_tokens": output_tokens,
        "avg_input_tokens_per_request": _ratio(input_tokens, total_requests),
        "avg_output_tokens_per_request": _ratio(output_tokens, total_requests),
        "cost_reported_calls": len(priced_calls),
        "total_cost_usd": round(total_cost, 6) if total_cost is not None else None,
        "cost_per_dau": _ratio(total_cost, user_days) if total_cost is not None else None,
        "latency_p50_ms": percentile(latencies, 50),
        "latency_p95_ms": percentile(latencies, 95),
        "latency_p99_ms": percentile(latencies, 99),
        "providers": provider_rows,
    }
