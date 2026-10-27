import {randomBytes} from "node:crypto";
import {readFile,writeFile,chmod} from "node:fs/promises";
import {execFileSync} from "node:child_process";
import {parse} from "dotenv";
import {readToolApiConfig} from "../config/tool-api.config.js";

try {
  // Keep the generated secret out of Git and terminal output. Run from Search-System.
  execFileSync("git",["check-ignore","-q",".env"],{stdio:"ignore"});
  let text=await readFile(".env","utf8");const values=parse(text);
  if(values.DATA_SOURCE!=="postgres")throw new Error("PostgreSQL configuration required");
  const token=values.SEARCH_API_TOKEN || randomBytes(32).toString("base64url");
  readToolApiConfig({SEARCH_API_ENABLED:"true",SEARCH_API_TOKEN:token});
  const lines=text.split(/\r?\n/).filter(line=>!/^\s*(?:export\s+)?SEARCH_API_(?:ENABLED|TOKEN)\s*=/.test(line));
  text=lines.join("\n").replace(/\s*$/,"")+"\nSEARCH_API_ENABLED=true\nSEARCH_API_TOKEN="+token+"\n";
  await writeFile(".env",text,{mode:0o600});await chmod(".env",0o600);
  console.log(JSON.stringify({status:"tool_api_configured",secret_printed:false,existing_token_preserved:Boolean(values.SEARCH_API_TOKEN)}));
} catch {
  console.error(JSON.stringify({status:"failed",message:"Run in Search-System with an existing ignored .env, DATA_SOURCE=postgres and a valid existing token if set. No secret is printed."}));process.exitCode=1;
}
