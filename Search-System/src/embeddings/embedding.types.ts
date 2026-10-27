export type EmbeddingPurpose = "query" | "document";
export interface EmbeddingProfile {
  provider: "llama_cpp";
  model: string;
  dimensions: number;
  distanceMetric: "cosine";
  version: string;
  pooling: "last";
  normalization: "server_l2";
  contextTokens: number;
  runtimeVersion: string;
  artifact: string;
  query: {prefix: string};
  document: {prefix: string};
}
export interface EmbeddingBounds {
  maxBatchSize: number;
  maxInputBytes: number;
  maxBatchBytes: number;
  maxResponseBytes: number;
  maxAttempts: number;
  attemptTimeoutMs: number;
  totalTimeoutMs: number;
  baseDelayMs: number;
  maxDelayMs: number;
}
export interface EmbeddingResult {
  provider: "llama_cpp";
  model: string;
  dimensions: number;
  configurationHash: string;
  purpose: EmbeddingPurpose;
  vectors: number[][];
  usage: {promptTokens: number; totalTokens: number};
  attempts: number;
  tokenCounts: number[];
}
export type EmbeddingErrorCode = "invalid_configuration" | "invalid_input" | "input_too_long" | "model_mismatch" | "cancelled" | "timeout" |
  "deadline_exceeded" | "network_error" | "http_error" | "invalid_response" | "response_too_large" | "retry_after_exceeds_budget";
/** Safe diagnostics only: never retain provider messages, request text, credentials or an Error cause. */
export class EmbeddingError extends Error {
  constructor(readonly code:EmbeddingErrorCode,readonly attempts=0,readonly httpStatus:number|null=null) {
    super("Embedding request failed: "+code);this.name="EmbeddingError";
  }
}
