import "dotenv/config";
import { readDataSource } from "./database.config.js";

const parsePort = (value: string | undefined): number => {
  const port = Number(value ?? 3000);
  if (!Number.isInteger(port) || port <= 0 || port > 65535) throw new Error("PORT must be a valid port number");
  return port;
};

export const env = {
  nodeEnv: process.env.NODE_ENV ?? "development",
  host: process.env.HOST ?? "127.0.0.1",
  port: parsePort(process.env.PORT),
  dataSource: readDataSource(process.env.DATA_SOURCE),
} as const;