import { createHash } from "node:crypto";
import { readdir, readFile } from "node:fs/promises";
import { join } from "node:path";
import { runner } from "node-pg-migrate";
import type { ClientBase } from "pg";

interface MigrationFile { name: string; sha256: string }
interface HistoryRow { name: string }
interface ChecksumRow extends HistoryRow { sha256: string }
const quiet = { debug() {}, info() {}, warn() {}, error() {} };

export async function migrationFiles(directory: string): Promise<MigrationFile[]> {
  const names = (await readdir(directory)).filter(n => !n.startsWith("."));
  if (names.some(n => !/^\d+_[a-z0-9_-]+\.sql$/.test(n))) throw new Error("Unexpected file in migrations directory");
  const files = await Promise.all(names.map(async file => ({
    name: file.slice(0,-4), sha256: createHash("sha256").update(await readFile(join(directory,file))).digest("hex"),
  })));
  files.sort((a,b) => Number(a.name.split("_")[0])-Number(b.name.split("_")[0]));
  const prefixes = files.map(f => Number(f.name.split("_")[0]));
  if (new Set(prefixes).size !== prefixes.length) throw new Error("Duplicate migration index");
  return files;
}

export async function inspectMigrations(client: ClientBase, directory: string) {
  const files = await migrationFiles(directory);
  const exists = (await client.query<{ ledger: string | null; history: string | null }>(
    "SELECT to_regclass('search_migrations.checksums')::text AS ledger,to_regclass('search_migrations.pgmigrations')::text AS history"
  )).rows[0];
  const pinned = exists.ledger ? (await client.query<ChecksumRow>("SELECT name,sha256 FROM search_migrations.checksums")).rows : [];
  const history = exists.history ? (await client.query<HistoryRow>("SELECT name FROM search_migrations.pgmigrations ORDER BY id")).rows : [];
  const byName = new Map(files.map(f => [f.name,f.sha256]));
  const byPinned = new Map(pinned.map(f => [f.name,f.sha256]));
  for (const row of pinned) if (byName.get(row.name) !== row.sha256) throw new Error("Migration checksum mismatch or pinned file missing");
  for (const row of history) if (!byName.has(row.name) || !byPinned.has(row.name)) throw new Error("Migration history has no verified source checksum");
  const applied = history.map(r => r.name);
  if (applied.some((name,index) => files[index]?.name !== name)) throw new Error("Migration history is not an ordered prefix");
  return { files, applied, pending: files.filter(f => !applied.includes(f.name)).map(f => f.name) };
}

/** Core runner accepts an already connected owner client. CLI verifies DB/user separately. */
export async function applyMigrations(client: ClientBase, directory: string, direction: "up" | "down") {
  const lock = await client.query<{ locked: boolean }>("SELECT pg_try_advisory_lock(18000,10) AS locked");
  if (!lock.rows[0].locked) throw new Error("Another migration command is running");
  try {
    const plan = await inspectMigrations(client,directory);
    if ((direction === "up" && !plan.pending.length) || (direction === "down" && !plan.applied.length)) return [];
    await client.query(`CREATE SCHEMA IF NOT EXISTS search_migrations;
      REVOKE ALL ON SCHEMA search_migrations FROM PUBLIC;
      CREATE TABLE IF NOT EXISTS search_migrations.checksums (
        name text PRIMARY KEY, sha256 text NOT NULL CHECK(sha256 ~ '^[a-f0-9]{64}$'),
        pinned_at timestamptz NOT NULL DEFAULT now());`);
    // Persist before execution so a crash after a successful COMMIT cannot lose source identity.
    // Pinned files are immutable, even after a failed or rolled-back attempt.
    for (const file of plan.files) await client.query(
      "INSERT INTO search_migrations.checksums(name,sha256) VALUES($1,$2) ON CONFLICT(name) DO NOTHING", [file.name,file.sha256]);
    const results = await runner({ dbClient: client, dir: directory, direction,
      migrationsSchema: "search_migrations", migrationsTable: "pgmigrations", schema: "public",
      singleTransaction: true, checkOrder: true, noLock: true, count: direction === "down" ? 1 : undefined,
      logger: quiet,
    });
    return results.map(r => r.name);
  } finally { await client.query("SELECT pg_advisory_unlock(18000,10)"); }
}
