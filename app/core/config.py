"""统一配置入口。

规则：**任何模块都不许直接读 os.environ**，一律从这里取。
这样换 key、换模型、换数据库只改一处。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 只在进程启动时加载一次；已存在的真实环境变量优先
load_dotenv(PROJECT_ROOT / ".env", override=False)


def _get(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _get_int(key: str, default: int) -> int:
    raw = _get(key)
    return int(raw) if raw else default


@dataclass(frozen=True)
class LLMSettings:
    provider: str = field(default_factory=lambda: _get("LLM_PROVIDER", "deepseek"))

    # DeepSeek
    deepseek_api_key: str = field(default_factory=lambda: _get("DEEPSEEK_API_KEY"))
    deepseek_base_url: str = field(
        default_factory=lambda: _get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    )
    deepseek_model: str = field(default_factory=lambda: _get("DEEPSEEK_MODEL", "deepseek-chat"))

    # HKU ITS
    hku_api_key: str = field(default_factory=lambda: _get("HKU_API_KEY"))
    hku_base_url: str = field(default_factory=lambda: _get("HKU_BASE_URL"))
    hku_model: str = field(default_factory=lambda: _get("HKU_MODEL"))

    timeout: int = field(default_factory=lambda: _get_int("LLM_TIMEOUT", 60))
    max_retries: int = field(default_factory=lambda: _get_int("LLM_MAX_RETRIES", 2))

    def resolve(self, provider: str | None = None) -> tuple[str, str, str]:
        """返回 (api_key, base_url, model)。provider 为空时用 .env 的默认值。

        切换模型只需要改 .env，不需要改代码 —— 这是 A 对 B/C 的承诺。
        """
        name = (provider or self.provider).lower()
        if name == "hku":
            return self.hku_api_key, self.hku_base_url, self.hku_model
        if name == "deepseek":
            return self.deepseek_api_key, self.deepseek_base_url, self.deepseek_model
        raise ValueError(f"未知的 LLM_PROVIDER: {name}（支持 deepseek / hku）")


@dataclass(frozen=True)
class EmbeddingSettings:
    ollama_base_url: str = field(
        default_factory=lambda: _get("OLLAMA_BASE_URL", "http://localhost:11434")
    )
    model: str = field(default_factory=lambda: _get("EMBEDDING_MODEL", "nomic-embed-text"))
    dim: int = field(default_factory=lambda: _get_int("EMBEDDING_DIM", 768))


@dataclass(frozen=True)
class DatabaseSettings:
    host: str = field(default_factory=lambda: _get("POSTGRES_HOST", "localhost"))
    port: int = field(default_factory=lambda: _get_int("POSTGRES_PORT", 5434))
    dbname: str = field(default_factory=lambda: _get("POSTGRES_DB", "academic"))
    user: str = field(default_factory=lambda: _get("POSTGRES_USER", "academic"))
    password: str = field(default_factory=lambda: _get("POSTGRES_PASSWORD", "academic_dev_pw"))

    @property
    def dsn(self) -> str:
        return (
            f"postgresql://{self.user}:{self.password}"
            f"@{self.host}:{self.port}/{self.dbname}"
        )


@dataclass(frozen=True)
class SearchSettings:
    """联网搜索后端配置（Issue #89）。

    API key 为空时自动回退到 HTML 端点爬取（既有行为），向后兼容。
    """

    tavily_api_key: str = field(default_factory=lambda: _get("TAVILY_API_KEY"))
    tavily_base_url: str = field(
        default_factory=lambda: _get("TAVILY_BASE_URL", "https://api.tavily.com")
    )
    tavily_timeout: int = field(default_factory=lambda: _get_int("TAVILY_TIMEOUT", 40))
    tavily_depth: str = field(default_factory=lambda: _get("TAVILY_DEPTH", "basic"))


@dataclass(frozen=True)
class RuntimeSettings:
    log_dir: Path = field(default_factory=lambda: PROJECT_ROOT / _get("LOG_DIR", "logs"))
    runs_dir: Path = field(default_factory=lambda: PROJECT_ROOT / _get("RUNS_DIR", "runs"))
    default_session_id: str = field(
        default_factory=lambda: _get("DEFAULT_SESSION_ID", "dev-session")
    )
    default_user_id: str = field(default_factory=lambda: _get("DEFAULT_USER_ID", "dev-user"))
    data_raw_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "raw")


@dataclass(frozen=True)
class Settings:
    llm: LLMSettings = field(default_factory=LLMSettings)
    embedding: EmbeddingSettings = field(default_factory=EmbeddingSettings)
    db: DatabaseSettings = field(default_factory=DatabaseSettings)
    search: SearchSettings = field(default_factory=SearchSettings)
    runtime: RuntimeSettings = field(default_factory=RuntimeSettings)


settings = Settings()
