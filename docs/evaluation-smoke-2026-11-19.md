# Evaluation Smoke Report — 2026-11-19

This report validates the experiment machinery after Issues #43 and #49. It is
not the final research table because the team cloud chat-model key was not
configured on this machine.

## Dataset coverage

- 50 samples total
- 12 direct, 25 document-retrieval, 13 complex-task
- 6 Web-required samples
- 3 second-turn memory-read samples
- Chinese and English questions across evidence/no-evidence cases

## Mock 4x3 structural run

The full dataset was run in B1-B4 three times, producing 12 independent run
directories. Every directory contains complete meta.json, samples.jsonl, and
metrics.json; experiment_summary.json contains mean, sample standard deviation,
and n for each scalar metric.

| Mode | Route accuracy | Web trigger accuracy | Cross-turn memory hit | Claim support | Tokens/query |
|---|---:|---:|---:|---:|---:|
| B1 | 0.240 ± 0.000 | 0.880 ± 0.000 | 0.000 ± 0.000 | N/A | 30.62 ± 0.00 |
| B2 | 0.500 ± 0.000 | 0.880 ± 0.000 | 0.000 ± 0.000 | N/A | 0.00 ± 0.00 |
| B3 | 0.360 ± 0.000 | 0.880 ± 0.000 | 0.333 ± 0.000 | N/A | 25.42 ± 0.00 |
| B4 | 0.360 ± 0.000 | 0.880 ± 0.000 | 0.333 ± 0.000 | 1.000 ± 0.000 | 25.42 ± 0.00 |

These values only describe deterministic Mock behavior. They must not be cited
as the performance of the real Router, Retriever, model, or SemanticVerifier.

## Real-chain smoke

One B4 q001 sample was run with PostgreSQL/pgvector, four indexed PDFs,
nomic-embed-text, and local Ollama llama3.2:1b.

- Route accuracy: 1.0
- Citation precision / recall: 1.0 / 1.0
- Evidence returned: 5 chunks
- Tokens: 4,272
- Pipeline wall latency: 55,575.63 ms
- Errors: 0

The 1B model repeated prompt-format text and inflated the factual claim count,
so it is suitable only for integration smoke. The final 50-sample 4x3 table
must be rerun with the team-frozen chat model using:

    uv run python -m app.evaluation.batch_runner --real --modes B1 B2 B3 B4 \
      --top-k 5 --repeats 3 --runs-dir runs/formal-v1
