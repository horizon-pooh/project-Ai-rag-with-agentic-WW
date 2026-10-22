import { Pool } from "pg";
import { databasePoolOptions, type DatabaseConfig } from "../config/database.config.js";

export interface DatabaseIdentity {
  database: string;
  database_user: string;
  server_version: string;
  superuser: boolean;
  bypass_rls: boolean;
  create_role: boolean;
  create_database: boolean;
  reader_member: boolean;
  default_read_only: string;
  owns_catalog: boolean;
  can_write_catalog: boolean;
  can_create_public: boolean;
}
export interface DatabaseLifecycle {
  check(): Promise<DatabaseIdentity>;
  close(): Promise<void>;
}

/** Public diagnostics contain only a bounded error code, never a URL/password/query. */
export function databaseErrorCode(error: unknown): string {
  if (typeof error === "object" && error !== null && "code" in error &&
      typeof error.code === "string" && /^[A-Z0-9_]{1,40}$/.test(error.code)) return error.code;
  return "DATABASE_UNAVAILABLE";
}

export function assertSearchIdentity(identity: DatabaseIdentity): void {
  if (identity.database !== "supabase_db_product" || identity.database_user !== "search_api" ||
      identity.superuser || identity.bypass_rls || identity.create_role || identity.create_database ||
      !identity.reader_member || identity.default_read_only !== "on" ||
      identity.owns_catalog || identity.can_write_catalog || identity.can_create_public) {
    throw new Error("Database identity does not satisfy search runtime requirements");
  }
}

export function createSearchDatabase(
  config: DatabaseConfig,
  onIdleError: (code: string) => void = () => {},
): DatabaseLifecycle & { pool: Pool } {
  const pool = new Pool(databasePoolOptions(config));
  pool.on("error", error => onIdleError(databaseErrorCode(error)));
  let closing: Promise<void> | undefined;
  return {
    pool,
    async check() {
      const result = await pool.query<DatabaseIdentity>(`
        SELECT current_database() AS database, current_user AS database_user,
          current_setting('server_version') AS server_version,
          r.rolsuper AS superuser, r.rolbypassrls AS bypass_rls,
          r.rolcreaterole AS create_role, r.rolcreatedb AS create_database,
          pg_has_role(current_user, 'catalog_search_reader', 'MEMBER') AS reader_member,
          current_setting('default_transaction_read_only') AS default_read_only,
          EXISTS (SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname IN ('public','dataset_admin') AND c.relkind IN ('r','p','v')
            AND pg_has_role(r.oid,c.relowner,'USAGE')) AS owns_catalog,
          EXISTS (SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname IN ('public','dataset_admin') AND c.relkind IN ('r','p')
            AND has_table_privilege(r.oid,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES')) AS can_write_catalog,
          has_schema_privilege(current_user,'public','CREATE') AS can_create_public
        FROM pg_catalog.pg_roles r WHERE r.rolname = current_user
      `);
      const identity = result.rows[0];
      if (!identity) throw new Error("Database identity unavailable");
      assertSearchIdentity(identity);
      return identity;
    },
    close() { return closing ??= pool.end(); },
  };
}
