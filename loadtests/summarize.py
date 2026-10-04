"""Summarize a k6 summary JSON (per-level sub-metrics) and/or service_events for a UTC window.

Usage:
  python loadtests/summarize.py loadtests/results/platform-summary.json
  python loadtests/summarize.py --db --from 2026-10-04T10:00:00Z --to 2026-10-04T10:05:00Z --user-prefix loadtest-
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LEVEL_METRIC = re.compile(r"(http_req_duration|http_req_failed|http_reqs|checks)\{level:([\d.]+)\}")


def k6_levels(path: Path) -> list[dict]:
	metrics = json.loads(path.read_text(encoding="utf-8"))["metrics"]
	levels: dict[str, dict] = {}
	for name, metric in metrics.items():
		match = LEVEL_METRIC.fullmatch(name)
		if match:
			levels.setdefault(match.group(2), {})[match.group(1)] = metric["values"]
	rows = []
	for level, values in sorted(levels.items(), key=lambda item: float(item[0])):
		d = values.get("http_req_duration", {})
		rows.append(
			{
				"target_rps": float(level),
				"requests": int(values.get("http_reqs", {}).get("count", 0)),
				"http_fail_rate": values.get("http_req_failed", {}).get("rate"),
				"check_pass_rate": values.get("checks", {}).get("rate"),
				"p50_ms": d.get("med"),
				"p90_ms": d.get("p(90)"),
				"p95_ms": d.get("p(95)"),
				"p99_ms": d.get("p(99)"),
				"max_ms": d.get("max"),
			}
		)
	return rows


def pct(values: list[int], p: float) -> int | None:
	if not values:
		return None
	ordered = sorted(values)
	return ordered[max(1, math.ceil(p / 100 * len(ordered))) - 1]


async def db_window(start: datetime, end: datetime, user_prefix: str | None, database_url: str | None) -> dict:
	from sqlalchemy import select

	from app.persistence import ServiceEventModel, build_async_engine, build_session_factory

	if not database_url:
		from app.settings import AppSettings

		database_url = AppSettings().database_url
	engine = build_async_engine(database_url or "")
	try:
		async with build_session_factory(engine)() as session:
			query = select(ServiceEventModel).where(ServiceEventModel.at >= start, ServiceEventModel.at < end)
			rows = list((await session.execute(query)).scalars())
	finally:
		await engine.dispose()
	if user_prefix:
		ids = {r.request_id for r in rows if r.stage == "request" and (r.user_id or "").startswith(user_prefix)}
		rows = [r for r in rows if r.request_id in ids]
	requests = [r for r in rows if r.stage == "request"]
	gens = [r for r in rows if r.stage == "generate"]
	calls = [r for r in gens if r.status == "completed"]
	seconds = (end - start).total_seconds()
	minutes = seconds / 60
	tin = sum(r.input_tokens or 0 for r in calls)
	tout = sum(r.output_tokens or 0 for r in calls)
	lat = [r.latency_ms for r in requests]
	plat = [r.provider_latency_ms for r in calls if r.provider_latency_ms is not None]
	generated = [r for r in requests if (r.generate_attempts or 0) >= 1]
	priced = [r for r in calls if r.cost_usd is not None]
	return {
		"window_utc": [start.isoformat(), end.isoformat()],
		"minutes": round(minutes, 3),
		"requests": len(requests),
		"users": len({r.user_id for r in requests if r.user_id}),
		"workflows": dict(Counter(r.workflow for r in requests)),
		"statuses": dict(Counter(r.status for r in requests)),
		"request_error_types": dict(Counter(r.error_type for r in requests if r.error_type)),
		"generate_failures": dict(Counter(r.error_type or "unknown" for r in gens if r.status == "failed")),
		"latency_ms": {k: pct(lat, v) for k, v in (("p50", 50), ("p90", 90), ("p95", 95), ("p99", 99))}
		| {"max": max(lat) if lat else None},
		"provider_latency_ms": {k: pct(plat, v) for k, v in (("p50", 50), ("p90", 90), ("p95", 95), ("p99", 99))},
		"providers": dict(Counter(f"{r.provider}/{r.model}" for r in calls)),
		"provider_calls": len(calls),
		"provider_calls_per_sec": round(len(calls) / seconds, 4) if seconds else None,
		"user_rps": round(len(requests) / seconds, 4) if seconds else None,
		"retry_rate": round(sum(1 for r in generated if r.generate_attempts > 1) / len(generated), 4) if generated else None,
		"input_tokens": tin,
		"output_tokens": tout,
		"avg_input_tokens_per_request": round(tin / len(requests), 1) if requests else None,
		"avg_output_tokens_per_request": round(tout / len(requests), 1) if requests else None,
		"input_tpm": round(tin / minutes, 1) if minutes else None,
		"output_tpm": round(tout / minutes, 1) if minutes else None,
		"total_tpm": round((tin + tout) / minutes, 1) if minutes else None,
		"self_check_status": dict(Counter(r.self_check_status for r in calls)),
		"cost_reported_calls": len(priced),
		"total_cost_usd": float(sum(r.cost_usd for r in priced)) if priced else None,
	}


def main() -> None:
	parser = argparse.ArgumentParser()
	parser.add_argument("k6_summary", nargs="?", type=Path)
	parser.add_argument("--db", action="store_true")
	parser.add_argument("--from", dest="start", type=lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")))
	parser.add_argument("--to", dest="end", type=lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")))
	parser.add_argument("--user-prefix", default=None)
	parser.add_argument("--database-url", default=None)
	args = parser.parse_args()
	out: dict = {}
	if args.k6_summary:
		out["k6_levels"] = k6_levels(args.k6_summary)
	if args.db:
		out["service_events"] = asyncio.run(db_window(args.start, args.end, args.user_prefix, args.database_url))
	print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
	main()
