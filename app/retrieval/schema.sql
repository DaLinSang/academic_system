CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS document_chunks (
    chunk_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    file_name TEXT NOT NULL,
    page_number INTEGER NOT NULL CHECK (page_number >= 1),
    text TEXT NOT NULL,
    char_start INTEGER,
    char_end INTEGER,
    embedding vector(768) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS document_chunks_document_id_idx
    ON document_chunks (document_id);

CREATE TABLE IF NOT EXISTS document_pages (
    document_id TEXT NOT NULL,
    file_name TEXT NOT NULL,
    page_number INTEGER NOT NULL CHECK (page_number >= 1),
    text TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (document_id, page_number)
);

CREATE INDEX IF NOT EXISTS document_pages_file_name_idx
    ON document_pages (file_name);
