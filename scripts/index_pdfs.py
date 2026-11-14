"""把 PDF 索引进 pgvector，供混合检索使用。

这是集成层的小工具（A 维护），底层解析与切分都是成员 B 的 `app/ingestion`
与 `app/retrieval`，本脚本只负责"批量跑一遍 + 报结果"。

运行：
    uv run python scripts/index_pdfs.py                  # 默认扫 data/raw
    uv run python scripts/index_pdfs.py --dir ./docs     # 指定目录
    uv run python scripts/index_pdfs.py --limit 3        # 只索引前 3 个
    uv run python scripts/index_pdfs.py --list           # 只看有哪些 PDF

前置：docker compose up -d（pgvector 在 5434）
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.core.errors import IngestionError, RetrievalError  # noqa: E402


def collect_pdfs(directory: Path) -> list[Path]:
    return sorted(p for p in directory.rglob("*.pdf") if p.is_file())


def main() -> int:
    parser = argparse.ArgumentParser(description="批量索引 PDF 到 pgvector")
    parser.add_argument(
        "--dir",
        type=Path,
        default=settings.runtime.data_raw_dir,
        help="PDF 所在目录（默认 data/raw，递归扫描）",
    )
    parser.add_argument("--limit", type=int, default=0, help="最多索引几个文件，0 表示不限")
    parser.add_argument("--list", action="store_true", help="只列出待索引文件，不执行")
    args = parser.parse_args()

    directory: Path = args.dir
    if not directory.exists():
        print(f"目录不存在：{directory}")
        print(f"把 PDF 放到 {settings.runtime.data_raw_dir} 后重试")
        return 1

    pdfs = collect_pdfs(directory)
    if not pdfs:
        print(f"{directory} 下没有 PDF")
        return 1

    if args.limit > 0:
        pdfs = pdfs[: args.limit]

    print(f"待索引 {len(pdfs)} 个文件，来自 {directory}")
    if args.list:
        for path in pdfs:
            print(f"  - {path.name} ({path.stat().st_size / 1024:.0f} KB)")
        return 0

    # 延迟导入：--list 模式不需要连数据库
    from app.retrieval import HybridRetriever

    try:
        retriever = HybridRetriever()
    except Exception as exc:  # noqa: BLE001
        print(f"初始化检索器失败：{type(exc).__name__}: {exc}")
        return 1

    total_chunks = 0
    failed: list[tuple[str, str]] = []

    for index, path in enumerate(pdfs, start=1):
        started = time.perf_counter()
        try:
            count = retriever.index_document(str(path))
        except (IngestionError, RetrievalError) as exc:
            failed.append((path.name, f"{type(exc).__name__}: {exc}"))
            print(f"[{index}/{len(pdfs)}] {path.name}  失败：{type(exc).__name__}")
            continue
        except Exception as exc:  # noqa: BLE001
            failed.append((path.name, f"{type(exc).__name__}: {exc}"))
            print(f"[{index}/{len(pdfs)}] {path.name}  失败：{type(exc).__name__}")
            continue

        elapsed = (time.perf_counter() - started) * 1000
        total_chunks += count
        print(f"[{index}/{len(pdfs)}] {path.name}  {count} chunks  {elapsed:.0f}ms")

    print()
    print(f"完成：{len(pdfs) - len(failed)}/{len(pdfs)} 个文件，共 {total_chunks} 个 chunk")

    if failed:
        print("\n失败清单：")
        for name, reason in failed:
            print(f"  - {name}: {reason}")
        return 1

    print("\n现在可以运行：")
    print('  uv run python -m app.demo --real -q "讲义里 Self-RAG 是怎么决定要不要检索的？"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
