import {readFile,mkdir,writeFile} from "node:fs/promises";
try{
 if(process.platform!=="linux"||process.versions.node.split(".")[0]!=="24"||!/^\/[A-Za-z0-9_./-]+$/.test(process.execPath))throw new Error("Require server Node24 path");
 await mkdir("deploy-private/systemd",{recursive:true,mode:0o700});
 for(const name of ["search-tool-api.service","search-embedding.service"]){const text=(await readFile("deploy/systemd/"+name,"utf8")).replaceAll("@@NODE_BIN@@",process.execPath);await writeFile("deploy-private/systemd/"+name,text,{flag:"wx",mode:0o600});}
 console.log(JSON.stringify({status:"units_rendered",directory:"deploy-private/systemd",services_started:false}));
}catch{console.error(JSON.stringify({status:"unit_render_failed",message:"รันบน Linux ด้วย Node24 ใช้ path ที่ไม่มีอักขระพิเศษ และไฟล์ปลายทางต้องยังไม่มี ไม่เปลี่ยน service ที่รันอยู่"}));process.exitCode=1;}
