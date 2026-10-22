import type { PoolConfig } from "pg";

export type DataSource = "mock" | "postgres";
export interface DatabaseConfig {
  host: string;
  port: number;
  database: "supabase_db_product";
  user: "search_api";
  password: string;
  max: number;
}

export function readDataSource(value: string | undefined): DataSource {
  const source = value ?? "mock";
  if (source !== "mock" && source !== "postgres") throw new Error("DATA_SOURCE must be mock or postgres");
  return source;
}

/** Commit 10 supports loopback via SSH tunnel or server-local PostgreSQL only. */
export function readDatabaseConfig(values: NodeJS.ProcessEnv): DatabaseConfig | null {
  if (readDataSource(values.DATA_SOURCE) === "mock") return null;
  let url: URL;
  try { url = new URL(values.SEARCH_DATABASE_URL ?? ""); }
  catch { throw new Error("SEARCH_DATABASE_URL must be a valid PostgreSQL URL"); }
  if (!["postgresql:", "postgres:"].includes(url.protocol)) throw new Error("SEARCH_DATABASE_URL must use PostgreSQL");
  if (!["127.0.0.1", "localhost", "[::1]"].includes(url.hostname)) {
    throw new Error("Database host must be loopback; use an SSH tunnel during development");
  }
  // Do not allow URL options to silently override pool timeouts, TLS or role settings.
  if (url.search || url.hash || url.password) {
    throw new Error("Database URL must have no query, fragment or password; use SEARCH_DATABASE_PASSWORD");
  }
  if (url.username !== "search_api" || url.pathname !== "/supabase_db_product") {
    throw new Error("Database URL must use search_api and supabase_db_product");
  }
  const password = values.SEARCH_DATABASE_PASSWORD;
  if (!password || password.trim() === "") throw new Error("SEARCH_DATABASE_PASSWORD is required");
  const maxText = values.SEARCH_DATABASE_POOL_MAX ?? "5";
  if (!/^[1-5]$/.test(maxText)) throw new Error("SEARCH_DATABASE_POOL_MAX must be an integer from 1 to 5");
  const port = Number(url.port || "5432");
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("Invalid database port");
  return {
    host: url.hostname === "[::1]" ? "::1" : url.hostname, port,
    database: "supabase_db_product", user: "search_api", password, max: Number(maxText),
  };
}

export function databasePoolOptions(config: DatabaseConfig): PoolConfig {
  return {
    ...config,
    ssl: false, // Transport is loopback; remote leg is encrypted by SSH, not plaintext TCP.
    application_name: "search-tool-api",
    connectionTimeoutMillis: 5000,
    idleTimeoutMillis: 30000,
    statement_timeout: 10000,
    query_timeout: 15000,
    idle_in_transaction_session_timeout: 15000,
    options: "-c default_transaction_read_only=on -c search_path=pg_catalog,public,extensions",
  };
}
