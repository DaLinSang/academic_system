"""Load and validate Member C's annotated JSONL dataset."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.types import RouteType

DEFAULT_DATASET = Path(__file__).with_name("datasets") / "annotated_v1.jsonl"
REQUIRED_FIELDS = {
    "id",
    "question",
    "user_id",
    "turn_index",
    "gold_route",
    "gold_need_retrieval",
    "gold_sources",
    "gold_pages",
    "gold_claims",
    "expected_memory",
    "tags",
}
OPTIONAL_FIELDS = {"gold_need_web"}


def _validate_sample(sample: dict[str, Any], *, line_number: int) -> None:
    missing = REQUIRED_FIELDS - set(sample)
    extra = set(sample) - REQUIRED_FIELDS - OPTIONAL_FIELDS
    if missing or extra:
        raise ValueError(
            f"Dataset line {line_number} has invalid fields; "
            f"missing={sorted(missing)}, extra={sorted(extra)}"
        )
    if not isinstance(sample["id"], str) or not sample["id"].strip():
        raise ValueError(f"Dataset line {line_number} has an empty id")
    if not isinstance(sample["question"], str) or not sample["question"].strip():
        raise ValueError(f"Dataset line {line_number} has an empty question")
    if sample["gold_route"] not in {route.value for route in RouteType}:
        raise ValueError(
            f"Dataset line {line_number} has invalid gold_route={sample['gold_route']!r}"
        )
    if not isinstance(sample["turn_index"], int) or sample["turn_index"] < 0:
        raise ValueError(f"Dataset line {line_number} has an invalid turn_index")
    if "gold_need_web" in sample and not isinstance(sample["gold_need_web"], bool):
        raise ValueError(f"Dataset line {line_number} has an invalid gold_need_web")


def load_dataset(path: Path = DEFAULT_DATASET) -> list[dict[str, Any]]:
    """Read a UTF-8 JSONL dataset and reject incomplete or duplicate samples."""
    samples: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw_line.strip():
            continue
        try:
            sample = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Dataset line {line_number} is not valid JSON: {exc}") from exc
        if not isinstance(sample, dict):
            raise ValueError(f"Dataset line {line_number} must contain a JSON object")
        _validate_sample(sample, line_number=line_number)
        sample.setdefault("gold_need_web", False)
        sample_id = sample["id"]
        if sample_id in seen_ids:
            raise ValueError(f"Dataset contains duplicate id {sample_id!r}")
        seen_ids.add(sample_id)
        samples.append(sample)
    if not samples:
        raise ValueError(f"Dataset is empty: {path}")
    return samples
