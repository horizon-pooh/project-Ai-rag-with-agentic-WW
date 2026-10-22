import assert from "node:assert/strict";
import { test } from "node:test";
import { readDatabaseConfig, readDataSource, databasePoolOptions } from "../src/config/database.config.js";
import { assertSearchIdentity, createSearchDatabase, databaseErrorCode, type DatabaseIdentity } from "../src/database/postgres.js";
import { buildApp } from "../src/app.js";

const values = (): NodeJS.ProcessEnv => ({
  DATA_SOURCE: "postgres", SEARCH_DATABASE_URL: "postgresql://search_api@127.0.0.1:15432/supabase_db_product",
  SEARCH_DATABASE_PASSWORD: "test-only-password:@#?",
});
const identity = (): DatabaseIdentity => ({ database: "supabase_db_product", database_user: "search_api",
  server_version: "18.6", superuser: false, bypass_rls: false, create_role: false,
  create_database: false, reader_member: true, default_read_only: "on",
  owns_catalog: false, can_write_catalog: false, can_create_public: false });

test("mock mode needs no database credentials", () => {
  assert.equal(readDataSource(undefined), "mock");
  assert.equal(readDatabaseConfig({}), null);
});
test("validates loopback tunnel and keeps raw password separate from URL", () => {
  const input = values(); const config = readDatabaseConfig(input)!;
  assert.equal(config.host, "127.0.0.1"); assert.equal(config.port, 15432);
  assert.equal(config.user, "search_api"); assert.equal(config.password, input.SEARCH_DATABASE_PASSWORD);
  assert.equal(config.max, 5);
});
test("server-local connection defaults to 5432", () => {
  assert.equal(readDatabaseConfig({ ...values(), SEARCH_DATABASE_URL: "postgresql://search_api@localhost/supabase_db_product" })!.port, 5432);
});
test("IPv6 loopback is supported", () => {
  assert.equal(readDatabaseConfig({ ...values(), SEARCH_DATABASE_URL: "postgresql://search_api@[::1]:15432/supabase_db_product" })!.host, "::1");
});
for (const [name, change] of Object.entries({
  "unknown source": { DATA_SOURCE: "postgress" },
  "missing URL": { SEARCH_DATABASE_URL: undefined },
  "missing password": { SEARCH_DATABASE_PASSWORD: undefined },
  "blank password": { SEARCH_DATABASE_PASSWORD: " " },
  "wrong protocol": { SEARCH_DATABASE_URL: "https://search_api@127.0.0.1/supabase_db_product" },
  "admin username": { SEARCH_DATABASE_URL: "postgresql://antonius@127.0.0.1/supabase_db_product" },
  "wrong database": { SEARCH_DATABASE_URL: "postgresql://search_api@127.0.0.1/DB_test" },
  "remote host": { SEARCH_DATABASE_URL: "postgresql://search_api@100.116.20.59/supabase_db_product" },
  "password in URL": { SEARCH_DATABASE_URL: "postgresql://search_api:secret@127.0.0.1/supabase_db_product" },
  "TLS query override": { SEARCH_DATABASE_URL: "postgresql://search_api@127.0.0.1/supabase_db_product?sslmode=disable" },
  "fragment": { SEARCH_DATABASE_URL: "postgresql://search_api@127.0.0.1/supabase_db_product#secret" },
  "invalid port": { SEARCH_DATABASE_URL: "postgresql://search_api@127.0.0.1:99999/supabase_db_product" },
  "zero pool": { SEARCH_DATABASE_POOL_MAX: "0" },
  "excess pool": { SEARCH_DATABASE_POOL_MAX: "6" },
  "noninteger pool": { SEARCH_DATABASE_POOL_MAX: "2.5" },
})) test(`rejects unsafe config: ${name}`, () => assert.throws(() => readDatabaseConfig({ ...values(), ...change })));

test("pool has bounded connections and server/client timeouts", () => {
  const options = databasePoolOptions(readDatabaseConfig(values())!);
  assert.equal(options.max, 5); assert.equal(options.ssl, false);
  assert.equal(options.connectionTimeoutMillis, 5000);
  assert.equal(options.statement_timeout, 10000);
  assert.ok(options.options?.includes("default_transaction_read_only=on"));
});
test("configuration errors never echo the input URL/password", () => {
  try { readDatabaseConfig({ ...values(), SEARCH_DATABASE_URL: "postgresql://admin:NEVER_PRINT@remote/database" }); assert.fail(); }
  catch (error) { assert.ok(error instanceof Error); assert.ok(!error.message.includes("NEVER_PRINT")); }
});
test("safe identity passes", () => assert.doesNotThrow(() => assertSearchIdentity(identity())));
for (const [name, change] of Object.entries({
  "superuser": { superuser: true }, "BYPASSRLS": { bypass_rls: true },
  "role admin": { create_role: true }, "database admin": { create_database: true },
  "wrong user": { database_user: "antonius" }, "wrong DB": { database: "postgres" },
  "missing reader membership": { reader_member: false }, "read-write default": { default_read_only: "off" },
  "catalog ownership": { owns_catalog: true }, "catalog write": { can_write_catalog: true },
  "schema create": { can_create_public: true },
})) test(`rejects database identity: ${name}`, () => assert.throws(() => assertSearchIdentity({ ...identity(), ...change })));
test("database diagnostics omit raw error messages and credentials", () => {
  assert.equal(databaseErrorCode({ code: "28P01", message: "secret password" }), "28P01");
  assert.equal(databaseErrorCode(new Error("postgresql://secret")), "DATABASE_UNAVAILABLE");
  assert.equal(databaseErrorCode({ code: "contains secret" }), "DATABASE_UNAVAILABLE");
});
test("closing an unused pool is idempotent and opens no database connection", async () => {
  const database = createSearchDatabase(readDatabaseConfig(values())!);
  await Promise.all([database.close(), database.close()]);
});
test("Fastify startup checks connection and closes it on shutdown", async () => {
  let checks = 0; let closes = 0;
  const app = buildApp({ database: { async check() { checks++; return identity(); }, async close() { closes++; } } });
  try {
    const response = await app.inject({ method: "GET", url: "/health" });
    assert.equal(response.statusCode, 200); assert.equal(checks, 1);
    assert.deepEqual(response.json(), { status: "ok", service: "search-system" });
  } finally { await app.close(); }
  assert.equal(closes, 1);
});
test("Fastify refuses readiness when database authentication fails", async () => {
  let closes = 0;
  const app = buildApp({ database: { async check() { throw new Error("authentication failed"); }, async close() { closes++; } } });
  await assert.rejects(async () => { await app.ready(); }); await app.close(); assert.equal(closes, 1);
});
test("mock app can serve health without a database", async () => {
  const app = buildApp({ database: null });
  try { assert.equal((await app.inject({ method: "GET", url: "/health" })).statusCode, 200); }
  finally { await app.close(); }
});
