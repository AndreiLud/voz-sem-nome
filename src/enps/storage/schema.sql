-- SQLite schema. One writer (the queue worker or the ingest endpoint), many readers.
-- Raw comments are never stored: only the anonymized text and counts of what was removed.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS responses (
    id                 INTEGER PRIMARY KEY,
    public_id          TEXT NOT NULL UNIQUE,          -- opaque random id shown in the dashboard and alerts
    tracking_hash      TEXT UNIQUE,                   -- sha256 of the tracking code given to the submitter
    dataset            TEXT NOT NULL CHECK (dataset IN ('synthetic', 'public')),
    channel            TEXT NOT NULL CHECK (channel IN ('synthetic', 'site', 'google_forms')),
    source_ref         TEXT,                          -- synthetic id (S0001...) for evaluation; NULL for real submissions
    submitted_at       TEXT NOT NULL,                 -- ISO 8601 UTC
    score              INTEGER NOT NULL CHECK (score BETWEEN 0 AND 10),
    area               TEXT NOT NULL,
    tenure             TEXT NOT NULL,
    lang               TEXT NOT NULL DEFAULT 'pt',
    comment_anon       TEXT NOT NULL DEFAULT '',      -- anonymized comment, '' when there was none
    comment_chars      INTEGER NOT NULL DEFAULT 0,    -- length of the original comment
    pii_counts         TEXT NOT NULL DEFAULT '{}',    -- JSON {"PESSOA": 2, "CPF": 1}
    anonymizer_version TEXT NOT NULL,
    dedupe_key         TEXT,                          -- sha256 of the normalized anonymized comment
    status             TEXT NOT NULL CHECK (status IN ('queued', 'processing', 'done', 'review', 'skipped', 'failed')),
    priority           INTEGER NOT NULL DEFAULT 10,   -- lower runs first
    attempts           INTEGER NOT NULL DEFAULT 0,
    queued_at          TEXT,
    started_at         TEXT,
    finished_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_responses_queue ON responses (status, priority, id);
CREATE INDEX IF NOT EXISTS idx_responses_dataset ON responses (dataset, submitted_at);
CREATE INDEX IF NOT EXISTS idx_responses_dedupe ON responses (dataset, dedupe_key, submitted_at);

CREATE TABLE IF NOT EXISTS classifications (
    response_id        INTEGER PRIMARY KEY REFERENCES responses (id) ON DELETE CASCADE,
    themes             TEXT NOT NULL,                 -- JSON list of theme codes
    sentiment          TEXT,                          -- pos | neu | neg, NULL when skipped
    critical           INTEGER NOT NULL DEFAULT 0,
    critical_types     TEXT NOT NULL DEFAULT '[]',    -- JSON list of critical type codes
    critical_sources   TEXT NOT NULL DEFAULT '[]',    -- JSON list: llm, lexicon, verifier
    evidence           TEXT,                          -- short excerpt of the anonymized text
    model              TEXT,
    model_digest       TEXT,
    quantization       TEXT,
    runtime_version    TEXT,                          -- Ollama version
    prompt_version     TEXT,
    verifier_version   TEXT,
    stages             TEXT NOT NULL DEFAULT '[]',    -- JSON list with per-stage timings, tokens, retries
    total_ms           INTEGER,
    output_tokens      INTEGER,
    prompt_tokens      INTEGER,
    created_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    id                 INTEGER PRIMARY KEY,
    response_public_id TEXT NOT NULL,                 -- kept even after the response is purged
    dataset            TEXT NOT NULL,
    critical_types     TEXT NOT NULL,                 -- JSON list
    submitted_at       TEXT NOT NULL,
    detected_at        TEXT NOT NULL,
    mode               TEXT NOT NULL CHECK (mode IN ('immediate', 'digest')),
    status             TEXT NOT NULL CHECK (status IN ('pending', 'sent', 'failed')),
    sent_at            TEXT,
    delivery_attempts  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts (status, mode, id);

-- Counters without any text, kept after public submissions are purged.
CREATE TABLE IF NOT EXISTS daily_stats (
    day                  TEXT NOT NULL,               -- YYYY-MM-DD in America/Sao_Paulo
    dataset              TEXT NOT NULL,
    channel              TEXT NOT NULL,
    submissions          INTEGER NOT NULL DEFAULT 0,
    distinct_submitters  INTEGER NOT NULL DEFAULT 0,  -- from a daily-salted IP hash kept only in memory
    rejected_rate_limit  INTEGER NOT NULL DEFAULT 0,
    rejected_duplicate   INTEGER NOT NULL DEFAULT 0,
    rejected_queue_full  INTEGER NOT NULL DEFAULT 0,
    classified           INTEGER NOT NULL DEFAULT 0,
    critical             INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, dataset, channel)
);

-- One row per finished job: time from submission to result, for p50/p95 after purges.
CREATE TABLE IF NOT EXISTS latency_samples (
    id                 INTEGER PRIMARY KEY,
    day                TEXT NOT NULL,
    dataset            TEXT NOT NULL,
    channel            TEXT NOT NULL,
    queue_ms           INTEGER NOT NULL,              -- submitted to started
    processing_ms      INTEGER NOT NULL,              -- started to finished
    total_ms           INTEGER NOT NULL               -- submitted to result visible
);

-- Queue length over time for the production chart.
CREATE TABLE IF NOT EXISTS queue_samples (
    ts                 TEXT PRIMARY KEY,
    pending            INTEGER NOT NULL,
    processing         INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key                TEXT PRIMARY KEY,
    value              TEXT NOT NULL
);
