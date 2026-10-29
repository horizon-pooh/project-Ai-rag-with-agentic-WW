import {isPrivateHost} from "./server-config.js";
export function privateApiUrl(raw:string) {
 const u=new URL(raw);if(u.protocol!=="http:"||!isPrivateHost(u.hostname)||u.pathname!=="/"||u.search||u.hash||u.username||u.password)throw new Error("Require private API URL");return u;
}
export async function boundedJson(response:Response,maxBytes=2*1024*1024):Promise<unknown> {
 const reader=response.body?.getReader();if(!reader)throw new Error("Empty response");let bytes=0;const chunks:Uint8Array[]=[];
 try{while(true){const p=await reader.read();if(p.done)break;bytes+=p.value.byteLength;if(bytes>maxBytes)throw new Error("Response too large");chunks.push(p.value);}}
 finally{await reader.cancel().catch(()=>{});}
 return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}
