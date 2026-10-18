#!/bin/sh
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -v app_password="$APP_DB_PASSWORD" <<'SQL'
CREATE ROLE majlis_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'app_password';
ALTER DATABASE majlis OWNER TO majlis_app;
ALTER SCHEMA public OWNER TO majlis_app;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SQL
