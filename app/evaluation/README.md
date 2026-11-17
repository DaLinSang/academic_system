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
