"""Aggregate repeated B1-B4 metrics into mean and sample standard deviation."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from statistics import mean, stdev
from typing import Any


def _read_metrics(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "metrics.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected metrics object: {path}")
    return value


def _scalar_metrics(report: dict[str, Any]) -> dict[str, float]:
    values: dict[str, float] = {}
    for rq in ("rq1", "rq2", "rq3", "rq4"):
        for name, value in report.get(rq, {}).items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            values[f"{rq}.{name}"] = float(value)
    return values


def aggregate_metrics(run_dirs: list[Path]) -> dict[str, Any]:
    """Group metrics by B-mode and summarize every scalar metric."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for run_dir in run_dirs:
        report = _read_metrics(run_dir)
        mode = str(report.get("run", {}).get("mode") or "unknown")
        grouped[mode].append(report)

    modes: dict[str, Any] = {}
    for mode, reports in sorted(grouped.items()):
        scalar_reports = [_scalar_metrics(report) for report in reports]
        metric_names = sorted(set().union(*(report.keys() for report in scalar_reports)))
        summary = {}
        for name in metric_names:
            values = [report[name] for report in scalar_reports if name in report]
            summary[name] = {
                "mean": mean(values),
                "std": stdev(values) if len(values) > 1 else 0.0,
                "n": len(values),
            }
        modes[mode] = {"runs": len(reports), "metrics": summary}
    return {"run_count": len(run_dirs), "modes": modes}


def write_experiment_summary(run_dirs: list[Path], output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(aggregate_metrics(run_dirs), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output
