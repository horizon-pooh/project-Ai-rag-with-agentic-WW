# Metadata foundation — Commit 11

Uses existing `categories.aliases`, `attribute_definitions.aliases`,
`category_search_catalog`, `category_attribute_search_catalog` and
`unit_conversion_rules`. No metadata/product reseed and no new storage tables.

## SQL helpers

Migration `0002_metadata_helpers.sql` adds the private `catalog_search` schema.
All functions are SECURITY INVOKER with an empty search_path, qualified references,
and EXECUTE granted to catalog_search_reader only. Existing source RLS applies.

| Function | Purpose |
|---|---|
| normalize_label(text) | Lowercase and collapse whitespace in metadata labels |
| category_label_matches(text) | All exact normalized ID/name/alias candidates, ordered by ID |
| attribute_label_matches(category,text) | All matching ID/code/name/alias candidates in one category |
| convert_unit(numeric,text,text) | Explicit enabled from/to rule; preserves numeric precision |
| metadata_version() | SHA256 of catalog views and unit rule fields; excludes updated_at |
| metadata_audit() | Counts, alias absence, collisions and unit codes without enabled rules |

Exact lookup is a metadata building block. Category hierarchy expansion, fuzzy
ranking and selection belong to Commit 12. Attribute operator/type validation,
brand/feature resolution and conflict diagnostics belong to Commit 13.

## Read adapter

`createMetadataRepository(poolOrClient)` uses parameterized SQL, never product
loads. It exposes version, audit, label matches, scoped attributes and conversion.
Pass a transaction-bound client when consistent metadata across several reads is
needed. The check script uses a repeatable-read, read-only transaction.

`min_value`, `max_value`, `value_out` are decimal strings or null internally.
This preserves PostgreSQL numeric precision and is not permission to convert
strings received in the Tool request to numeric values. Input contract stays
strict. Null bounds mean unspecified, zero is a real bound.

Normalization applies to metadata labels only, not user numeric values, model
identity or unit symbols. Punctuation and underscores are retained. No Unicode
accent folding, Thai segmentation or typo search is claimed here.

## Units

Only direct stored from/to pairs are supported. Unit symbols are case-sensitive.
`missing_unit`, `unsupported_unit`, `disabled_rule` carry null value_out.
Unknown same-unit pairs are unsupported rather than assumed valid. A known
attribute's canonical unit/label can be handled with its metadata in Commit 13.
No transitive conversion, rounding or guessed kg/L rules.

Some existing inverse factors are finite approximations. Commit 13 must define
comparison precision/tolerance before applying equality to converted values.
No conversion result is currently used to filter products.

Live read-only inspection on 2026-10-22 found 2,003 categories, 1,247 attribute
 definitions, 13,807 category-attribute links and 136 enabled unit rules. Every
category and definition had a nonempty aliases array; this does not prove every
synonym is present or unambiguous. Units such as W and counts lack conversion
coverage. The audit reports uncovered codes; membership in a rule does not imply
all pair conversions exist. Collisions are reported, not picked automatically.

## Deployment / rollback

Do not edit applied migration 0001. Inspect the plan before applying 0002 with
migration credentials. Search runtime cannot apply migrations. Re-running up is
a no-op. Down drops only Commit 11 functions/schema, without CASCADE or deleting
source metadata; revert later dependent migrations first. Keep app code and SQL
migration versions aligned. No search HTTP endpoint is added by this commit.

Run `npm run db:check-metadata` after deployment. It checks role identity and
known TV/diagonal-screen examples, then prints metadata audit and conversions.
Failure is not proof of missing products: inspect schema drift/tunnel/access.
Do not expose this operational audit as a public endpoint or run its full hash
on every customer search; later commits can refresh bounded metadata caches
outside the request hot path.
