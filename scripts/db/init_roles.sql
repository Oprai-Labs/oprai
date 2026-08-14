-- Per-service database roles — OWNER-BASED, migration-safe.
--
-- WHY OWNER-BASED: every service runs DDL at startup (auth/admin do
-- `CREATE SCHEMA IF NOT EXISTS`; chat/memory run `alembic upgrade head`;
-- solana runs its CREATE TABLE migrations). A DML-only grant model would make
-- all five fail their boot migration with "permission denied". So each *_app
-- role OWNS its schema and every object in it: it can migrate (DDL) and read/
-- write (DML) inside its own schema, and has NO access to any other service's
-- schema. The `postgres` superuser keeps full access (superuser bypasses
-- ownership), so backups, cross-schema admin SQL, and manual migrations are
-- unaffected.
--
-- BLAST-RADIUS RESULT: a SQL-injection (or ORM escape) in one service can touch
-- only that service's schema — not all five, and not COPY..PROGRAM as superuser.
--
-- Run ONCE as superuser, AFTER the schemas/objects already exist (i.e. after the
-- services have booted at least once and run their migrations):
--   psql "$SUPERUSER_DATABASE_URL" \
--     -v auth_pass=... -v chat_pass=... -v solana_pass=... \
--     -v memory_pass=... -v admin_pass=... \
--     -f scripts/db/init_roles.sql
--
-- Then point each service's DATABASE_URL at its role:
--   auth-service   -> auth_app     chat-service   -> chat_app
--   solana-service -> solana_app   memory-service -> memory_app
--   admin-service  -> admin_app
--
-- Re-runnable: creating roles is idempotent, ownership/ grants are declarative.

\set ON_ERROR_STOP on

-- Ownership changes take a brief ACCESS EXCLUSIVE lock per object. Cap the wait
-- so a busy table can never make this script block production behind it — if a
-- lock can't be taken in time the script aborts (ON_ERROR_STOP) and is simply
-- re-run (every step here is idempotent). No partial-state harm.
SET lock_timeout = '5s';
SET statement_timeout = '60s';

-- ── 1) Roles (idempotent; (re)set the login password each run) ────────────────
-- NOTE: psql `:'var'` substitution does NOT reach inside a dollar-quoted DO
-- block, so role creation is done with top-level statements: create-if-missing
-- via \gexec, then an unconditional ALTER to (re)set the password.
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', 'auth_app', :'auth_pass')
    WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'auth_app') \gexec
ALTER ROLE auth_app WITH LOGIN PASSWORD :'auth_pass';

SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', 'chat_app', :'chat_pass')
    WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'chat_app') \gexec
ALTER ROLE chat_app WITH LOGIN PASSWORD :'chat_pass';

SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', 'solana_app', :'solana_pass')
    WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'solana_app') \gexec
ALTER ROLE solana_app WITH LOGIN PASSWORD :'solana_pass';

SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', 'memory_app', :'memory_pass')
    WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'memory_app') \gexec
ALTER ROLE memory_app WITH LOGIN PASSWORD :'memory_pass';

SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', 'admin_app', :'admin_pass')
    WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'admin_app') \gexec
ALTER ROLE admin_app WITH LOGIN PASSWORD :'admin_pass';

-- ── 2) Ownership: each role owns its schema + every object in it ───────────────
-- Covers ordinary tables (r), partitioned tables (p), partitions, sequences (S),
-- views (v) and materialised views (m). Re-running is a no-op once owned.
DO $$
DECLARE
    m record;
    o record;
BEGIN
    FOR m IN
        SELECT * FROM (VALUES
            ('auth_schema',   'auth_app'),
            ('chat_schema',   'chat_app'),
            ('solana_schema', 'solana_app'),
            ('memory_schema', 'memory_app'),
            ('admin_schema',  'admin_app')
        ) AS t(sch, rol)
    LOOP
        EXECUTE format('ALTER SCHEMA %I OWNER TO %I', m.sch, m.rol);
        FOR o IN
            SELECT c.relname, c.relkind
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = m.sch
              AND c.relkind IN ('r', 'p', 'S', 'v', 'm')
        LOOP
            IF o.relkind IN ('r', 'p') THEN
                EXECUTE format('ALTER TABLE %I.%I OWNER TO %I', m.sch, o.relname, m.rol);
            ELSIF o.relkind = 'S' THEN
                EXECUTE format('ALTER SEQUENCE %I.%I OWNER TO %I', m.sch, o.relname, m.rol);
            ELSIF o.relkind = 'v' THEN
                EXECUTE format('ALTER VIEW %I.%I OWNER TO %I', m.sch, o.relname, m.rol);
            ELSIF o.relkind = 'm' THEN
                EXECUTE format('ALTER MATERIALIZED VIEW %I.%I OWNER TO %I', m.sch, o.relname, m.rol);
            END IF;
        END LOOP;
    END LOOP;
END $$;

-- Functions/procedures in a service's own schema follow the same rule.
DO $$
DECLARE
    m record;
    f record;
BEGIN
    FOR m IN
        SELECT * FROM (VALUES
            ('auth_schema',   'auth_app'),
            ('chat_schema',   'chat_app'),
            ('solana_schema', 'solana_app'),
            ('memory_schema', 'memory_app'),
            ('admin_schema',  'admin_app')
        ) AS t(sch, rol)
    LOOP
        FOR f IN
            SELECT p.oid::regprocedure AS sig
            FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = m.sch
        LOOP
            EXECUTE format('ALTER ROUTINE %s OWNER TO %I', f.sig, m.rol);
        END LOOP;
    END LOOP;
END $$;

-- ── 2b) CREATE on the database — all five services CREATE SCHEMA at boot ───────
-- Every service runs `CREATE SCHEMA IF NOT EXISTS <its schema>` on startup
-- (auth/admin in Go, chat/memory via SQLAlchemy, solana in migration 001).
-- Postgres checks CREATE-on-database BEFORE the IF NOT EXISTS short-circuits, so
-- without this the statement errors "permission denied for database" and the
-- service won't boot (proven on a restored copy). It grants only the ability to
-- create NEW, self-owned schemas — no access to any existing schema the role
-- doesn't already own — so cross-service isolation is unchanged.
SELECT format('GRANT CREATE ON DATABASE %I TO auth_app, chat_app, solana_app, memory_app, admin_app', current_database()) \gexec

-- ── 3) public schema: keep USAGE so built-in funcs / plpgsql stay reachable ───
-- We do NOT grant CREATE on public, and we do NOT revoke USAGE (revoking it
-- broke nothing observable here but is a needless regression risk — the roles
-- have no objects in public and cannot create any without CREATE).
GRANT USAGE ON SCHEMA public TO auth_app, chat_app, solana_app, memory_app, admin_app;

-- ── 4) Cross-schema grants (least privilege) ──────────────────────────────────
-- solana_app: NONE. Verified the Rust solana-service never reads another schema.
--
-- admin_app: the admin panel reads across services and performs three narrow
-- column-scoped writes. Read-only everywhere else; owner of admin_schema (above).
GRANT USAGE ON SCHEMA auth_schema, chat_schema, solana_schema, memory_schema TO admin_app;
GRANT SELECT ON ALL TABLES IN SCHEMA auth_schema   TO admin_app;
GRANT SELECT ON ALL TABLES IN SCHEMA chat_schema   TO admin_app;
GRANT SELECT ON ALL TABLES IN SCHEMA solana_schema TO admin_app;
GRANT SELECT ON ALL TABLES IN SCHEMA memory_schema TO admin_app;
-- The only cross-schema writes the admin service makes:
GRANT UPDATE (status, status_reason, status_changed_at, status_changed_by, role, updated_at)
    ON auth_schema.users TO admin_app;
GRANT UPDATE (is_deleted, deleted_at, updated_at)
    ON chat_schema.chat_sessions TO admin_app;
-- Issue reports: the user files them (chat_app writes the row), an admin
-- triages them. Column-scoped like the two above — admin can move a report
-- through its states and answer it, and cannot touch what the user wrote.
GRANT UPDATE (status, admin_note, updated_at)
    ON chat_schema.issue_reports TO admin_app;

-- Future tables the owner roles create in later migrations must stay readable to
-- admin (default privileges are recorded per granting-role + schema).
ALTER DEFAULT PRIVILEGES FOR ROLE auth_app   IN SCHEMA auth_schema   GRANT SELECT ON TABLES TO admin_app;
ALTER DEFAULT PRIVILEGES FOR ROLE chat_app   IN SCHEMA chat_schema   GRANT SELECT ON TABLES TO admin_app;
ALTER DEFAULT PRIVILEGES FOR ROLE solana_app IN SCHEMA solana_schema GRANT SELECT ON TABLES TO admin_app;
ALTER DEFAULT PRIVILEGES FOR ROLE memory_app IN SCHEMA memory_schema GRANT SELECT ON TABLES TO admin_app;
