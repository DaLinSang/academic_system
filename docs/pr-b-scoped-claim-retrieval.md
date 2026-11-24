# B: specified-PDF claim retrieval handoff

## Problem and scope

The summary path in A's PR #107 checks answer sentences after generation. Its temporary fallback calls the global `retrieve(sentence, top_k=3)` and then discards evidence from other PDFs. If three unrelated PDFs outrank the requested one, filtering afterward produces no evidence even when the requested PDF contains a matching passage.

This change adds `HybridRetriever.retrieve_for_claim(claim, file_names, top_k=3)`. It filters the in-memory BM25 corpus **before ranking** and uses `WHERE file_name = ANY(%s)` in the pgvector query. The existing `Evidence` type and public `Retriever` protocol remain unchanged; A's existing duck-typed call is compatible.

## Contract

```python
evidence = retriever.retrieve_for_claim(
    "RAG combines a retriever and a generator.",
    ["rag_paper.pdf"],
    top_k=3,
)
```

- File names are exact indexed PDF names, not inferred from the claim. Multiple names may be supplied; all results belong to that set.
- Missing or unindexed names yield `[]` and `retriever.last_warning`; the method never searches the remaining corpus. A malformed/empty name list raises `ValueError`.
- Empty or punctuation-only claims and nonpositive `top_k` yield `[]`.
- If embedding or the vector query fails, the method uses **scoped** BM25 and sets `last_warning`; a database failure while loading the corpus still raises `RetrievalError`.
- This is evidence **retrieval**, not a verdict. A/C must still check whether each returned passage supports the exact claim. A high rank alone is not proof.
- For a claim, section-overview expansion is disabled so a sentence about a conclusion is not replaced by generic abstract/method/result passages.

## Verification

Run `uv run pytest tests/test_hybrid_retriever.py -q` and `uv run pytest tests -q` from the repository root. The regression test creates 25 competing hits ranked above the target PDF in global search, then checks that file-scoped search still returns the target passage and page. Other tests check unknown-file behavior and confirm that the vector SQL applies the file filter.

These are deterministic tests with a fake embedding failure/connection. After A merges the integration, run an end-to-end Real-mode summary against two indexed PDFs and inspect the post-answer evidence and page numbers. The actual verdict quality depends on the verification model and source text.

## A: next step

1. Rebase PR #107 on the main branch that contains this PR. The current `getattr(self.retriever, "retrieve_for_claim", None)` call already matches the new signature.
2. For a named-PDF summary, pass `state.document_file_name` as `[file_name]` and use the returned `Evidence` for post-answer verification. Remove the global Top-3-then-filter fallback once the integration is confirmed; it can silently miss target-file evidence.
3. Keep the scope tied to the file whose full text was supplied to the model. Treat empty evidence and `last_warning` as **unverified**, not supported. Check multi-sentence summaries, factual lists, and cost/latency limits in PR #107.

## C: next step

1. After A integrates, refresh PR #108 from main and display the claim outcome that A returns. Show file name/page for actual supporting passages. Do not label an answer supported merely because scoped retrieval returned a passage.
2. Distinguish `unverified` (no passage, unknown file, or retrieval failure) from `unsupported` (passages checked but insufficient), using the existing UI claim state/trace rather than synthesizing a citation.

## Merge order

Merge this B PR first, then update and finish A's #107, then update and finish C's #108. A and C can continue reviewing UI/verification behavior in parallel, but the final end-to-end claim display should be checked after all three are integrated.
