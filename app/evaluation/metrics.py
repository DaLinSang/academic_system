"""Calculate the frozen RQ1-RQ4 metrics from one RunLogger directory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.core.types import RouteType
from app.evaluation.dataset import DEFAULT_DATASET, load_dataset

ROUTES = [route.value for route in RouteType]


def _ratio(numerator: int | float, denominator: int | float) -> float | None:
    return numerator / denominator if denominator else None


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"Expected object at {path}:{line_number}")
        rows.append(value)
    return rows


def _align_rows(
    rows: list[dict[str, Any]],
    dataset: list[dict[str, Any]],
    meta: dict[str, Any],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    sample_ids = meta.get("extra", {}).get("sample_ids")
    dataset_by_id = {sample["id"]: sample for sample in dataset}
    aligned = []
    for fallback_index, row in enumerate(rows):
        index = row.get("_sample_index", fallback_index)
        if not isinstance(index, int) or index < 0:
            raise ValueError(f"Invalid _sample_index={index!r}")
        if sample_ids is not None:
            try:
                sample = dataset_by_id[sample_ids[index]]
            except (IndexError, KeyError) as exc:
                raise ValueError(f"Cannot align sample index {index}") from exc
        else:
            try:
                sample = dataset[index]
            except IndexError as exc:
                raise ValueError(f"Cannot align sample index {index}") from exc
        aligned.append((sample, row))
    if meta.get("sample_count") != len(rows):
        raise ValueError("meta.sample_count does not match samples.jsonl")
    return aligned


def _citation_audit(row: dict[str, Any]) -> tuple[list[int], list[int]]:
    for event in reversed(row.get("trace", [])):
        if event.get("stage") == "generate" and event.get("event") == "citation_audit":
            detail = event.get("detail", {})
            return list(detail.get("used", [])), list(detail.get("invalid", []))
    return [], []


def calculate_metrics(
    run_dir: Path, dataset_path: Path = DEFAULT_DATASET
) -> dict[str, Any]:
    """Return a JSON-serializable metric report for a single run."""
    meta = _read_json(run_dir / "meta.json")
    rows = _read_jsonl(run_dir / "samples.jsonl")
    aligned = _align_rows(rows, load_dataset(dataset_path), meta)
    n = len(aligned)

    confusion = {gold: {pred: 0 for pred in ROUTES} for gold in ROUTES}
    route_correct = direct_total = unnecessary = retrieval_total = missed = 0
    memory_expected = memory_hits = proposed = accepted = 0
    cross_session = []
    supported = claim_total = 0
    used_total = invalid_total = cited_gold_total = gold_source_total = 0
    tokens = cost = latency = error_rows = recovered = 0

    for sample, row in aligned:
        route = row.get("route") or {}
        gold_route = sample["gold_route"]
        pred_route = route.get("route")
        if pred_route in ROUTES:
            confusion[gold_route][pred_route] += 1
        route_correct += pred_route == gold_route
        need_retrieval = bool(route.get("need_retrieval"))
        if gold_route == RouteType.DIRECT.value:
            direct_total += 1
            unnecessary += need_retrieval
        else:
            retrieval_total += 1
            missed += not need_retrieval

        expected = [text.casefold() for text in sample.get("expected_memory", [])]
        if expected:
            memory_expected += 1
            contents = " ".join(
                str(item.get("content", "")) for item in row.get("memory_context", [])
            ).casefold()
            hit = all(text in contents for text in expected)
            memory_hits += hit
            cross_session.append(
                {
                    "sample_id": sample["id"],
                    "user_id": sample["user_id"],
                    "turn_index": sample["turn_index"],
                    "expected_memory": sample["expected_memory"],
                    "memory_hit": hit,
                }
            )

        for event in row.get("trace", []):
            if event.get("stage") == "memory_write":
                detail = event.get("detail", {})
                stats = detail.get("stats", detail)
                proposed += int(stats.get("proposed", 0) or 0)
                accepted += int(stats.get("accepted", 0) or 0)

        if row.get("evidence"):
            claims = row.get("claims", [])
            claim_total += len(claims)
            supported += sum(claim.get("status") == "supported" for claim in claims)

        used, invalid = _citation_audit(row)
        used_total += len(used)
        invalid_total += len(invalid)
        citations = {item.get("index"): item for item in row.get("citations", [])}
        used_sources = {
            (citations[index].get("file_name"), citations[index].get("page_number"))
            for index in used
            if index in citations and index not in invalid
        }
        gold_sources = {
            (item["file_name"], item["page_number"])
            for item in sample.get("gold_sources", [])
        }
        cited_gold_total += len(used_sources & gold_sources)
        gold_source_total += len(gold_sources)

        usage = row.get("usage", {})
        tokens += int(usage.get("total_tokens", 0) or 0)
        cost += float(usage.get("cost_usd", 0) or 0)
        latency += float(usage.get("latency_ms", 0) or 0)
        if row.get("errors"):
            error_rows += 1
            recovered += bool(str(row.get("final_answer", "")).strip())

    return {
        "run": {
            "run_id": meta.get("run_id"),
            "mode": meta.get("generation", {}).get("mode"),
            "dataset": meta.get("dataset"),
            "sample_count": n,
        },
        "rq1": {
            "route_accuracy": _ratio(route_correct, n),
            "unnecessary_retrieval_rate": _ratio(unnecessary, direct_total),
            "missed_retrieval_rate": _ratio(missed, retrieval_total),
            "confusion_matrix": confusion,
        },
        "rq2": {
            "memory_hit_rate": _ratio(memory_hits, memory_expected),
            "memory_write_rejection_rate": _ratio(proposed - accepted, proposed),
            "cross_session_results": cross_session,
            "counts": {
                "expected": memory_expected,
                "hits": memory_hits,
                "proposed": proposed,
                "accepted": accepted,
            },
        },
        "rq3": {
            "claim_support_rate": _ratio(supported, claim_total),
            "citation_precision": _ratio(used_total - invalid_total, used_total),
            "citation_recall": _ratio(cited_gold_total, gold_source_total),
            "counts": {
                "supported_claims": supported,
                "claims": claim_total,
                "used_citations": used_total,
                "invalid_citations": invalid_total,
                "cited_gold_sources": cited_gold_total,
                "gold_sources": gold_source_total,
            },
        },
        "rq4": {
            "tokens_per_query": _ratio(tokens, n),
            "cost_usd_per_query": _ratio(cost, n),
            "latency_ms_per_query": _ratio(latency, n),
            "error_recovery_rate": _ratio(recovered, error_rows),
            "counts": {"error_queries": error_rows, "recovered_queries": recovered},
        },
    }


def write_metrics(run_dir: Path, dataset_path: Path = DEFAULT_DATASET) -> Path:
    output = run_dir / "metrics.json"
    output.write_text(
        json.dumps(calculate_metrics(run_dir, dataset_path), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Calculate RQ1-RQ4 metrics for one run")
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    args = parser.parse_args()
    print(write_metrics(args.run_dir, args.dataset))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
