import {writeFile} from "node:fs/promises";
import {toolOpenApiDocument} from "../deployment/tool-openapi.js";
await writeFile("docs/tool-api.openapi.json",JSON.stringify(toolOpenApiDocument(),null,2)+"\n");
console.log(JSON.stringify({status:"openapi_exported",path:"docs/tool-api.openapi.json"}));
