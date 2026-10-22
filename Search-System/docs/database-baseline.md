# PostgreSQL migration baseline — Commit 10

Database: supabase_db_product, PostgreSQL18.6 on BPLUTONIX. Mac development connects through SSH local15432; server deployment uses loopback5432.

Verified after import: 18,000 products (6,045 published,11,955 draft), Articles100000–117999,1,907 leaf categories,2,003 total categories,18,000 product search documents,234,000 logical content sections. Definitions/aliases/unit rules and category documents were already installed before server migration. This is an existing schema, not an empty database.

The owner antonius owns catalog tables and cannot CREATE ROLE. Administrator provisioning of catalog_search_reader/search_api is separate from schema migrations. Runtime search_api is not an owner/admin and is read-only by default. Owner credentials belong only in .env.migration.local, never .env or Git.

0001 only grants SELECT/EXECUTE and adds role-specific RLS policies. Metadata definitions are readable; products and related rows require published. Security-invoker content/metadata views keep underlying RLS. Restrictive published guards supplement permissive reader policies. Existing anon/authenticated policies, products, mock flags, templates, vectors and v1 search SQL are unchanged.

No dataset_admin schema access or raw embedding tables/documents/models are granted in this phase. Their scope is reviewed in later embedding commits. Rollback only removes the named policies and direct grants created by0001, never data/roles or old policies. Do not run down casually in production; it removes the new API's catalog access.

Migration metadata uses the private schema search_migrations with pgmigrations history and a separate checksums ledger. node-pg-migrate does not itself provide this source checksum guarantee. The wrapper pins each file's SHA256 before execution and rejects changed/missing pinned files, including rolled-back files. Use new numbered migrations for subsequent changes. Failed reserved files may need an administrator-reviewed ledger repair when they have never been applied; never delete history/checksums to force a rerun.

Only use npm db:migrate scripts, not a bare runner/CLI that bypasses checksum checks. Up migrations are atomic in node-pg-migrate's single transaction; checksum reservations persist separately for crash recovery. Plan is read-only and never calls the runner's dry-run mode, which may still initialize migration metadata. The wrapper serializes its own plan/write paths through a session lock during up/down.

Before production application: pass tests on the isolated PostgreSQL18 fixture, inspect the plan/diff, and retain the pre-import backup or take an updated backup. The existing schema remains the baseline; do not replay old initial migrations/imports.

After user-applied migration, run db:check and db:check-access with the real search_api login. Expected current visibility is6,045 published products,0 drafts and13 sections for Article100000; draft Article106045 and its children remain invisible. Do not expect18,000 visible products or publish drafts to make checks pass. Counts reflect this dataset snapshot, not permanent API constants.