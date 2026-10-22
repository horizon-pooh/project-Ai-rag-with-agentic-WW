import { buildApp } from "./app.js";
import { env } from "./config/env.config.js";
import { databaseErrorCode } from "./database/postgres.js";

const app = buildApp();
let closing = false;
async function stop(): Promise<void> {
  if (closing) return;
  closing = true;
  const deadline = setTimeout(() => { process.exit(1); }, 20000);
  deadline.unref();
  try { await app.close(); }
  catch (error) { app.log.error({ code: databaseErrorCode(error) }, "Shutdown failed"); process.exitCode = 1; }
  finally { clearTimeout(deadline); }
}
process.once("SIGINT", () => { void stop(); });
process.once("SIGTERM", () => { void stop(); });
try { await app.listen({ host: env.host, port: env.port }); }
catch (error) {
  app.log.error({ code: databaseErrorCode(error) }, "Startup failed");
  process.exitCode = 1;
  await stop();
}