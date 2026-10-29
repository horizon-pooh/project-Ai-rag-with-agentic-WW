import {readFile,mkdir,writeFile} from "node:fs/promises";
import {parse} from "dotenv";
import {createServerEnvironment} from "../deployment/server-config.js";
try {
 const args=process.argv.slice(2);if(args.length!==0&&(args.length!==2||args[0]!=="--host"))throw new Error("Invalid configuration flags");
 const source:Record<string,string>={};
 for(const file of [".env",".env.embedding.local",".env.product-hybrid.local",".env.category-semantic.local",".env.query-cache.local"]){try{Object.assign(source,parse(await readFile(file,"utf8")));}catch(e){if(file!==".env.query-cache.local"||!(e&&typeof e==="object"&&"code" in e&&e.code==="ENOENT"))throw e;}}
 const env=createServerEnvironment(source,args[1]??"100.116.20.59");await mkdir("deploy-private",{recursive:true,mode:0o700});
 await writeFile("deploy-private/api.json",JSON.stringify(env,null,2)+"\n",{flag:"wx",mode:0o600});
 console.log(JSON.stringify({status:"server_configuration_created",path:"deploy-private/api.json",secret_printed:false}));
}catch{console.error(JSON.stringify({status:"server_configuration_failed",message:"ตรวจไฟล์ตั้งค่าเดิมและ host ส่วนตัว ไฟล์ปลายทางต้องยังไม่มี ไม่แสดงข้อมูลลับหรือเขียนทับค่าเดิม"}));process.exitCode=1;}
