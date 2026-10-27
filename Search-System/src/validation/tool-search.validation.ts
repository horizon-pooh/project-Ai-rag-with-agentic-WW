import { Ajv, type ErrorObject, type ValidateFunction } from "ajv";
import { searchRequestSchema } from "../schemas/search-request.schema.js";
import { searchResponseSchema } from "../schemas/search-response.schema.js";
import type { ContractIssue, NormalizedToolSearchRequest, ToolSearchRequest, ToolSearchResponse, ValidationResult } from "../types/tool-search.types.js";

/** Reuse these options when wiring Fastify in Commit 17. Never mutate caller JSON. */
export const TOOL_AJV_OPTIONS = {
    strict: true,
    allErrors: true,
    coerceTypes: false,
    removeAdditional: false,
    useDefaults: false,
    ownProperties: true,
} as const;
const ajv = new Ajv(TOOL_AJV_OPTIONS);
const requestValidator: ValidateFunction<ToolSearchRequest> = ajv.compile(searchRequestSchema);
const responseValidator: ValidateFunction<ToolSearchResponse> = ajv.compile(searchResponseSchema);

function schemaIssues(errors: ErrorObject[] | null | undefined): ContractIssue[] {
    return (errors ?? []).map(error => {
        const property = error.params.missingProperty ?? error.params.additionalProperty;
        const suffix = typeof property === "string" ? "/" + property.replace(/~/g, "~0").replace(/\//g, "~1") : "";
        return {
            path: (error.instancePath + suffix) || "/", code: error.keyword,
            message: error.message ?? "Invalid value"
        };
    });
}
const issue = (path: string, code: string, message: string): ContractIssue => ({ path, code, message });

export function parseToolSearchRequest(input: unknown): ValidationResult<NormalizedToolSearchRequest> {
    if (!requestValidator(input)) return { valid: false, issues: schemaIssues(requestValidator.errors) };
    // Validate first; defaults are applied to a deep copy, never to input.
    const request: NormalizedToolSearchRequest = {
        ...structuredClone(input),
        version: input.version ?? "1",
        query: input.query ?? "",
        filters: structuredClone(input.filters ?? {}),
        sort: input.sort ?? "relevance",
        page: input.page ?? 1,
        limit: input.limit ?? 20,
    };
    const issues: ContractIssue[] = [];
    const price = request.filters.price;
    if (price?.min != null && price.max != null && price.min > price.max) {
        issues.push(issue("/filters/price", "invalid_range", "Price min must not exceed max"));
    }
    if (price) price.currency ??= "THB";
    const filters = request.filters;
    const hasConstraint = Boolean(filters.category_query || filters.category_ids?.length || filters.brand_queries?.length ||
        filters.feature_queries_all?.length || filters.feature_ids_all?.length || filters.specs?.length ||
        price?.min != null || price?.max != null || request.exact);
    if (!request.query.trim() && !request.semantic_query?.trim() && !hasConstraint) {
        issues.push(issue("/", "empty_search", "Supply a query, semantic query, exact selector or filter"));
    }
    if ((request.page - 1) * request.limit > 10000) {
        issues.push(issue("/page", "offset_exceeded", "Pagination offset must not exceed 10000"));
    }
    return issues.length ? { valid: false, issues } : { valid: true, value: request };
}

export function validateToolSearchResponse(input: unknown): ValidationResult<ToolSearchResponse> {
    if (!responseValidator(input)) return { valid: false, issues: schemaIssues(responseValidator.errors) };
    const issues: ContractIssue[] = [];
    if (input.status === "ok" || input.status === "no_matches") {
        const { results, meta } = input;
        if (meta.returned_count !== results.length || results.length > meta.limit) {
            issues.push(issue("/meta/returned_count", "count_mismatch", "Returned count must equal results length within limit"));
        }
        if ((meta.page - 1) * meta.limit > 10000) {
            issues.push(issue("/meta/page", "offset_exceeded", "Pagination offset must not exceed 10000"));
        }
        if (meta.all_requested_filters_applied !== (input.unapplied_filters.length === 0)) {
            issues.push(issue("/meta/all_requested_filters_applied", "diagnostic_mismatch", "Flag must agree with unapplied filters"));
        }
        if (!input.semantic_available && !input.warnings.some(w => w.code === "semantic_unavailable")) {
            issues.push(issue("/warnings", "missing_warning", "Keyword-only responses must explain semantic unavailability"));
        }
        if (input.status === "no_matches" && (meta.total_matches !== 0 || meta.total_alternatives !== 0 || meta.has_more)) {
            issues.push(issue("/meta", "invalid_no_matches", "No-matches requires known zero totals and has_more=false"));
        }
        if (input.status === "ok" && meta.total_matches === 0 && meta.total_alternatives === 0) {
            issues.push(issue("/status", "invalid_status", "Known empty search must use no_matches"));
        }
        if (!results.length && meta.has_more) {
            issues.push(issue("/meta/has_more", "invalid_page", "An empty page cannot claim later results"));
        }
        const ids = new Set<number>();
        let sawAlternative = false;
        results.forEach((row, index) => {
            const path = `/results/${index}`;
            if (ids.has(row.product.article_id)) issues.push(issue(path, "duplicate_article", "Duplicate Article"));
            ids.add(row.product.article_id);
            const hasDifference = row.unmet_filters.length + row.unknown_filters.length > 0;
            if (row.match_type === "alternative") {
                sawAlternative = true;
                if (!hasDifference) issues.push(issue(path, "unexplained_alternative", "Alternatives require unmet or unknown filters"));
            } else if (hasDifference || sawAlternative) {
                issues.push(issue(path, "invalid_strict_match", "Strict results must precede alternatives and have no unmet/unknown filters"));
            }
            if (row.unknown_filters.some(f => f.actual_value !== null)) {
                issues.push(issue(path, "invalid_unknown_value", "Unknown values must be null, never zero or false"));
            }
            if (row.product.is_mock && !row.product.mock_notice?.trim()) {
                issues.push(issue(path, "missing_mock_notice", "Mock facts require a visible notice"));
            }
        });
        const strictCount = results.filter(r => r.match_type === "matches_applied_filters").length;
        if ((meta.total_matches !== null && meta.total_matches < strictCount) ||
            (meta.total_alternatives !== null && meta.total_alternatives < results.length - strictCount)) {
            issues.push(issue("/meta", "invalid_total", "Totals cannot be less than returned tier counts"));
        }
    }
    return issues.length ? { valid: false, issues } : { valid: true, value: input };
}
