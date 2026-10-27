import {Pool,type PoolClient} from "pg";
import {embeddingWorkerPoolOptions,type EmbeddingWorkerConfig} from "../config/embedding-worker.config.js";
import {databaseErrorCode} from "./postgres.js";
export interface EmbeddingWorkerIdentity {
 database:string;database_user:string;superuser:boolean;bypass_rls:boolean;create_role:boolean;create_database:boolean;replication:boolean;unexpected_membership:boolean;reader_member:boolean;
 default_read_only:string;owns_catalog:boolean;can_write_sources:boolean;can_create_public:boolean;can_write_vectors:boolean;can_delete_vectors:boolean;can_update_keys:boolean;
}
export function assertEmbeddingWorkerIdentity(i:EmbeddingWorkerIdentity):void {
 if(i.database!=="supabase_db_product" || i.database_user!=="embedding_worker" || i.superuser || i.bypass_rls || i.create_role || i.create_database || i.replication || i.unexpected_membership ||
  !i.reader_member || i.default_read_only!=="off" || i.owns_catalog || i.can_write_sources || i.can_create_public || !i.can_write_vectors || i.can_delete_vectors || i.can_update_keys)
  throw new Error("Embedding worker identity does not satisfy scoped runtime requirements");
}
export async function readEmbeddingWorkerIdentity(client:Pick<PoolClient,"query">):Promise<EmbeddingWorkerIdentity> {
 const i=(await client.query<EmbeddingWorkerIdentity>(`
   SELECT current_database() AS database,current_user AS database_user,r.rolsuper AS superuser,r.rolbypassrls AS bypass_rls,
    r.rolcreaterole AS create_role,r.rolcreatedb AS create_database,r.rolreplication AS replication,
    EXISTS(SELECT 1 FROM pg_roles parent WHERE parent.oid<>r.oid AND parent.rolname<>'catalog_embedding_worker' AND pg_has_role(r.oid,parent.oid,'MEMBER')) AS unexpected_membership,pg_has_role(current_user,'catalog_embedding_worker','MEMBER') AS reader_member,
    current_setting('default_transaction_read_only') AS default_read_only,
    EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname IN ('public','dataset_admin') AND c.relkind IN ('r','p','v') AND pg_has_role(r.oid,c.relowner,'USAGE')) AS owns_catalog,
    EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname IN ('public','dataset_admin') AND c.relkind IN ('r','p','v') AND c.relname NOT IN ('product_embeddings','category_embeddings')
      AND (has_any_column_privilege(r.oid,c.oid,'INSERT,UPDATE') OR has_table_privilege(r.oid,c.oid,'DELETE,TRUNCATE,TRIGGER,REFERENCES'))) AS can_write_sources,
    has_schema_privilege(current_user,'public','CREATE') AS can_create_public,
    has_table_privilege(current_user,'public.product_embeddings','INSERT') AND has_table_privilege(current_user,'public.category_embeddings','INSERT')
      AND has_column_privilege(current_user,'public.product_embeddings','embedding','UPDATE') AND has_column_privilege(current_user,'public.category_embeddings','embedding','UPDATE') AS can_write_vectors,
    has_table_privilege(current_user,'public.product_embeddings','DELETE,TRUNCATE,TRIGGER,REFERENCES') OR has_table_privilege(current_user,'public.category_embeddings','DELETE,TRUNCATE,TRIGGER,REFERENCES') AS can_delete_vectors,
    has_column_privilege(current_user,'public.product_embeddings','product_id','UPDATE') OR has_column_privilege(current_user,'public.product_embeddings','model_id','UPDATE') OR
     has_column_privilege(current_user,'public.category_embeddings','category_id','UPDATE') OR has_column_privilege(current_user,'public.category_embeddings','model_id','UPDATE') AS can_update_keys
   FROM pg_roles r WHERE r.rolname=current_user`)).rows[0];
 if(!i)throw new Error("Embedding worker identity unavailable");return i;
}
export function createEmbeddingWorkerDatabase(config:EmbeddingWorkerConfig,onIdleError:(code:string)=>void=()=>{}) {
 const pool=new Pool(embeddingWorkerPoolOptions(config));pool.on("error",e=>onIdleError(databaseErrorCode(e)));let closing:Promise<void>|undefined;
 return {pool,async check():Promise<EmbeddingWorkerIdentity> {
  const i=await readEmbeddingWorkerIdentity(pool);assertEmbeddingWorkerIdentity(i);return i;
 },close(){return closing??=pool.end();}};
}
