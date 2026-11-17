from __future__ import annotations

import json

from app.evaluation.metrics import calculate_metrics, write_metrics


def _write_json(path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _write_run(tmp_path, samples, rows):
    dataset_path = tmp_path / "dataset.jsonl"
    dataset_path.write_text(
        "\n".join(json.dumps(sample, ensure_ascii=False) for sample in samples) + "\n",
        encoding="utf-8",
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_json(
        run_dir / "meta.json",
        {
            "run_id": "test-B4",
            "generation": {"mode": "B4"},
            "dataset": {"name": "test", "size": len(samples)},
            "sample_count": len(rows),
            "extra": {"sample_ids": [sample["id"] for sample in samples]},
        },
    )
    (run_dir / "samples.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    return run_dir, dataset_path


def _sample(sample_id, route, *, sources=None, expected_memory=None):
    sources = sources or []
    return {
        "id": sample_id,
        "question": f"question {sample_id}",
        "user_id": "user-1",
        "turn_index": 1 if expected_memory else 0,
        "gold_route": route,
        "gold_need_retrieval": route != "direct",
        "gold_sources": sources,
        "gold_pages": sorted({source["page_number"] for source in sources}),
        "gold_claims": [],
        "expected_memory": expected_memory or [],
        "tags": ["test"],
    }


def _row(index, route, need_retrieval, **overrides):
    row = {
        "_sample_index": index,
        "route": {"route": route, "need_retrieval": need_retrieval},
        "memory_context": [],
        "evidence": [],
        "claims": [],
        "citations": [],
        "trace": [],
        "usage": {"total_tokens": 0, "cost_usd": 0, "latency_ms": 0},
        "errors": [],
        "final_answer": "answer",
    }
    row.update(overrides)
    return row


def test_calculate_metrics_covers_rq1_to_rq4(tmp_path) -> None:
    source = {"file_name": "doc.pdf", "page_number": 3}
    samples = [
        _sample("s1", "direct"),
        _sample("s2", "document_retrieval", sources=[source], expected_memory=["English"]),
    ]
    rows = [
        _row(
            0,
            "direct",
            False,
            trace=[
                {
                    "stage": "memory_write",
                    "event": "stats",
                    "detail": {"stats": {"proposed": 2, "accepted": 1}},
                }
            ],
            usage={"total_tokens": 10, "cost_usd": 0.1, "latency_ms": 100},
        ),
        _row(
            1,
            "direct",
            False,
            memory_context=[{"content": "The user prefers English answers."}],
            evidence=[{"chunk_id": "c1"}],
            claims=[
                {"status": "supported"},
                {"status": "partially_supported"},
            ],
            citations=[{"index": 1, **source}],
            trace=[
                {
                    "stage": "generate",
                    "event": "citation_audit",
                    "detail": {"used": [1, 2], "invalid": [2]},
                }
            ],
            usage={"total_tokens": 30, "cost_usd": 0.3, "latency_ms": 300},
            errors=["retrieval failed"],
        ),
    ]
    run_dir, dataset_path = _write_run(tmp_path, samples, rows)

    metrics = calculate_metrics(run_dir, dataset_path)

    assert metrics["rq1"]["route_accuracy"] == 0.5
    assert metrics["rq1"]["unnecessary_retrieval_rate"] == 0
    assert metrics["rq1"]["missed_retrieval_rate"] == 1
    assert metrics["rq1"]["confusion_matrix"]["document_retrieval"]["direct"] == 1
    assert metrics["rq2"]["memory_hit_rate"] == 1
    assert metrics["rq2"]["memory_write_rejection_rate"] == 0.5
    assert metrics["rq2"]["cross_session_results"][0]["memory_hit"] is True
    assert metrics["rq3"]["claim_support_rate"] == 0.5
    assert metrics["rq3"]["citation_precision"] == 0.5
    assert metrics["rq3"]["citation_recall"] == 1
    assert metrics["rq4"]["tokens_per_query"] == 20
    assert metrics["rq4"]["cost_usd_per_query"] == 0.2
    assert metrics["rq4"]["latency_ms_per_query"] == 200
    assert metrics["rq4"]["error_recovery_rate"] == 1

    output = write_metrics(run_dir, dataset_path)
    assert json.loads(output.read_text(encoding="utf-8")) == metrics


def test_zero_denominators_are_null(tmp_path) -> None:
    samples = [_sample("s1", "direct")]
    rows = [_row(0, "direct", False)]
    run_dir, dataset_path = _write_run(tmp_path, samples, rows)

    metrics = calculate_metrics(run_dir, dataset_path)

    assert metrics["rq1"]["missed_retrieval_rate"] is None
    assert metrics["rq2"]["memory_hit_rate"] is None
    assert metrics["rq2"]["memory_write_rejection_rate"] is None
    assert metrics["rq3"]["claim_support_rate"] is None
    assert metrics["rq3"]["citation_precision"] is None
    assert metrics["rq3"]["citation_recall"] is None
    assert metrics["rq4"]["error_recovery_rate"] is None


def test_unverified_claims_are_reported_but_excluded_from_support_rate(tmp_path) -> None:
    samples = [_sample("s1", "document_retrieval")]
    rows = [
        _row(
            0,
            "document_retrieval",
            True,
            evidence=[{"chunk_id": "c1"}],
            claims=[
                {
                    "status": "unsupported",
                    "note": "UNVERIFIED: conflicting evidence",
                }
            ],
        )
    ]
    run_dir, dataset_path = _write_run(tmp_path, samples, rows)

    metrics = calculate_metrics(run_dir, dataset_path)

    assert metrics["rq3"]["claim_support_rate"] is None
    assert metrics["rq3"]["counts"]["claims"] == 0
    assert metrics["rq3"]["counts"]["unverified_claims"] == 1
