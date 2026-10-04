"""Aggregate sample_stats.ps1 CSV into per-container CPU/memory stats for a UTC window.

Usage: python loadtests/stats_window.py loadtests/results/platform-stats.csv 2026-10-04T10:00:00Z 2026-10-04T10:01:00Z
"""
from __future__ import annotations

import csv
import re
import sys
from collections import defaultdict

UNITS = {"B": 1 / 1024**2, "KiB": 1 / 1024, "MiB": 1, "GiB": 1024}


def mib(value: str) -> float:
	match = re.match(r"([\d.]+)\s*([A-Za-z]+)", value.split("/")[0].strip())
	return float(match.group(1)) * UNITS.get(match.group(2), 1) if match else 0.0


def main() -> None:
	path, start, end = sys.argv[1], sys.argv[2], sys.argv[3]
	cpu: dict[str, list[float]] = defaultdict(list)
	mem: dict[str, list[float]] = defaultdict(list)
	with open(path, encoding="utf-8-sig") as handle:
		for row in csv.DictReader(handle):
			if start <= row["utc"] < end:
				cpu[row["name"]].append(float(row["cpu_pct"].rstrip("%") or 0))
				mem[row["name"]].append(mib(row["mem_usage"]))
	for name in sorted(cpu):
		c, m = cpu[name], mem[name]
		print(f"{name}: samples={len(c)} cpu_avg={sum(c)/len(c):.1f}% cpu_max={max(c):.1f}% mem_max={max(m):.0f}MiB")


if __name__ == "__main__":
	main()
