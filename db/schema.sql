CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS documents (
    document_id TEXT PRIMARY KEY,
    ticker TEXT NOT NULL,
    document_type TEXT NOT NULL CHECK (document_type IN ('filing', 'earnings_transcript')),
    title TEXT NOT NULL,
    document_date DATE NOT NULL,
    period TEXT NOT NULL,
    is_synthetic BOOLEAN NOT NULL DEFAULT TRUE,
    source_file TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    section_or_page TEXT NOT NULL,
    text TEXT NOT NULL,
    embedding vector(768) NOT NULL
);

CREATE INDEX IF NOT EXISTS documents_lookup ON documents (ticker, document_type, document_date DESC);
CREATE INDEX IF NOT EXISTS chunks_document ON chunks (document_id);
