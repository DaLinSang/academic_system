from __future__ import annotations

import json

import pytest

from app.evaluation.batch_runner import run_evaluation
from app.evaluation.dataset import DEFAULT_DATASET, load_dataset


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_load_dataset_returns_frozen_v1_samples() -> None:
    samples = load_dataset()
    assert 50 <= len(samples) <= 80
    assert samples[0]["id"] == "q001"
    assert all("gold_need_web" in sample for sample in samples)


def test_load_dataset_rejects_duplicate_ids(tmp_path) -> None:
    first = DEFAULT_DATASET.read_text(encoding="utf-8").splitlines()[0]
    duplicate = tmp_path / "duplicate.jsonl"
    duplicate.write_text(f"{first}\n{first}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate id"):
        load_dataset(duplicate)


def test_mock_batch_runner_writes_separate_comparable_runs(tmp_path) -> None:
    samples = load_dataset()[:2]
    paths = run_evaluation(
        samples=samples,
        modes=["B1", "B4"],
        top_k=3,
        runs_dir=tmp_path,
    )

    assert len(paths) == 2
    b1_meta = _read_json(paths[0] / "meta.json")
    b4_meta = _read_json(paths[1] / "meta.json")
    assert b1_meta["generation"]["mode"] == "B1"
    assert b4_meta["generation"]["mode"] == "B4"
    assert b1_meta["dataset"] == b4_meta["dataset"] == {
        "name": "annotated_v1",
        "size": 2,
    }
    assert b1_meta["extra"]["sample_ids"] == ["q001", "q002"]

    b1_rows = _read_jsonl(paths[0] / "samples.jsonl")
    b4_rows = _read_jsonl(paths[1] / "samples.jsonl")
    assert [row["question"] for row in b1_rows] == [row["question"] for row in b4_rows]
    assert all(row["evidence"] == [] for row in b1_rows)
    assert all(row["claims"] == [] for row in b4_rows)
    assert all(
        any(
            event["stage"] == "verify"
            and event["event"] == "skip"
            and event["detail"].get("reason") == "no_document_evidence"
            for event in row["trace"]
        )
        for row in b4_rows
    )


def test_batch_runner_preserves_memory_pair_identity_and_turns(tmp_path) -> None:
    samples = load_dataset()[-2:]
    path = run_evaluation(
        samples=samples,
        modes=["B4"],
        runs_dir=tmp_path,
    )[0]
    rows = _read_jsonl(path / "samples.jsonl")

    expected_user = samples[0]["user_id"]
    assert [row["user_id"] for row in rows] == [expected_user] * 2
    assert [row["turn_index"] for row in rows] == [0, 1]
    assert rows[0]["session_id"] != rows[1]["session_id"]


def test_batch_runner_rejects_unknown_mode(tmp_path) -> None:
    with pytest.raises(ValueError, match="Unknown evaluation modes"):
        run_evaluation(
            samples=load_dataset()[:1],
            modes=["B5"],
            runs_dir=tmp_path,
        )


def test_repeated_runner_creates_four_by_three_independent_runs(tmp_path) -> None:
    paths = run_evaluation(
        samples=load_dataset()[:1],
        modes=["B1", "B2", "B3", "B4"],
        repeats=3,
        runs_dir=tmp_path,
    )

    assert len(paths) == 12
    meta = [_read_json(path / "meta.json") for path in paths]
    assert {item["generation"]["mode"] for item in meta} == {
        "B1", "B2", "B3", "B4"
    }
    assert {item["extra"]["repeat_index"] for item in meta} == {1, 2, 3}
    assert all(item["extra"]["repeat_count"] == 3 for item in meta)


def test_batch_runner_rejects_invalid_repeat_count(tmp_path) -> None:
    with pytest.raises(ValueError, match="repeats"):
        run_evaluation(
            samples=load_dataset()[:1],
            modes=["B1"],
            repeats=0,
            runs_dir=tmp_path,
        )
