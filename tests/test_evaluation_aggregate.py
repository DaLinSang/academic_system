from __future__ import annotations

import json

from app.evaluation.aggregate import aggregate_metrics, write_experiment_summary


def _run(tmp_path, name, mode, value):
    path = tmp_path / name
    path.mkdir()
    (path / "metrics.json").write_text(
        json.dumps(
            {
                "run": {"mode": mode},
                "rq1": {"route_accuracy": value, "counts": {"x": 1}},
                "rq2": {"memory_hit_rate": None},
                "rq3": {"claim_support_rate": value},
                "rq4": {"tokens_per_query": 10 + value},
            }
        ),
        encoding="utf-8",
    )
    return path


def test_aggregate_reports_mean_std_and_run_count(tmp_path) -> None:
    paths = [
        _run(tmp_path, "b1-1", "B1", 0.5),
        _run(tmp_path, "b1-2", "B1", 1.0),
        _run(tmp_path, "b4-1", "B4", 0.75),
    ]

    report = aggregate_metrics(paths)

    assert report["run_count"] == 3
    assert report["modes"]["B1"]["runs"] == 2
    metric = report["modes"]["B1"]["metrics"]["rq1.route_accuracy"]
    assert metric["mean"] == 0.75
    assert metric["std"] > 0
    assert metric["n"] == 2
    assert "rq2.memory_hit_rate" not in report["modes"]["B1"]["metrics"]

    output = write_experiment_summary(paths, tmp_path / "summary.json")
    assert json.loads(output.read_text(encoding="utf-8")) == report
