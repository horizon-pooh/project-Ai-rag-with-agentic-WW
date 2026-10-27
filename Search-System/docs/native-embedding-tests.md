# Native storage tests (isolated PostgreSQL18.6 + pgvector0.8.2 only)

The default npm test suite is self-contained PGlite. Additional native verification uses the same storage test
file over pg.Client, with real vector0.8.2/crypto/trigram and native roles/RLS/migrations.
It must use a NEW DISPOSABLE PostgreSQL cluster, never the live BPLUTONIX catalog instance.
Fixtures create global test roles, catalog tables and model/vector samples. Do not grant a test administrator
on the production cluster merely to run these tests.

Install PostgreSQL18.6 and extensions pgvector0.8.2, pgcrypto and pg_trgm in the TEST installation.
Create a fresh private cluster with administrator catalog_test_admin and database catalog_embedding_test_c19.
The harness checks exact database/user/superuser/PostgreSQL version before fixture mutation. Use only loopback
or a /tmp or /private/tmp socket directory. Worker/source role names must not already exist in this cluster.
Tests do not automatically drop existing roles or a database to force rerun; recreate the disposable cluster
between native runs. Native tests are additional to npm test, not a substitute.

Example once the separate fresh cluster and extensions are installed:

```bash
EMBEDDING_STORAGE_TEST_HOST=/private/tmp/your-test-socket \
EMBEDDING_STORAGE_TEST_PORT=55439 \
npm run test:embedding-storage:native
```

A loopback test server may instead use host127.0.0.1 and a private
EMBEDDING_STORAGE_TEST_PASSWORD environment value for catalog_test_admin. Never put a password in Git or a
command transcript. The target database/user remain fixed to their isolated test names.
After testing, stop/remove that disposable cluster according to its test runbook.

Preparation evidence: native PostgreSQL18.6/vector0.8.2 passed54/54 tests; default full suite661/661 passed.
The prepared native instance used a private Unix socket with no TCP listener and was stopped afterwards.
These results do not mean migration0007 or worker bootstrap was applied to supabase_db_product.
