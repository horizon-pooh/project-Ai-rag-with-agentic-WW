import assert from "node:assert/strict";
import {test} from "node:test";
import {execFile} from "node:child_process";
import {promisify} from "node:util";
import {mkdtemp,mkdir,writeFile,readFile,copyFile,rm,chmod,stat} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join,resolve} from "node:path";
import {createHash} from "node:crypto";
const exec=promisify(execFile);
test("แพ็ก release เฉพาะ commit โดยไม่รวมไฟล์ส่วนตัว",async t=>{
 const dir=await mkdtemp(join(tmpdir(),"search-release-")),app=join(dir,"Search-System");
 try{
  await mkdir(join(app,"scripts"),{recursive:true});await copyFile(resolve("scripts/package-search-release.sh"),join(app,"scripts/package-search-release.sh"));await writeFile(join(app,"package.json"),'{"name":"release-fixture"}');await writeFile(join(app,".gitignore"),".env\n/deploy-private/\n/reports/\n");await writeFile(join(app,"README.md"),"fixture\n");
  await exec("git",["init","-q",dir]);await exec("git",["-C",dir,"add","Search-System"]);await exec("git",["-C",dir,"-c","user.name=Test","-c","user.email=test@example.invalid","commit","-qm","fixture"]);
  const rev=(await exec("git",["-C",dir,"rev-parse","HEAD"])).stdout.trim();await writeFile(join(app,".env"),"SECRET=must-not-be-packed");await mkdir(join(app,"reports"));await writeFile(join(app,"reports/private.json"),"must-not-be-packed");
  await t.test("แพ็กตรวจ checksum และ revision ได้",async()=>{await exec("bash",[join(app,"scripts/package-search-release.sh")],{cwd:app});const archive=join(app,"deploy-private/releases",rev+".tar.gz"),bytes=await readFile(archive),checksum=await readFile(archive+".sha256","utf8");assert.equal(checksum,createHash("sha256").update(bytes).digest("hex")+"  "+rev+".tar.gz\n");const list=(await exec("tar",["-tzf",archive])).stdout;assert.ok(list.includes("Search-System/package.json"));assert.ok(!list.includes("Search-System/.env"));assert.ok(!list.includes("reports/"));const found=await exec("bash",["-c",'gzip -cd "$1" | git get-tar-commit-id',"test",archive]);assert.equal(found.stdout.trim(),rev);assert.equal((await stat(archive)).mode&0o077,0);});
  await t.test("ไม่ทับ release เดิม",async()=>{await assert.rejects(exec("bash",[join(app,"scripts/package-search-release.sh")],{cwd:app}));});
  await t.test("งานยังไม่ commit หยุดแพ็ก",async()=>{await writeFile(join(app,"README.md"),"changed");await assert.rejects(exec("bash",[join(app,"scripts/package-search-release.sh")],{cwd:app}));await exec("git",["-C",dir,"restore","Search-System/README.md"]);});
  await t.test("installer ตรวจ archive และแยก release โดยไม่ activate (fixture commands)",async()=>{
   const home=join(dir,"server-home"),bin=join(dir,"server-bin"),incoming=join(home,"search-tool/incoming");await mkdir(incoming,{recursive:true});await mkdir(bin);const archive=join(app,"deploy-private/releases",rev+".tar.gz");await copyFile(archive,join(incoming,rev+".tar.gz"));await copyFile(archive+".sha256",join(incoming,rev+".tar.gz.sha256"));
   for(const [name,text] of [["uname", "printf Linux"], ["node", "printf 24"], ["sha256sum", "exec shasum -a 256 \"$@\""], ["npm", "printf '%s\\n' \"$*\" >> \"$NPM_LOG\""]]){await writeFile(join(bin,name!),"#!/bin/sh\n"+text+"\n");await chmod(join(bin,name!),0o700);}
   const env={...process.env,HOME:home,PATH:bin+":"+process.env.PATH,NPM_LOG:join(dir,"npm-log")};await exec("bash",[resolve("deploy/install-release.sh"),rev],{env});assert.equal((await readFile(join(home,"search-tool/releases",rev,".build-complete"),"utf8")).trim(),rev);await assert.rejects(stat(join(home,"search-tool/current")));assert.ok((await readFile(join(dir,"npm-log"),"utf8")).includes("prune --omit=dev"));await assert.rejects(exec("bash",[resolve("deploy/install-release.sh"),rev],{env}));
  });
  await t.test("ไฟล์ลับที่เผลอ track ทำให้แพ็กหยุด",async()=>{await exec("git",["-C",dir,"add","-f","Search-System/.env"]);await exec("git",["-C",dir,"-c","user.name=Test","-c","user.email=test@example.invalid","commit","-qm","private-fixture"]);await assert.rejects(exec("bash",[join(app,"scripts/package-search-release.sh")],{cwd:app}));});
 }finally{await rm(dir,{recursive:true,force:true});}
});
test("managed model ไม่แตะ process ที่ใช้พอร์ตอยู่แล้ว",async()=>{
 const dir=await mkdtemp(join(tmpdir(),"managed-model-"));try{const bin=join(dir,"bin"),scripts=join(dir,"scripts");await mkdir(bin);await mkdir(scripts);await copyFile(resolve("scripts/start-managed-embedding.sh"),join(scripts,"start-managed-embedding.sh"));await writeFile(join(scripts,"start-embedding-server.sh"),'echo started > "$MARKER"\n');for(const [name,text] of [["uname","printf Linux"],["ss","printf LISTEN"]]){await writeFile(join(bin,name!),"#!/bin/sh\n"+text+"\n");await chmod(join(bin,name!),0o700);}const marker=join(dir,"marker");await assert.rejects(exec("bash",[join(scripts,"start-managed-embedding.sh")],{env:{...process.env,PATH:bin+":/usr/bin:/bin",MARKER:marker}}),(e:any)=>e.code===78);await assert.rejects(stat(marker));}finally{await rm(dir,{recursive:true,force:true});}
});
test("shell syntax ถูกและ API ไม่บังคับ restart โมเดลเดิม",async()=>{for(const file of ["scripts/package-search-release.sh","scripts/start-managed-embedding.sh","deploy/install-release.sh","deploy/activate-release.sh"])await exec("bash",["-n",file]);const unit=await readFile("deploy/systemd/search-tool-api.service","utf8");assert.ok(!unit.includes("Requires=search-embedding"));assert.ok(!unit.includes("Wants=search-embedding"));assert.ok(unit.includes("start-server-release.js"));const model=await readFile("deploy/systemd/search-embedding.service","utf8");assert.ok(model.includes("RestartPreventExitStatus=78"));});
