import {loadServerEnvironment,applyServerEnvironment} from "../deployment/server-config.js";
import {readDatabaseConfig} from "../config/database.config.js";
import {createSearchDatabase} from "../database/postgres.js";
import {verifyStartup} from "../deployment/startup-check.js";
try{
 if(process.versions.node.split(".")[0]!=="24")throw new Error("Require tested Node major");
 const file=process.env.SEARCH_SERVER_CONFIG;if(!file)throw new Error("Require private configuration");
 const env=await loadServerEnvironment(file),config=readDatabaseConfig(env);if(!config)throw new Error("Require PostgreSQL");const db=createSearchDatabase(config);
 try{await db.check();console.log(JSON.stringify(await verifyStartup(db.pool,env)));}finally{await db.close();}
 applyServerEnvironment(env);await import("../server.js");
}catch{console.error(JSON.stringify({status:"server_start_failed",message:"ตรวจ config และใช้ server:check ก่อนเปิด API ไม่แสดงข้อมูลลับหรือข้อผิดพลาดดิบ"}));process.exitCode=1;}
