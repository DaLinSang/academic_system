"""环境自检：确认 LLM 与 embedding 都能调通。

这是分工文档阶段 0 的验收项之一（"所有人先确认可以调用 LLM / Ollama embedding"）。
B 和 C 在开始写代码前先跑一遍这个脚本。

运行：
    uv run python scripts/check_env.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.errors import LLMError  # noqa: E402
from app.llm.client import get_llm  # noqa: E402

OK = "  [OK]  "
BAD = "  [FAIL]"
WARN = "  [WARN]"


def check_llm() -> bool:
    print("\n[1] LLM 调用")
    print(f"      provider = {settings.llm.provider}")
    try:
        llm = get_llm()
        resp = llm.generate(
            [{"role": "user", "content": "只回复两个字：收到"}],
            max_tokens=16,
        )
        if type(llm).__name__ == "MockLLM":
            print(f"{WARN} 未配置 API key，当前是 MockLLM（不影响阶段 0 开发）")
            print(f"      返回：{resp.text[:40]}")
            return True
        print(f"{OK} model={resp.model}  回复={resp.text[:40]!r}")
        print(
            f"      tokens={resp.usage.total_tokens}  "
            f"cost=${resp.usage.cost_usd:.6f}  "
            f"latency={resp.usage.latency_ms}ms"
        )
        return True
    except LLMError as exc:
        print(f"{BAD} {exc}")
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"{BAD} {type(exc).__name__}: {exc}")
        return False


def check_ollama() -> bool:
    print("\n[2] Ollama Embedding")
    base = settings.embedding.ollama_base_url.rstrip("/")
    model = settings.embedding.model
    print(f"      base_url = {base}")
    print(f"      model    = {model}  (dim={settings.embedding.dim})")
    try:
        with httpx.Client(timeout=10) as c:
            tags = c.get(f"{base}/api/tags")
            tags.raise_for_status()
            names = [m["name"] for m in tags.json().get("models", [])]
            if not any(model in n for n in names):
                print(f"{BAD} 模型未下载。已安装：{names}")
                print(f"         修复：ollama pull {model}")
                return False

            r = c.post(f"{base}/api/embeddings", json={"model": model, "prompt": "测试"})
            r.raise_for_status()
            vec = r.json().get("embedding", [])
            if len(vec) != settings.embedding.dim:
                print(f"{WARN} 实际维度 {len(vec)} 与 EMBEDDING_DIM={settings.embedding.dim} 不一致")
                print(f"         请把 .env 的 EMBEDDING_DIM 改成 {len(vec)}")
                return True
            print(f"{OK} 向量维度 {len(vec)}，前 3 维 {[round(x, 4) for x in vec[:3]]}")
            return True
    except Exception as exc:  # noqa: BLE001
        print(f"{BAD} {type(exc).__name__}: {exc}")
        print("         修复：确认 ollama 已启动（ollama serve）")
        return False


def check_db() -> bool:
    print("\n[3] PostgreSQL + pgvector")
    print(f"      dsn = {settings.db.host}:{settings.db.port}/{settings.db.dbname}")
    try:
        import psycopg

        with psycopg.connect(settings.db.dsn, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
                row = cur.fetchone()
                if row is None:
                    print(f"{BAD} 未安装 vector 扩展")
                    return False
                print(f"{OK} 已连接，pgvector {row[0]}")
                return True
    except Exception as exc:  # noqa: BLE001
        print(f"{WARN} 未连接：{type(exc).__name__}")
        print("         阶段 0 用 Mock 不需要数据库；B 开发检索前执行：")
        print("         docker compose up -d")
        return True


def main() -> int:
    print("=" * 60)
    print("  COMP7608B Group 36 · 环境自检")
    print("=" * 60)
    results = [check_llm(), check_ollama(), check_db()]
    print("\n" + "=" * 60)
    if all(results):
        print("  全部通过，可以开始开发")
        return 0
    print("  有项目未通过，请按上面的提示修复")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
