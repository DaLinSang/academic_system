# Evaluation

Member C owns the annotated dataset, B1-B4 batch runner, metric calculation,
failure-case analysis, and experiment-result presentation.

## Dataset v1

`datasets/annotated_v1.jsonl` contains one JSON object per sample. Its structure
is frozen by `datasets/annotated_v1.schema.json`.

Required fields:

- `id`, `question`, `user_id`, and `turn_index` identify one run input.
- `gold_route` and `gold_need_retrieval` support RQ1.
- `gold_sources` is the authoritative file-and-page annotation for RQ3.
- `gold_pages` is retained for compatibility with `docs/eval-contract.md`; it is
  derived from `gold_sources` and must not be used alone for multi-document data.
- `gold_claims` records the factual statements expected from the cited pages.
- `expected_memory` records phrases that should be available for RQ2 samples.
- `tags` defines the intended evaluation slice.

The first version contains ten routing/retrieval samples and one two-turn memory
pair. The PDF annotations were checked against the actual files in `data/raw/`.
The dataset deliberately excludes `COMP7608B_Lec04.pdf`, which is not one of the
three files specified by `docs/b-demo.md`.

## Validation

```bash
uv run pytest tests/test_evaluation_dataset.py -q
```

The validation test checks IDs, required fields, route semantics, memory turn
ordering, file existence, PDF page bounds, and consistency between
`gold_sources` and `gold_pages`.

## Batch Runner

Run a dependency-free Mock smoke test first:

```bash
uv run python -m app.evaluation.batch_runner --modes B1 B2 B3 B4 --limit 2
```

Mock mode validates dataset ordering, mode flags, memory continuity, logging, and
output structure. It uses `MockRouter` and canned answers, so its English route
predictions and answer quality must not be reported as A's real Router/model result.

Run the complete real experiment only after PostgreSQL, Ollama embedding, PDF
indexing, and the chat model are ready:

```bash
uv run python -m app.evaluation.batch_runner --real --modes B1 B2 B3 B4 --top-k 5
```

Each mode receives a fresh Orchestrator and its own `runs/<run_id>/` directory.
Samples keep their JSONL order within a mode, so the two memory samples share a
MemoryStore in B3/B4 while different baselines cannot contaminate one another.
The script never implements a second output format; it delegates all raw result
writing to A's `RunLogger`.
