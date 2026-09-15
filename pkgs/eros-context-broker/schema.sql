BEGIN;

CREATE TABLE IF NOT EXISTS sources (
    id text PRIMARY KEY,
    uri text NOT NULL,
    revision text NOT NULL,
    digest text NOT NULL,
    observed_at timestamptz NOT NULL DEFAULT now(),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (uri, revision, digest)
);

CREATE TABLE IF NOT EXISTS entities (
    id text PRIMARY KEY,
    kind text NOT NULL,
    trust_domain text NOT NULL CHECK (trust_domain IN ('shared', 'personal', 'work')),
    name text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS assertions (
    id text PRIMARY KEY,
    kind text NOT NULL CHECK (kind IN ('claim', 'decision', 'procedure', 'memory', 'observation')),
    trust_domain text NOT NULL CHECK (trust_domain IN ('shared', 'personal', 'work')),
    title text NOT NULL,
    body text NOT NULL,
    subject_id text REFERENCES entities(id) ON DELETE SET NULL,
    predicate text,
    object_id text REFERENCES entities(id) ON DELETE SET NULL,
    source_id text NOT NULL REFERENCES sources(id) ON DELETE RESTRICT,
    authority text NOT NULL,
    confidence double precision NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    verification_method text NOT NULL CHECK (
        verification_method IN ('source-backed', 'user-confirmed', 'test-passed', 'provider-readback')
    ),
    verified_by text NOT NULL,
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'superseded', 'retracted')),
    valid_from timestamptz,
    valid_until timestamptz,
    supersedes text REFERENCES assertions(id) ON DELETE SET NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    search_document tsvector GENERATED ALWAYS AS (
        to_tsvector('english', coalesce(title, '') || ' ' || coalesce(body, '') || ' ' || coalesce(predicate, ''))
    ) STORED,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS assertions_search_idx ON assertions USING gin(search_document);
CREATE INDEX IF NOT EXISTS assertions_scope_idx ON assertions(trust_domain, kind, status);

CREATE TABLE IF NOT EXISTS relations (
    id text PRIMARY KEY,
    trust_domain text NOT NULL CHECK (trust_domain IN ('shared', 'personal', 'work')),
    subject_id text NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    predicate text NOT NULL,
    object_id text NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    source_id text NOT NULL REFERENCES sources(id) ON DELETE RESTRICT,
    confidence double precision NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    valid_from timestamptz,
    valid_until timestamptz,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (trust_domain, subject_id, predicate, object_id, source_id)
);

CREATE INDEX IF NOT EXISTS relations_subject_idx ON relations(trust_domain, subject_id);
CREATE INDEX IF NOT EXISTS relations_object_idx ON relations(trust_domain, object_id);

CREATE TABLE IF NOT EXISTS capabilities (
    id text PRIMARY KEY,
    kind text NOT NULL CHECK (kind IN ('skill', 'plugin', 'mcp_server', 'mcp_tool', 'model_route')),
    trust_domain text NOT NULL CHECK (trust_domain IN ('shared', 'personal', 'work')),
    name text NOT NULL,
    version text,
    description text NOT NULL DEFAULT '',
    source_uri text,
    source_digest text,
    side_effect_class text NOT NULL DEFAULT 'read' CHECK (side_effect_class IN ('read', 'write', 'destructive', 'unknown')),
    cost_class text NOT NULL DEFAULT 'local' CHECK (cost_class IN ('local', 'free', 'metered', 'unknown')),
    required_capabilities jsonb NOT NULL DEFAULT '[]'::jsonb,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    search_document tsvector GENERATED ALWAYS AS (
        to_tsvector('english', coalesce(name, '') || ' ' || coalesce(description, ''))
    ) STORED,
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (kind, trust_domain, name, version)
);

CREATE INDEX IF NOT EXISTS capabilities_search_idx ON capabilities USING gin(search_document);
CREATE INDEX IF NOT EXISTS capabilities_scope_idx ON capabilities(trust_domain, kind);

CREATE TABLE IF NOT EXISTS verified_results (
    id text PRIMARY KEY,
    trust_domain text NOT NULL CHECK (trust_domain IN ('shared', 'personal', 'work')),
    query text NOT NULL,
    answer text NOT NULL,
    query_hash text NOT NULL,
    source_id text NOT NULL REFERENCES sources(id) ON DELETE RESTRICT,
    verification_method text NOT NULL CHECK (
        verification_method IN ('source-backed', 'user-confirmed', 'test-passed', 'provider-readback')
    ),
    verified_by text NOT NULL,
    model text,
    route text,
    corpus_version text NOT NULL,
    expires_at timestamptz,
    vector_point_id text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (trust_domain, query_hash, corpus_version)
);

CREATE INDEX IF NOT EXISTS verified_results_scope_idx
    ON verified_results(trust_domain, corpus_version, expires_at);

CREATE TABLE IF NOT EXISTS outcomes (
    id text PRIMARY KEY,
    request_id text,
    trust_domain text NOT NULL CHECK (trust_domain IN ('shared', 'personal', 'work')),
    consumer text NOT NULL,
    workload text NOT NULL,
    task_id text,
    session_id text,
    success boolean NOT NULL,
    accepted boolean,
    model text,
    route text,
    spend double precision,
    prompt_tokens bigint,
    completion_tokens bigint,
    cached_prompt_tokens bigint,
    evidence_uri text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (request_id, consumer, workload)
);

CREATE INDEX IF NOT EXISTS outcomes_efficiency_idx
    ON outcomes(trust_domain, consumer, workload, created_at);

COMMIT;
