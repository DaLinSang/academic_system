"""Calculate the frozen RQ1-RQ4 metrics from one RunLogger directory."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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


def _web_search_requested(row: dict[str, Any]) -> bool:
    """Return the recorded policy decision; missing trace means no web request."""
    for event in reversed(row.get("trace", [])):
        if event.get("stage") == "web_search" and event.get("event") == "decision":
            return bool(event.get("detail", {}).get("should_search"))
    return False


def _web_search_credits(row: dict[str, Any]) -> int:
    """Return external search credits recorded by completed Web-search steps."""
    credits = 0
    for event in row.get("trace", []):
        if event.get("stage") != "web_search" or event.get("event") != "end":
            continue
        detail = event.get("detail", {})
        credits += int(detail.get("search_credits", 0) or 0)
    return credits


def _valid_http_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _pipeline_wall_latency_ms(row: dict[str, Any]) -> float:
    """Use pipeline trace timestamps so nested component latency is not double-counted."""
    events = [
        event
        for event in row.get("trace", [])
        if event.get("stage") == "pipeline" and event.get("event") in {"start", "end"}
    ]
    starts = [event for event in events if event.get("event") == "start"]
    ends = [event for event in events if event.get("event") == "end"]
    if starts and ends:
        try:
            started = datetime.fromisoformat(str(starts[0]["timestamp"]))
            finished = datetime.fromisoformat(str(ends[-1]["timestamp"]))
            return max(0.0, (finished - started).total_seconds() * 1000)
        except (KeyError, TypeError, ValueError):
            pass
    return float(row.get("usage", {}).get("latency_ms", 0) or 0)


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
    cross_turn_expected = cross_turn_hits = 0
    cross_session = []
    supported = claim_total = unverified_claims = 0
    used_total = invalid_total = cited_gold_total = gold_source_total = 0
    tokens = cost = latency = search_credits = error_rows = recovered = 0
    web_correct = web_unnecessary = web_negative = 0
    web_citations = valid_web_citations = 0

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

        gold_need_web = bool(sample.get("gold_need_web", False))
        predicted_web = _web_search_requested(row)
        web_correct += predicted_web == gold_need_web
        if not gold_need_web:
            web_negative += 1
            web_unnecessary += predicted_web

        expected = [text.casefold() for text in sample.get("expected_memory", [])]
        if expected:
            memory_expected += 1
            contents = " ".join(
                str(item.get("content", "")) for item in row.get("memory_context", [])
            ).casefold()
            hit = all(text in contents for text in expected)
            memory_hits += hit
            if int(sample.get("turn_index", 0) or 0) > 0:
                cross_turn_expected += 1
                cross_turn_hits += hit
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
            verifiable_claims = [
                claim
                for claim in claims
                if not str(claim.get("note", "")).startswith("UNVERIFIED:")
            ]
            unverified_claims += len(claims) - len(verifiable_claims)
            claim_total += len(verifiable_claims)
            supported += sum(
                claim.get("status") == "supported" for claim in verifiable_claims
            )

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

        evidence = {
            item.get("chunk_id"): item
            for item in row.get("evidence", [])
            if item.get("chunk_id")
        }
        supported_chunks = {
            chunk_id
            for claim in row.get("claims", [])
            if claim.get("status") in {"supported", "partially_supported"}
            for chunk_id in claim.get("supporting_evidence", [])
        }
        for index in used:
            citation = citations.get(index)
            if not citation or index in invalid or not citation.get("url"):
                continue
            web_citations += 1
            chunk_id = citation.get("chunk_id")
            matching_evidence = evidence.get(chunk_id, {})
            if (
                _valid_http_url(citation.get("url"))
                and matching_evidence.get("url") == citation.get("url")
                and chunk_id in supported_chunks
            ):
                valid_web_citations += 1

        usage = row.get("usage", {})
        tokens += int(usage.get("total_tokens", 0) or 0)
        cost += float(usage.get("cost_usd", 0) or 0)
        latency += _pipeline_wall_latency_ms(row)
        search_credits += _web_search_credits(row)
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
            "web_search_trigger_accuracy": _ratio(web_correct, n),
            "unnecessary_web_search_rate": _ratio(web_unnecessary, web_negative),
            "web_counts": {
                "correct": web_correct,
                "samples": n,
                "unnecessary": web_unnecessary,
                "gold_no_web": web_negative,
            },
        },
        "rq2": {
            "memory_hit_rate": _ratio(memory_hits, memory_expected),
            "cross_turn_memory_hit_rate": _ratio(
                cross_turn_hits, cross_turn_expected
            ),
            "memory_write_rejection_rate": _ratio(proposed - accepted, proposed),
            "cross_session_results": cross_session,
            "counts": {
                "expected": memory_expected,
                "hits": memory_hits,
                "proposed": proposed,
                "accepted": accepted,
                "cross_turn_expected": cross_turn_expected,
                "cross_turn_hits": cross_turn_hits,
            },
        },
        "rq3": {
            "claim_support_rate": _ratio(supported, claim_total),
            "citation_precision": _ratio(used_total - invalid_total, used_total),
            "citation_recall": _ratio(cited_gold_total, gold_source_total),
            "web_citation_valid_rate": _ratio(
                valid_web_citations, web_citations
            ),
            "counts": {
                "supported_claims": supported,
                "claims": claim_total,
                "unverified_claims": unverified_claims,
                "used_citations": used_total,
                "invalid_citations": invalid_total,
                "cited_gold_sources": cited_gold_total,
                "gold_sources": gold_source_total,
                "web_citations": web_citations,
                "valid_web_citations": valid_web_citations,
            },
        },
        "rq4": {
            "tokens_per_query": _ratio(tokens, n),
            "cost_usd_per_query": _ratio(cost, n),
            "search_credits_per_query": _ratio(search_credits, n),
            "latency_ms_per_query": _ratio(latency, n),
            "error_recovery_rate": _ratio(recovered, error_rows),
            "counts": {
                "search_credits": search_credits,
                "error_queries": error_rows,
                "recovered_queries": recovered,
            },
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
