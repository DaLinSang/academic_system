-- 容器首次启动时自动执行（仅执行一次）。
-- 职责边界：本文件只负责**扩展**。建表、索引、BM25 配置由成员 B 在
-- app/retrieval/schema.sql 中维护，并通过 scripts/init_schema.py 应用。

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- 确认扩展已就绪
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
        RAISE EXCEPTION 'pgvector 扩展安装失败';
    END IF;
END
$$;
