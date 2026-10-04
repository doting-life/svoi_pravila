"""Export privacy-safe service metrics from service_events.

Examples:
  python scripts/export_metrics.py --from 2026-10-01 --to 2026-10-31 --kind summary --format json
  python scripts/export_metrics.py --from 2026-10-01 --to 2026-10-31 --kind events --format csv --out events.csv
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.observability.metrics import EVENT_COLUMNS, DateRange, load_events, summarize  # noqa: E402
from app.persistence import build_async_engine, build_session_factory  # noqa: E402


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def _flatten(prefix: str, value: Any, out: dict[str, Any]) -> None:
    if isinstance(value, dict):
        for key, inner in value.items():
            _flatten(f"{prefix}.{key}" if prefix else str(key), inner, out)
    elif isinstance(value, list):
        for index, inner in enumerate(value):
            _flatten(f"{prefix}[{index}]", inner, out)
    else:
        out[prefix] = value


def render(kind: str, fmt: str, events: list[dict[str, Any]], window: DateRange) -> str:
    if kind == "summary":
        summary = summarize(events, window)
        if fmt == "json":
            return json.dumps(summary, ensure_ascii=False, indent=2, default=_json_default)
        flat: dict[str, Any] = {}
        _flatten("", summary, flat)
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(["metric", "value"])
        writer.writerows(flat.items())
        return buffer.getvalue()
    if fmt == "json":
        return json.dumps(events, ensure_ascii=False, indent=2, default=_json_default)
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(EVENT_COLUMNS), lineterminator="\n")
    writer.writeheader()
    writer.writerows(events)
    return buffer.getvalue()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from", dest="start", type=date.fromisoformat, required=True, help="UTC start date, inclusive")
    parser.add_argument("--to", dest="end", type=date.fromisoformat, required=True, help="UTC end date, inclusive")
    parser.add_argument("--kind", choices=("summary", "events"), default="summary")
    parser.add_argument("--format", dest="fmt", choices=("json", "csv"), default="json")
    parser.add_argument("--out", type=Path, default=None, help="Output file (default: stdout)")
    parser.add_argument("--database-url", default=None, help="Overrides DATABASE_URL")
    args = parser.parse_args(argv)
    if args.end < args.start:
        parser.error("--to must not be earlier than --from")
    return args


async def run(args: argparse.Namespace) -> str:
    database_url = args.database_url
    if not database_url:
        from app.settings import AppSettings

        database_url = AppSettings().database_url
    if not database_url:
        raise SystemExit("DATABASE_URL is required")
    engine = build_async_engine(database_url)
    try:
        window = DateRange(args.start, args.end)
        events = await load_events(build_session_factory(engine), window)
        return render(args.kind, args.fmt, events, window)
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    output = asyncio.run(run(args))
    if args.out:
        args.out.write_text(output, encoding="utf-8")
    else:
        sys.stdout.write(output)


if __name__ == "__main__":
    main()
