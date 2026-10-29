import "dotenv/config";
import {stat,writeFile} from "node:fs/promises";
import {readDatabaseConfig} from "../config/database.config.js";
import {createSearchDatabase} from "../database/postgres.js";
import {inspectSearchPlans} from "../performance/search-plan-audit.js";
let database:ReturnType<typeof createSearchDatabase>|undefined;
try {
 const args=process.argv.slice(2);if(args.length!==2||args[0]!=="--out")throw new Error("Require output path");
 const out=args[1]!,exists=await stat(out).then(()=>true,e=>{if(e.code==="ENOENT")return false;throw e;});if(exists)throw new Error("Output exists");
 const config=readDatabaseConfig(process.env);if(!config)throw new Error("PostgreSQL required");database=createSearchDatabase(config);await database.check();
 const client=await database.pool.connect();let report;
 try{report=await inspectSearchPlans(client);}finally{client.release();}
 await writeFile(out,JSON.stringify({checked_at:new Date().toISOString(),...report},null,2)+"\n",{flag:"wx",mode:0o600});
 console.log(JSON.stringify({status:report.status,visibility:report.visibility,write_probes_denied:report.write_probes_denied,statement_timeout:report.statement_timeout,plans:report.plans.map(p=>({id:p.id,status:p.status}))}));
 if(report.status!=="passed")process.exitCode=1;
}catch{console.error(JSON.stringify({status:"inspection_failed",message:"ตรวจ tunnel บัญชีอ่าน และ path รายงานที่ยังไม่มีไฟล์ ไม่แสดงข้อมูลลับหรือ SQL error"}));process.exitCode=1;}
finally{await database?.close();}
