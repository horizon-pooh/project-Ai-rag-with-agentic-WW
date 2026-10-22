import Fastify from "fastify";
import { readDatabaseConfig } from "./config/database.config.js";
import { createSearchDatabase, type DatabaseLifecycle } from "./database/postgres.js";

export function buildApp(options: { database?: DatabaseLifecycle | null } = {}) {
  const app = Fastify({ logger: true });
  const config = options.database === undefined ? readDatabaseConfig(process.env) : null;
  const database = options.database === undefined
    ? (config ? createSearchDatabase(config, code => app.log.error({ code }, "Idle database connection failed")) : null)
    : options.database;

  if (database) {
    app.addHook("onReady", async () => { await database.check(); });
    app.addHook("onClose", async () => { await database.close(); });
  }
  app.get("/health", async () => ({ status: "ok", service: "search-system" }));
  return app;
}