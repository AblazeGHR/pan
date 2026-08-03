-- Pan Memory SQLite Schema

-- Key-value metadata
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- File tracking
CREATE TABLE IF NOT EXISTS files (
    path   TEXT PRIMARY KEY,
    source TEXT NOT NULL DEFAULT 'memory'
        CHECK (source IN ('memory', 'sessions')),
    hash   TEXT NOT NULL,
    mtime  REAL NOT NULL,
    size   INTEGER NOT NULL
);

-- Chunk storage
CREATE TABLE IF NOT EXISTS chunks (
    id         TEXT PRIMARY KEY,
    path       TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT 'memory'
        CHECK (source IN ('memory', 'sessions')),
    start_line INTEGER NOT NULL,
    end_line   INTEGER NOT NULL,
    hash       TEXT NOT NULL,
    model      TEXT NOT NULL,
    text       TEXT NOT NULL,
    embedding  TEXT NOT NULL,  -- JSON-serialized float array
    updated_at REAL NOT NULL
);

-- Full-text search index
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
    text,
    id         UNINDEXED,  -- 16-hex chunk id must not be tokenized (#23)
    path       UNINDEXED,
    source     UNINDEXED,
    model      UNINDEXED,
    start_line UNINDEXED,
    end_line   UNINDEXED,
    tokenize='unicode61'
);

-- Embedding dedup cache
CREATE TABLE IF NOT EXISTS embedding_cache (
    provider     TEXT NOT NULL,
    model        TEXT NOT NULL,
    provider_key TEXT NOT NULL,
    hash         TEXT NOT NULL,
    embedding    TEXT NOT NULL,  -- JSON-serialized float array
    dims         INTEGER,
    updated_at   REAL NOT NULL,
    PRIMARY KEY (provider, model, provider_key, hash)
);
