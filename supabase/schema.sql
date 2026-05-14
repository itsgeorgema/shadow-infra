-- Shadow-Infra Database Schema
-- Run this in your Supabase SQL editor after creating a project.

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Tracks shadow deployments spawned for each PR
CREATE TABLE shadow_deployments (
    id            uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    pr_number     int NOT NULL,
    pr_title      text NOT NULL,
    repo          text NOT NULL,
    branch        text NOT NULL,
    shadow_url    text NOT NULL,
    status        text NOT NULL DEFAULT 'active',
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_shadow_deployments_pr_number ON shadow_deployments(pr_number);
CREATE INDEX idx_shadow_deployments_status ON shadow_deployments(status);

-- Stores matched prod/shadow response pairs captured by the traffic splitter
CREATE TABLE response_pairs (
    id              uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    deployment_id   uuid NOT NULL REFERENCES shadow_deployments(id) ON DELETE CASCADE,
    request_path    text NOT NULL,
    request_method  text NOT NULL,
    prod_status     int NOT NULL,
    prod_headers    jsonb NOT NULL DEFAULT '{}',
    prod_body       text NOT NULL DEFAULT '',
    shadow_status   int NOT NULL,
    shadow_headers  jsonb NOT NULL DEFAULT '{}',
    shadow_body       text NOT NULL DEFAULT '',
    prod_latency_ms   integer,
    shadow_latency_ms integer,
    captured_at       timestamptz NOT NULL DEFAULT now()
);

-- Migration for existing databases:
-- ALTER TABLE response_pairs ADD COLUMN IF NOT EXISTS prod_latency_ms integer;
-- ALTER TABLE response_pairs ADD COLUMN IF NOT EXISTS shadow_latency_ms integer;

CREATE INDEX idx_response_pairs_deployment_id ON response_pairs(deployment_id);
CREATE INDEX idx_response_pairs_captured_at ON response_pairs(captured_at DESC);

-- Stores LLM verdicts for each response pair
CREATE TABLE verdicts (
    id           uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    pair_id      uuid NOT NULL REFERENCES response_pairs(id) ON DELETE CASCADE,
    verdict      text NOT NULL CHECK (verdict IN ('Safe', 'Warning', 'Critical')),
    reasoning    text NOT NULL DEFAULT '',
    diff_summary text NOT NULL DEFAULT '',
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_verdicts_pair_id ON verdicts(pair_id);
CREATE INDEX idx_verdicts_verdict ON verdicts(verdict);

-- Auto-update updated_at on shadow_deployments
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_shadow_deployments_updated_at
    BEFORE UPDATE ON shadow_deployments
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
