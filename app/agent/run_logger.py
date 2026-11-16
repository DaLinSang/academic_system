"""实验运行记录器（A 负责）。

职责边界
--------
**只负责"把一次运行原样落盘"，不负责算任何指标。**

    本模块（A）：目录结构、元信息、逐样本 JSONL
    app/evaluation（C）：读这些 JSONL，算准确率 / 支持率 / 成本

把两者分开的理由：指标口径会随实验设计反复调整，但"原始记录"必须一次写对、
永不改动。否则换了指标定义就没法回头重算。

为什么需要它
------------
在此之前，`AgentState` 只在内存里，`trace` 跑完就丢。评测脚本要么自己写一套落盘逻辑
（各写各的，字段不一致），要么只能重跑实验。CONTRIBUTING 里要求每次实验记录
「commit hash / 模型 / 生成参数 / 数据版本 / 运行时间」五项，没有它就只能靠手抄。

产物结构
--------
    runs/<run_id>/
      meta.json      运行身份与汇总（五项上下文 + 计数 + 总量）
      samples.jsonl  一行一个 AgentState，直接可被 pandas / jq 读

用法
----
    from app.agent.factory import build_stage1_orchestrator
    from app.agent.orchestrator import MODE_PRESETS
    from app.agent.run_logger import RunLogger

    orch = build_stage1_orchestrator()
    with RunLogger(mode="B4", flags=MODE_PRESETS["B4"], dataset_name="annotated_v1") as run:
        for question in questions:
            run.log(orch.run(question, **MODE_PRESETS["B4"]))

异常策略
--------
**写不进去就直接抛。** 这不是流水线阶段，不需要降级——评测脚本静默丢数据
比崩溃危险得多，因为你会拿着一份残缺的结果去写报告。
"""

from __future__ import annotations

import json
import platform
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import PROJECT_ROOT, settings
from app.core.types import AgentState

__all__ = ["RunLogger"]


def _git_commit() -> str:
    """当前提交短哈希。取不到就写 unknown，不让它阻断实验。"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def _unique_dir(base: Path, run_id: str) -> Path:
    """同名 run 已存在时加序号，避免两次运行混进同一个 JSONL。"""
    candidate = base / run_id
    if not candidate.exists():
        return candidate
    for index in range(2, 100):
        candidate = base / f"{run_id}-{index}"
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"{base} 下 {run_id} 的重名目录过多，请手动清理")


class RunLogger:
    """一次实验运行 = 一个目录。

    `log()` 逐条追加，`finish()` 写 meta。也可用 with 语句自动 finish。
    """

    def __init__(
        self,
        *,
        mode: str,
        flags: dict[str, bool] | None = None,
        dataset_name: str = "unspecified",
        dataset_size: int | None = None,
        model: str | None = None,
        top_k: int = 5,
        name: str | None = None,
        runs_dir: Path | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.mode = mode
        self.flags = dict(flags or {})
        self.dataset_name = dataset_name
        self.dataset_size = dataset_size
        self.model = model or "unknown"
        self.top_k = top_k
        self.extra = dict(extra or {})

        started = datetime.now(UTC)
        run_id = name or f"{started.astimezone().strftime('%Y%m%d-%H%M%S')}-{mode}"

        base = runs_dir or settings.runtime.runs_dir
        base.mkdir(parents=True, exist_ok=True)
        self.run_dir = _unique_dir(base, run_id)
        self.run_dir.mkdir(parents=True, exist_ok=False)

        self.run_id = self.run_dir.name
        self.started_at = started
        self.samples_path = self.run_dir / "samples.jsonl"
        self.meta_path = self.run_dir / "meta.json"

        self.sample_count = 0
        self.total_tokens = 0
        self.total_cost_usd = 0.0
        self.total_errors = 0
        self._finished = False

    # ------------------------------------------------------------------ #
    def log(self, state: AgentState) -> None:
        """追加一条样本。写失败直接抛，不静默丢数据。"""
        record = json.loads(state.model_dump_json())
        record["_sample_index"] = self.sample_count

        with self.samples_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

        self.sample_count += 1
        self.total_tokens += state.usage.total_tokens
        self.total_cost_usd += state.usage.cost_usd
        self.total_errors += len(state.errors)

    # ------------------------------------------------------------------ #
    def finish(self, summary: dict[str, Any] | None = None) -> Path:
        """写 meta.json，返回 run 目录。可重复调用，只生效一次。"""
        if self._finished:
            return self.run_dir

        finished = datetime.now(UTC)
        meta: dict[str, Any] = {
            "run_id": self.run_id,
            # ---- 五项上下文：缺一项这个实验就无法复现 ----
            "commit": _git_commit(),
            "model": self.model,
            "generation": {"top_k": self.top_k, "mode": self.mode, "flags": self.flags},
            "dataset": {"name": self.dataset_name, "size": self.dataset_size},
            "started_at": self.started_at.isoformat(),
            "finished_at": finished.isoformat(),
            # ---- 汇总 ----
            "sample_count": self.sample_count,
            "totals": {
                "tokens": self.total_tokens,
                "cost_usd": round(self.total_cost_usd, 8),
                "errors": self.total_errors,
            },
            "python": platform.python_version(),
            "summary": dict(summary or {}),
            "extra": self.extra,
        }
        self.meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self._finished = True
        return self.run_dir

    # ------------------------------------------------------------------ #
    def __enter__(self) -> RunLogger:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        # 即使中途抛异常也写 meta，留下"这次运行失败在哪一步"的证据
        summary = {"aborted": True, "error": f"{exc_type.__name__}: {exc}"} if exc else None
        self.finish(summary)
