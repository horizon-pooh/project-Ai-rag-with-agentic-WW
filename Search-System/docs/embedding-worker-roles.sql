-- Run as server PostgreSQL administrator, not the catalog owner or search API.
-- No passwords in this file. Set the login password interactively with \password embedding_worker.
BEGIN;
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='catalog_embedding_worker') THEN
  CREATE ROLE catalog_embedding_worker NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
 END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='embedding_worker') THEN
  CREATE ROLE embedding_worker LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 2;
 END IF;
 IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname IN ('catalog_embedding_worker','embedding_worker') AND
  (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls OR (rolname='catalog_embedding_worker' AND rolcanlogin))) THEN
  RAISE EXCEPTION 'Existing embedding role is unsafe; inspect it before proceeding';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='embedding_worker' AND rolcanlogin) THEN RAISE EXCEPTION 'Existing embedding_worker is not a login';END IF;
 IF EXISTS(SELECT 1 FROM pg_auth_members m JOIN pg_roles child ON child.oid=m.member JOIN pg_roles parent ON parent.oid=m.roleid
  WHERE child.rolname IN ('embedding_worker','catalog_embedding_worker') AND (parent.rolname<>'catalog_embedding_worker' OR m.admin_option)) THEN
  RAISE EXCEPTION 'Embedding roles have unexpected parent memberships';
 END IF;
END $$;
GRANT CONNECT ON DATABASE supabase_db_product TO catalog_embedding_worker;
GRANT catalog_embedding_worker TO embedding_worker;
ALTER ROLE embedding_worker CONNECTION LIMIT 2;
ALTER ROLE embedding_worker SET default_transaction_read_only=off;
ALTER ROLE embedding_worker SET statement_timeout='15s';
ALTER ROLE embedding_worker SET lock_timeout='5s';
ALTER ROLE embedding_worker SET idle_in_transaction_session_timeout='15s';
ALTER ROLE embedding_worker SET search_path=pg_catalog,public,extensions;
COMMIT;
