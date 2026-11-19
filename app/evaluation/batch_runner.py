"""Run the same annotated samples across B1-B4 and persist raw outputs."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from app.agent.factory import build_mock_orchestrator, build_stage1_orchestrator
from app.agent.orchestrator import MODE_PRESETS, Orchestrator
from app.agent.run_logger import RunLogger
from app.core.config import settings
from app.evaluation.dataset import DEFAULT_DATASET, load_dataset

OrchestratorFactory = Callable[[int], Orchestrator]


def _factory(real: bool) -> OrchestratorFactory:
    return build_stage1_orchestrator if real else build_mock_orchestrator


def _model_name(real: bool) -> str:
    if not real:
        return "mock-model"
    api_key, _, model = settings.llm.resolve()
    return model if api_key else "mock-model"


def run_evaluation(
    *,
    samples: Sequence[dict[str, Any]],
    modes: Sequence[str],
    top_k: int = 5,
    real: bool = False,
    runs_dir: Path | None = None,
    dataset_name: str = "annotated_v1",
    orchestrator_factory: OrchestratorFactory | None = None,
    repeats: int = 1,
) -> list[Path]:
    """Run each mode independently while preserving sample order within a mode."""
    if top_k < 1:
        raise ValueError("top_k must be at least 1")
    unknown_modes = sorted(set(modes) - set(MODE_PRESETS))
    if unknown_modes:
        raise ValueError(f"Unknown evaluation modes: {unknown_modes}")
    if not samples:
        raise ValueError("At least one sample is required")
    if repeats < 1:
        raise ValueError("repeats must be at least 1")

    factory = orchestrator_factory or _factory(real)
    run_paths: list[Path] = []
    for repeat_index in range(1, repeats + 1):
        for mode in modes:
            flags = dict(MODE_PRESETS[mode])
            orchestrator = factory(top_k)
            with RunLogger(
                mode=mode,
                flags=flags,
                dataset_name=dataset_name,
                dataset_size=len(samples),
                model=_model_name(real),
                top_k=top_k,
                runs_dir=runs_dir,
                extra={
                    "runtime": "real" if real else "mock",
                    "sample_ids": [sample["id"] for sample in samples],
                    "repeat_index": repeat_index,
                    "repeat_count": repeats,
                },
            ) as run:
                for sample in samples:
                    state = orchestrator.run(
                        sample["question"],
                        session_id=f"{sample['user_id']}-session-{sample['turn_index']}",
                        user_id=sample["user_id"],
                        turn_index=sample["turn_index"],
                        **flags,
                    )
                    run.log(state)
                run_paths.append(run.run_dir)
    return run_paths


def main() -> int:
    parser = argparse.ArgumentParser(description="Run annotated samples across B1-B4")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET,
        help="UTF-8 JSONL dataset path",
    )
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=sorted(MODE_PRESETS),
        default=sorted(MODE_PRESETS),
    )
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--real", action="store_true")
    parser.add_argument("--runs-dir", type=Path, default=None)
    parser.add_argument("--repeats", type=int, default=1)
    args = parser.parse_args()

    samples = load_dataset(args.dataset)
    if args.limit is not None:
        if args.limit < 1:
            parser.error("--limit must be at least 1")
        samples = samples[: args.limit]

    paths = run_evaluation(
        samples=samples,
        modes=args.modes,
        top_k=args.top_k,
        real=args.real,
        runs_dir=args.runs_dir,
        dataset_name=args.dataset.stem,
        repeats=args.repeats,
    )
    if args.repeats > 1:
        from app.evaluation.aggregate import write_experiment_summary
        from app.evaluation.metrics import write_metrics

        for path in paths:
            write_metrics(path, args.dataset)
        output_dir = args.runs_dir or settings.runtime.runs_dir
        print(write_experiment_summary(paths, output_dir / "experiment_summary.json"))
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
