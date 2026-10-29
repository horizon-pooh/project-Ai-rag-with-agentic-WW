import {loadServerEnvironment} from "../deployment/server-config.js";
import {readDatabaseConfig} from "../config/database.config.js";
import {createSearchDatabase} from "../database/postgres.js";
import {verifyStartup} from "../deployment/startup-check.js";
let db:ReturnType<typeof createSearchDatabase>|undefined;
try{
 if(process.versions.node.split(".")[0]!=="24")throw new Error("Require tested Node major");
 const path=process.env.SEARCH_SERVER_CONFIG;if(!path)throw new Error("Require server config file");const env=await loadServerEnvironment(path),config=readDatabaseConfig(env);if(!config)throw new Error("Require PostgreSQL");
 db=createSearchDatabase(config);await db.check();const report=await verifyStartup(db.pool,env);console.log(JSON.stringify(report));
}catch{console.error(JSON.stringify({status:"server_startup_blocked",message:"ตรวจ Node24 config ส่วนตัว ฐาน โมเดล และ profile ที่ลงทะเบียน ไม่เปิด API จนตรวจผ่าน"}));process.exitCode=1;}
finally{await db?.close();}
