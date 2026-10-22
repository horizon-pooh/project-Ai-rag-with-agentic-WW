import "dotenv/config";
import { readDatabaseConfig } from "../config/database.config.js";
import { createSearchDatabase, databaseErrorCode } from "../database/postgres.js";

async function main(): Promise<void> {
  const config = readDatabaseConfig(process.env);
  if (!config) throw new Error("Set DATA_SOURCE=postgres before running db:check");
  const database = createSearchDatabase(config, code => console.error(JSON.stringify({ event: "database_idle_error", code })));
  try {
    const identity = await database.check();
    console.log(JSON.stringify({ status: "connected", ...identity }, null, 2));
  } finally { await database.close(); }
}

try { await main(); }
catch (error) {
  console.error(JSON.stringify({ status: "failed", code: databaseErrorCode(error),
    message: "Check local environment, SSH tunnel, login credentials and reader membership. Do not paste secrets in chat." }));
  process.exitCode = 1;
}
