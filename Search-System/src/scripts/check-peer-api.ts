import "dotenv/config";
import {privateApiUrl,boundedJson} from "../deployment/private-http.js";
import {readToolApiConfig} from "../config/tool-api.config.js";
import {checkPeerIntegration} from "../deployment/peer-check.js";
try {
 const args=process.argv.slice(2);if(args.length!==2||args[0]!=="--url")throw new Error("Require private API URL");const base=privateApiUrl(args[1]!);
 const api=readToolApiConfig({SEARCH_API_ENABLED:"true",SEARCH_API_TOKEN:process.env.SEARCH_API_TOKEN});if(!api)throw new Error("Require caller token");
 const report=await checkPeerIntegration(async(path,body,authorized=true)=>{const r=await fetch(new URL(path,base),{method:body===undefined?"GET":"POST",redirect:"error",signal:AbortSignal.timeout(20000),headers:{...(authorized?{authorization:"Bearer "+api.token}:{}),...(body===undefined?{}:{"content-type":"application/json"})},...(body===undefined?{}:{body:JSON.stringify(body)})});return {http_status:r.status,body:await boundedJson(r)};});
 console.log(JSON.stringify(report));
}catch{console.error(JSON.stringify({status:"peer_integration_failed",message:"ตรวจ Tailscale URL token สินค้าที่เผยแพร่ และ draft probe ไม่พิมพ์ token คำตอบดิบ หรือรหัสฐานข้อมูล"}));process.exitCode=1;}
