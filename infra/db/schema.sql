-- InvoiceOps v1 PostgreSQL schema. Audit rows are append-only.
CREATE TABLE IF NOT EXISTS tickets (
  id UUID PRIMARY KEY,
  request_id VARCHAR(128) NOT NULL UNIQUE,
  idempotency_key VARCHAR(255) UNIQUE,
  source VARCHAR(32) NOT NULL,
  text TEXT NOT NULL,
  taxonomy_version VARCHAR(64) NOT NULL,
  language VARCHAR(16) NOT NULL,
  status VARCHAR(32) NOT NULL,
  risk VARCHAR(16) NOT NULL DEFAULT 'medium',
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  trace_id VARCHAR(64) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS predictions (
  id UUID PRIMARY KEY,
  ticket_id UUID NOT NULL REFERENCES tickets(id),
  decision VARCHAR(32) NOT NULL,
  labels JSONB NOT NULL,
  reason_codes JSONB NOT NULL,
  route_primary VARCHAR(64) NOT NULL,
  route_collaborators JSONB NOT NULL,
  route_version VARCHAR(64) NOT NULL,
  model_version VARCHAR(128) NOT NULL,
  threshold_version VARCHAR(64) NOT NULL,
  taxonomy_version VARCHAR(64) NOT NULL,
  language VARCHAR(16) NOT NULL,
  inference_ms DOUBLE PRECISION NOT NULL,
  trace_id VARCHAR(64) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS prediction_labels (
  id BIGSERIAL PRIMARY KEY,
  prediction_id UUID NOT NULL REFERENCES predictions(id),
  label_code VARCHAR(64) NOT NULL,
  score DOUBLE PRECISION NOT NULL CHECK (score >= 0 AND score <= 1),
  UNIQUE (prediction_id, label_code)
);
CREATE TABLE IF NOT EXISTS reviews (
  id UUID PRIMARY KEY,
  ticket_id UUID NOT NULL REFERENCES tickets(id),
  revision INTEGER NOT NULL CHECK (revision >= 1),
  labels JSONB NOT NULL,
  primary_queue VARCHAR(64) NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  reviewer_id VARCHAR(128) NOT NULL,
  training_candidate BOOLEAN NOT NULL DEFAULT FALSE,
  trace_id VARCHAR(64) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (ticket_id, revision)
);
CREATE TABLE IF NOT EXISTS model_versions (
  version VARCHAR(128) PRIMARY KEY,
  model_type VARCHAR(64) NOT NULL,
  threshold_version VARCHAR(64) NOT NULL,
  status VARCHAR(32) NOT NULL,
  metrics JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE TABLE IF NOT EXISTS taxonomy_versions (
  version VARCHAR(64) PRIMARY KEY,
  snapshot JSONB NOT NULL,
  checksum VARCHAR(128) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS audit_events (
  event_id UUID PRIMARY KEY,
  event_type VARCHAR(64) NOT NULL,
  event_version VARCHAR(16) NOT NULL DEFAULT '1',
  request_id VARCHAR(128) NOT NULL,
  trace_id VARCHAR(64) NOT NULL,
  actor VARCHAR(128) NOT NULL,
  subject VARCHAR(128) NOT NULL,
  payload JSONB NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS outbox_events (
  event_id UUID PRIMARY KEY,
  event_type VARCHAR(64) NOT NULL,
  aggregate_id VARCHAR(128) NOT NULL,
  payload JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  published_at TIMESTAMPTZ NULL
);
CREATE TABLE IF NOT EXISTS llm_suggestions (
  id UUID PRIMARY KEY,
  ticket_id UUID NOT NULL REFERENCES tickets(id),
  labels JSONB NOT NULL,
  rationale TEXT NOT NULL,
  confidence DOUBLE PRECISION NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
  provider VARCHAR(64) NOT NULL,
  model VARCHAR(128) NOT NULL,
  prompt_version VARCHAR(64) NOT NULL,
  cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0,
  error_code VARCHAR(64),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS batch_jobs (
  batch_id UUID PRIMARY KEY,
  status VARCHAR(32) NOT NULL,
  total_rows INTEGER NOT NULL DEFAULT 0,
  processed_rows INTEGER NOT NULL DEFAULT 0,
  success_rows INTEGER NOT NULL DEFAULT 0,
  failed_rows INTEGER NOT NULL DEFAULT 0,
  row_errors JSONB NOT NULL DEFAULT '[]'::jsonb,
  payload BYTEA NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS idempotency_records (
  key VARCHAR(255) PRIMARY KEY,
  fingerprint VARCHAR(64) NOT NULL,
  response JSONB NOT NULL,
  ticket_id UUID NULL REFERENCES tickets(id) ON DELETE SET NULL
);
