import { buildApp } from "./app.js";
import { env } from "./config/env.config.js";

const app = buildApp();

const start = async (): Promise<void> => {
  try {
    await app.listen({
      host: env.host,
      port: env.port,
    });
  } catch (error) {
    app.log.error(error);
    process.exit(1);
  }
};

await start();