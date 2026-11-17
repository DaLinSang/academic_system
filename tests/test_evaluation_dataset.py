from __future__ import annotations

import json
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).parents[1]
DATASET = ROOT / "app" / "evaluation" / "datasets" / "annotated_v1.jsonl"
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
ROUTES = {"direct", "document_retrieval", "complex_task"}
DEMO_PDFS = {
    "comp7608_lecture_rag.pdf",
    "comp7608_lecture_agent.pdf",
    "rag_paper.pdf",
}


def _samples() -> list[dict]:
    return [json.loads(line) for line in DATASET.read_text().splitlines() if line.strip()]


def test_annotated_v1_has_unique_complete_samples() -> None:
    samples = _samples()
    assert len(samples) == 12
    assert len({sample["id"] for sample in samples}) == len(samples)
    assert all(set(sample) == REQUIRED_FIELDS for sample in samples)
    assert all(sample["question"].strip() for sample in samples)
    assert all(sample["gold_route"] in ROUTES for sample in samples)


def test_route_and_retrieval_labels_are_consistent() -> None:
    for sample in _samples():
        if sample["gold_route"] == "direct":
            assert sample["gold_need_retrieval"] is False
            assert sample["gold_sources"] == []
        else:
            assert sample["gold_need_retrieval"] is True
            assert sample["gold_sources"]


def test_gold_sources_reference_real_demo_pdf_pages() -> None:
    page_counts = {
        name: len(PdfReader(ROOT / "data" / "raw" / name).pages)
        for name in DEMO_PDFS
    }
    for sample in _samples():
        pages = []
        for source in sample["gold_sources"]:
            assert source["file_name"] in DEMO_PDFS
            page = source["page_number"]
            assert 1 <= page <= page_counts[source["file_name"]]
            pages.append(page)
        assert sample["gold_pages"] == list(dict.fromkeys(pages))


def test_memory_pair_reuses_user_and_advances_turn() -> None:
    memory_samples = [sample for sample in _samples() if "rq2" in sample["tags"]]
    assert [sample["id"] for sample in memory_samples] == ["m001", "m002"]
    assert {sample["user_id"] for sample in memory_samples} == {"eval-memory-001"}
    assert [sample["turn_index"] for sample in memory_samples] == [0, 1]
    assert memory_samples[0]["expected_memory"] == memory_samples[1]["expected_memory"]
