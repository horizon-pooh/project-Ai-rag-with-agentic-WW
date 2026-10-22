import "dotenv/config";
import assert from "node:assert/strict";
import { readDatabaseConfig } from "../config/database.config.js";
import { createSearchDatabase, databaseErrorCode } from "../database/postgres.js";
import { createMetadataRepository } from "../repositories/metadata.repository.js";

async function main(): Promise<void> {
  const config = readDatabaseConfig(process.env);
  if (!config) throw new Error("Set DATA_SOURCE=postgres before checking metadata");
  const database = createSearchDatabase(config);
  try {
    await database.check();
    const client = await database.pool.connect();
    try {
      await client.query("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY");
      const metadata = createMetadataRepository(client);
      const audit = await metadata.audit();
      assert.match(audit.version,/^[a-f0-9]{64}$/);
      const categories = await metadata.findCategoryLabels("ทีวี");
      assert.ok(categories.some(c => c.category_id === "TV0100"));
      const attributes = await metadata.findAttributeLabels("TV0108","ขนาดหน้าจอ");
      assert.ok(attributes.some(a => a.attribute_id === "AT0048" && a.unit_code === "inch"));
      const centimetres = await metadata.convertUnit(10,"cm","mm");
      const inches = await metadata.convertUnit(55,"นิ้ว","inch");
      const unsupported = await metadata.convertUnit(1,"kg","L");
      assert.equal(Number(centimetres.value_out),100);
      assert.equal(Number(inches.value_out),55);
      assert.equal(unsupported.status,"unsupported_unit");
      console.log(JSON.stringify({status:"metadata_ready",audit,examples:{categories,attributes,
        centimetres,inches,unsupported}},null,2));
    } finally {
      try { await client.query("ROLLBACK"); } finally { client.release(); }
    }
  } finally { await database.close(); }
}
try { await main(); }
catch (error) {
  console.error(JSON.stringify({status:"failed",code:databaseErrorCode(error),
    message:"Check migration 0002, SSH tunnel and reader access. Do not paste credentials."}));
  process.exitCode=1;
}
