"""正式实验就绪检查（Issue #49）。

跑正式实验前执行，确认所有前置条件：
    uv run python scripts/check_experiment_ready.py

Issue #49 明确要求：契约、Web 搜索、多轮、规划闭环、重试全部冻结后才跑，
否则 50×4×3 的数据作废。本脚本检查：
  1. LLM key（Real 模式，非 Mock）
  2. PostgreSQL + pgvector 可达
  3. Ollama embedding 可用
  4. 数据集引用的 PDF 是否全部已索引（比只数文件数更准确）
  5. 标注集 50 条完整
  6. 功能冻结标记 .experiment-freeze 存在且与当前 HEAD 一致

只有全部 OK 才提示"可以跑正式实验"。冻结标记由人确认后创建：
    git rev-parse HEAD > .experiment-freeze
（.experiment-freeze 应加入 .gitignore，避免版本号污染源码）

不做任何写操作，只读检查。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.core.mocks import MockLLM  # noqa: E402
from app.evaluation.dataset import DEFAULT_DATASET, load_dataset  # noqa: E402

OK = "  [OK]  "
BAD = "  [FAIL]"
WARN = "  [WARN]"


def check_llm() -> bool:
    from app.llm.client import get_llm

    llm = get_llm()
    if isinstance(llm, MockLLM):
        print(f"{BAD} 未配置 LLM key（当前 MockLLM），正式实验必须用 Real 模型")
        return False
    print(f"{OK} LLM 就绪：provider={settings.llm.provider}")
    return True


def check_db() -> bool:
    try:
        import psycopg

        with psycopg.connect(settings.db.dsn, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
    except Exception as exc:  # noqa: BLE001 - 自检脚本只报告不抛
        print(f"{BAD} PostgreSQL 不可达：{type(exc).__name__}")
        return False
    print(f"{OK} PostgreSQL 可达：{settings.db.host}:{settings.db.port}")
    return True


def check_embedding() -> bool:
    import httpx

    try:
        resp = httpx.get(f"{settings.embedding.ollama_base_url}/api/tags", timeout=5)
        resp.raise_for_status()
        models = [m.get("name", "") for m in resp.json().get("models", [])]
        if not any(settings.embedding.model in m for m in models):
            print(f"{BAD} Ollama 未找到模型 {settings.embedding.model}")
            return False
    except Exception as exc:  # noqa: BLE001
        print(f"{BAD} Ollama 不可达：{type(exc).__name__}")
        return False
    print(f"{OK} Embedding 就绪：{settings.embedding.model}")
    return True


def check_indexed_pdfs() -> bool:
    """检查数据集引用的每个 PDF 是否都已索引（比只数文件数更准确）。"""
    try:
        import psycopg

        with psycopg.connect(settings.db.dsn, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT DISTINCT file_name FROM document_chunks")
                indexed = {Path(row[0]).name.casefold() for row in cur.fetchall() if row[0]}
    except Exception as exc:  # noqa: BLE001
        print(f"{BAD} 查索引失败：{type(exc).__name__}")
        return False
    if not indexed:
        print(f"{BAD} 数据库无已索引 PDF，请先运行 scripts/index_pdfs.py")
        return False

    referenced: set[str] = set()
    for sample in load_dataset():
        for source in sample.get("gold_sources", []) or []:
            name = source.get("file_name") if isinstance(source, dict) else str(source)
            if name:
                referenced.add(Path(name).name.casefold())
    missing = sorted(name for name in referenced if name not in indexed)
    if missing:
        print(f"{BAD} 数据集引用但未索引的 PDF：{missing}")
        print("       请先运行 scripts/index_pdfs.py，否则相关样本拿不到证据、RQ3 数据作废")
        return False
    print(f"{OK} 已索引 PDF：{len(indexed)} 份；数据集引用的 {len(referenced)} 份全部就绪")
    return True


def check_dataset() -> bool:
    if not DEFAULT_DATASET.exists():
        print(f"{BAD} 标注集不存在：{DEFAULT_DATASET}")
        return False
    try:
        rows = load_dataset()
    except (OSError, ValueError) as exc:
        print(f"{BAD} 标注集校验失败：{exc}")
        return False
    if not 50 <= len(rows) <= 80:
        print(f"{BAD} 标注集条数 {len(rows)} 不在 50–80 范围")
        return False
    print(f"{OK} 标注集就绪：{len(rows)} 条，已通过正式 loader 全量校验")
    return True


def check_freeze(*, required: bool = False) -> bool:
    root = Path(__file__).resolve().parents[1]
    marker = root / ".experiment-freeze"
    if not marker.exists():
        status = BAD if required else WARN
        print(f"{status} 无功能冻结标记 .experiment-freeze")
        print("       前置环境可以继续检查；正式运行前请创建冻结标记并加 --require-freeze")
        return not required
    frozen = marker.read_text(encoding="utf-8").strip()
    head = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()
    if frozen != head:
        print(f"{BAD} 冻结标记 {frozen[:8]} 与当前 HEAD {head[:8]} 不一致")
        print("       代码在冻结后又变了——请确认改动是否影响输出；若影响需重冻结并重跑")
        return False
    print(f"{OK} 功能冻结标记与 HEAD 一致：{head[:8]}")
    return True


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="检查正式实验环境与功能冻结状态")
    parser.add_argument(
        "--require-freeze",
        action="store_true",
        help="正式运行前启用：要求 .experiment-freeze 与当前 HEAD 一致",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    print("=" * 64)
    print("正式实验就绪检查（Issue #49）")
    print("=" * 64)
    checks = [
        ("LLM key（Real）", check_llm),
        ("PostgreSQL + pgvector", check_db),
        ("Ollama embedding", check_embedding),
        ("已索引 PDF", check_indexed_pdfs),
        ("标注集", check_dataset),
        ("功能冻结标记", lambda: check_freeze(required=args.require_freeze)),
    ]
    results: list[bool] = []
    for name, fn in checks:
        print(f"\n[{len(results) + 1}] {name}")
        try:
            results.append(bool(fn()))
        except Exception as exc:  # noqa: BLE001
            print(f"{BAD} {type(exc).__name__}: {exc}")
            results.append(False)

    print("\n" + "=" * 64)
    if all(results):
        if not args.require_freeze:
            print("前置环境就绪。冻结代码后，请执行最终检查：")
            print("  uv run python scripts/check_experiment_ready.py --require-freeze")
            return 0
        print("全部就绪。可以运行正式实验：")
        print("  uv run python -m app.evaluation.batch_runner --real --repeats 3")
        print("  （约 50×4×3=600 次，本机 2 条冒烟实测推算约 30 分钟）")
        return 0
    print(f"未就绪（{sum(results)}/{len(results)} 通过）。请先解决上面的 FAIL 项。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
