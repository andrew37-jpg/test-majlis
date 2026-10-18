-- Run ONLY after backup, stopping old API/bot/notification workers and approval
-- for administrative operations in the existing environment.
-- psql -v ON_ERROR_STOP=1 -v migration_password=... -U majlis -d majlis -f ...
-- Password must be supplied locally, never logged/shared.
SELECT format('CREATE ROLE majlis_migrator LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD %L', :'migration_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='majlis_migrator') \gexec
ALTER DATABASE majlis OWNER TO majlis_migrator;
ALTER SCHEMA public OWNER TO majlis_migrator;
-- Unlike REASSIGN OWNED, this does not change other databases owned by this role.
SELECT format('ALTER TABLE public.%I OWNER TO majlis_migrator', c.relname)
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND c.relkind IN ('r','p') \gexec
-- Ownership of identity/serial dependent sequences follows table ownership.
SELECT format('ALTER SEQUENCE public.%I OWNER TO majlis_migrator', c.relname)
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND c.relkind='S'
AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid=c.oid AND d.deptype IN ('a','i')) \gexec
REVOKE ALL ON DATABASE majlis FROM PUBLIC;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM majlis_app;
GRANT CONNECT ON DATABASE majlis TO majlis_app;
GRANT USAGE ON SCHEMA public TO majlis_app;
-- Next: set MIGRATION_DATABASE_URL, run python -m majlis.cli migrate.
-- Database.migrate grants explicit DML; runtime cannot DDL or rewrite audit.
