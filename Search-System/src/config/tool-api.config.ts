export interface ToolApiConfig {token: string}
export function readToolApiConfig(values: NodeJS.ProcessEnv): ToolApiConfig | null {
  const enabled=values.SEARCH_API_ENABLED ?? "false";
  if(enabled!=="true" && enabled!=="false")throw new Error("SEARCH_API_ENABLED must be true or false");
  if(enabled==="false")return null;
  const token=values.SEARCH_API_TOKEN;
  if(!token || !/^[A-Za-z0-9_-]{43,128}$/.test(token))throw new Error("SEARCH_API_TOKEN must be a 43–128 character base64url token");
  return {token};
}
