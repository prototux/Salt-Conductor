-- Salt Conductor schema. Idempotent: executed by the PostgreSQL init scripts and
-- again by Salt Conductor on startup.

-- ---------------------------------------------------------------------------
-- Salt pgjsonb returner (master_job_cache + event_return)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS jids (
    jid  varchar(255) NOT NULL PRIMARY KEY,
    load jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jids_jsonb ON jids USING gin (load) WITH (fastupdate=on);

CREATE TABLE IF NOT EXISTS salt_returns (
    fun        varchar(50) NOT NULL,
    jid        varchar(255) NOT NULL,
    return     jsonb NOT NULL,
    id         varchar(255) NOT NULL,
    success    varchar(10) NOT NULL,
    full_ret   jsonb NOT NULL,
    alter_time timestamp with time zone DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_salt_returns_id ON salt_returns (id);
CREATE INDEX IF NOT EXISTS idx_salt_returns_jid ON salt_returns (jid);
CREATE INDEX IF NOT EXISTS idx_salt_returns_fun ON salt_returns (fun);
CREATE INDEX IF NOT EXISTS idx_salt_returns_time ON salt_returns (alter_time);
CREATE INDEX IF NOT EXISTS idx_salt_returns_return ON salt_returns USING gin (return) WITH (fastupdate=on);
CREATE INDEX IF NOT EXISTS idx_salt_returns_full_ret ON salt_returns USING gin (full_ret) WITH (fastupdate=on);

CREATE SEQUENCE IF NOT EXISTS seq_salt_events_id;
CREATE TABLE IF NOT EXISTS salt_events (
    id         bigint NOT NULL UNIQUE DEFAULT nextval('seq_salt_events_id'),
    tag        varchar(255) NOT NULL,
    data       jsonb NOT NULL,
    alter_time timestamp with time zone DEFAULT now(),
    master_id  varchar(255) NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_salt_events_tag ON salt_events (tag);
CREATE INDEX IF NOT EXISTS idx_salt_events_time ON salt_events (alter_time);
CREATE INDEX IF NOT EXISTS idx_salt_events_data ON salt_events USING gin (data) WITH (fastupdate=on);

-- ---------------------------------------------------------------------------
-- Database pillar (served to minions by the conductor_db ext_pillar)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS conductor_pillar (
    id          serial PRIMARY KEY,
    target      text NOT NULL DEFAULT '*',
    tgt_type    text NOT NULL DEFAULT 'glob',
    key         text NOT NULL,
    value       jsonb NOT NULL,
    priority    integer NOT NULL DEFAULT 100,
    enabled     boolean NOT NULL DEFAULT true,
    sensitive   boolean NOT NULL DEFAULT false,
    description text NOT NULL DEFAULT '',
    created_at  timestamp with time zone NOT NULL DEFAULT now(),
    updated_at  timestamp with time zone NOT NULL DEFAULT now(),
    updated_by  text NOT NULL DEFAULT 'system'
);
CREATE INDEX IF NOT EXISTS idx_conductor_pillar_key ON conductor_pillar (key);

CREATE TABLE IF NOT EXISTS conductor_pillar_history (
    id         bigserial PRIMARY KEY,
    pillar_id  integer NOT NULL,
    action     text NOT NULL,
    before     jsonb,
    after      jsonb,
    changed_by text NOT NULL,
    changed_at timestamp with time zone NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_conductor_pillar_history_pid ON conductor_pillar_history (pillar_id);

-- ---------------------------------------------------------------------------
-- Salt Conductor internals
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS conductor_sessions (
    id          text PRIMARY KEY,
    username    text NOT NULL,
    eauth       text NOT NULL,
    salt_token  text NOT NULL,
    perms       jsonb NOT NULL DEFAULT '[]',
    created_at  timestamp with time zone NOT NULL DEFAULT now(),
    last_seen   timestamp with time zone NOT NULL DEFAULT now(),
    expires_at  timestamp with time zone NOT NULL,
    ip          text
);

CREATE TABLE IF NOT EXISTS conductor_audit (
    id       bigserial PRIMARY KEY,
    ts       timestamp with time zone NOT NULL DEFAULT now(),
    username text NOT NULL,
    action   text NOT NULL,
    target   text NOT NULL DEFAULT '',
    details  jsonb NOT NULL DEFAULT '{}',
    success  boolean NOT NULL DEFAULT true,
    ip       text
);
CREATE INDEX IF NOT EXISTS idx_conductor_audit_ts ON conductor_audit (ts DESC);

CREATE TABLE IF NOT EXISTS conductor_saved_commands (
    id          serial PRIMARY KEY,
    name        text NOT NULL,
    description text NOT NULL DEFAULT '',
    client      text NOT NULL DEFAULT 'local',
    tgt         text NOT NULL DEFAULT '*',
    tgt_type    text NOT NULL DEFAULT 'glob',
    fun         text NOT NULL,
    arg         jsonb NOT NULL DEFAULT '[]',
    kwarg       jsonb NOT NULL DEFAULT '{}',
    owner       text NOT NULL,
    shared      boolean NOT NULL DEFAULT true,
    created_at  timestamp with time zone NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Job metadata: with master_job_cache the "load" stored in jids may be the first
-- minion return, so the publish data (target, arguments, targeted minions) is
-- taken from the salt/job/<jid>/new event when event_return is enabled.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW conductor_jobs AS
SELECT j.jid,
       coalesce(e.data->>'fun', j.load->>'fun') AS fun,
       CASE WHEN jsonb_typeof(coalesce(e.data->'tgt', j.load->'tgt')) = 'array'
            THEN (SELECT string_agg(t, ',') FROM jsonb_array_elements_text(coalesce(e.data->'tgt', j.load->'tgt')) t)
            ELSE coalesce(e.data->>'tgt', j.load->>'tgt') END AS tgt,
       coalesce(e.data->>'tgt_type', j.load->>'tgt_type') AS tgt_type,
       coalesce(e.data->>'user', j.load->>'user') AS "user",
       coalesce(e.data->'arg', j.load->'arg', j.load->'fun_args', '[]'::jsonb) AS arg,
       e.data->'minions' AS minions,
       j.load
FROM jids j
LEFT JOIN LATERAL (
    SELECT data FROM salt_events
    WHERE tag = 'salt/job/' || j.jid || '/new'
    ORDER BY id LIMIT 1
) e ON true;

-- Runner / wheel returns are stored with their event envelope and success='false';
-- the real outcome is the envelope's own "success" flag.
CREATE OR REPLACE VIEW conductor_returns AS
SELECT r.*,
       CASE WHEN r.fun LIKE 'runner.%' OR r.fun LIKE 'wheel.%'
            THEN coalesce(CASE WHEN jsonb_typeof(r.return) = 'object' AND r.return->>'success' IN ('true', 'false')
                               THEN (r.return->>'success')::boolean END, r.success = 'true')
            ELSE r.success = 'true' END AS ok
FROM salt_returns r;
