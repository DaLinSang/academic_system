"""RunLogger 的测试。

重点守三件事：
  1. 落盘结构稳定（评测脚本依赖它，不能随手改格式）；
  2. 五项上下文（commit / 模型 / 参数 / 数据 / 时间）一项都不能缺；
  3. 写失败必须抛，不能静默丢数据——残缺的实验结果比崩溃更危险。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agent.run_logger import RunLogger
from app.core.types import AgentState, Evidence, Usage


def make_state(question: str = "讲义里 RAG 是什么？", *, tokens: int = 100,
               errors: list[str] | None = None) -> AgentState:
    state = AgentState(question=question)
    state.final_answer = "RAG 结合两类记忆 [1]。"
    state.evidence = [
        Evidence(
            chunk_id="c1", document_id="d1", file_name="Lec04.pdf",
            page_number=9, text="RAG 结合参数化与非参数化记忆", score=1.0,
        )
    ]
    state.usage = Usage(total_tokens=tokens, cost_usd=0.0001, llm_calls=1)
    state.errors = list(errors or [])
    return state


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# --------------------------------------------------------------------------- #
# 目录与文件结构
# --------------------------------------------------------------------------- #
def test_creates_run_directory(tmp_path):
    run = RunLogger(mode="B4", runs_dir=tmp_path)
    assert run.run_dir.is_dir()
    assert run.run_dir.parent == tmp_path
    assert "B4" in run.run_id


def test_run_id_is_unique_on_collision(tmp_path):
    first = RunLogger(mode="B4", name="same-name", runs_dir=tmp_path)
    second = RunLogger(mode="B4", name="same-name", runs_dir=tmp_path)

    assert first.run_dir != second.run_dir
    assert second.run_dir.name.endswith("-2")


def test_samples_file_appends_one_line_per_state(tmp_path):
    run = RunLogger(mode="B4", runs_dir=tmp_path)
    run.log(make_state("问题一"))
    run.log(make_state("问题二"))

    rows = read_jsonl(run.samples_path)
    assert len(rows) == 2
    assert [r["question"] for r in rows] == ["问题一", "问题二"]
    assert [r["_sample_index"] for r in rows] == [0, 1]


def test_sample_record_keeps_evaluation_fields(tmp_path):
    """评测要用的字段必须原样保留，不能被精简掉。"""
    run = RunLogger(mode="B4", runs_dir=tmp_path)
    state = make_state()
    run.log(state)

    row = read_jsonl(run.samples_path)[0]
    for key in ("route", "evidence", "claims", "citations", "usage", "trace", "errors"):
        assert key in row, f"缺少评测必需字段 {key}"
    assert row["evidence"][0]["page_number"] == 9


# --------------------------------------------------------------------------- #
# meta.json：五项上下文
# --------------------------------------------------------------------------- #
def test_meta_contains_five_reproducibility_fields(tmp_path):
    run = RunLogger(
        mode="B4",
        flags={"enable_routing": True, "enable_memory": True},
        dataset_name="annotated_v1",
        dataset_size=50,
        model="deepseek-chat",
        top_k=5,
        runs_dir=tmp_path,
    )
    run.log(make_state())
    meta = json.loads(run.finish().joinpath("meta.json").read_text(encoding="utf-8"))

    # 1 提交
    assert "commit" in meta and meta["commit"]
    # 2 模型
    assert meta["model"] == "deepseek-chat"
    # 3 生成参数
    assert meta["generation"]["top_k"] == 5
    assert meta["generation"]["flags"]["enable_routing"] is True
    # 4 数据版本
    assert meta["dataset"] == {"name": "annotated_v1", "size": 50}
    # 5 运行时间
    assert meta["started_at"] and meta["finished_at"]


def test_meta_aggregates_totals(tmp_path):
    run = RunLogger(mode="B4", runs_dir=tmp_path)
    run.log(make_state(tokens=100))
    run.log(make_state(tokens=250, errors=["retrieval: boom"]))
    meta = json.loads(run.finish().joinpath("meta.json").read_text(encoding="utf-8"))

    assert meta["sample_count"] == 2
    assert meta["totals"]["tokens"] == 350
    assert meta["totals"]["errors"] == 1


def test_meta_records_python_version_and_extra(tmp_path):
    run = RunLogger(mode="B1", runs_dir=tmp_path, extra={"note": "smoke test"})
    meta = json.loads(run.finish().joinpath("meta.json").read_text(encoding="utf-8"))

    assert meta["python"].startswith("3.")
    assert meta["extra"]["note"] == "smoke test"


# --------------------------------------------------------------------------- #
# 生命周期
# --------------------------------------------------------------------------- #
def test_finish_is_idempotent(tmp_path):
    run = RunLogger(mode="B4", runs_dir=tmp_path)
    run.log(make_state())
    first = run.finish()
    second = run.finish()

    assert first == second
    meta = json.loads((first / "meta.json").read_text(encoding="utf-8"))
    assert meta["sample_count"] == 1


def test_context_manager_writes_meta(tmp_path):
    with RunLogger(mode="B2", runs_dir=tmp_path) as run:
        run.log(make_state())
        run_dir = run.run_dir

    assert (run_dir / "meta.json").is_file()
    assert (run_dir / "samples.jsonl").is_file()


def test_context_manager_records_abort(tmp_path):
    """中途崩溃也要留下证据，否则不知道这批数据跑到哪断了。"""
    run = RunLogger(mode="B4", runs_dir=tmp_path)
    with pytest.raises(RuntimeError):
        with run:
            run.log(make_state())
            raise RuntimeError("模型额度耗尽")

    meta = json.loads((run.run_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["summary"]["aborted"] is True
    assert "RuntimeError" in meta["summary"]["error"]
    assert meta["sample_count"] == 1, "已写入的样本数要如实记录"


def test_empty_run_still_writes_meta(tmp_path):
    run = RunLogger(mode="B1", runs_dir=tmp_path)
    run.finish()
    meta = json.loads((run.run_dir / "meta.json").read_text(encoding="utf-8"))

    assert meta["sample_count"] == 0
    assert not run.samples_path.exists(), "没有样本就不该产生空的 jsonl"


# --------------------------------------------------------------------------- #
# 与真实 Orchestrator 的衔接
# --------------------------------------------------------------------------- #
def test_logs_real_orchestrator_output(tmp_path):
    from app.agent.factory import build_mock_orchestrator
    from app.agent.orchestrator import MODE_PRESETS

    orch = build_mock_orchestrator(top_k=3)
    with RunLogger(mode="B4", flags=MODE_PRESETS["B4"], runs_dir=tmp_path) as run:
        for question in ("讲义里 RAG 是什么？", "比较 RAG 和 ReAct 的差异"):
            run.log(orch.run(question, **MODE_PRESETS["B4"]))

    rows = read_jsonl(run.samples_path)
    assert len(rows) == 2
    assert all(row["final_answer"] for row in rows)
    assert all(row["route"] is not None for row in rows)
