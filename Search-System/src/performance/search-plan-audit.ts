import type {PoolClient} from "pg";
import {databaseErrorCode} from "../database/postgres.js";
type Client=Pick<PoolClient,"query">;
export interface PlanAuditInput {categoryId:string;articleId:number;attributeId:string}
export async function inspectSearchPlans(client:Client,input:PlanAuditInput={categoryId:"AP0101",articleId:100000,attributeId:"AT0017"}) {
 if(!/^[A-Z]{2}\d{4}$/.test(input.categoryId)||!/^AT\d{4}$/.test(input.attributeId)||!Number.isInteger(input.articleId)||input.articleId<100000||input.articleId>199999)throw new Error("Invalid plan probe");
 await client.query("BEGIN READ ONLY");
 try{
  await client.query("SELECT set_config('statement_timeout','3000',true),set_config('lock_timeout','500',true)");
  const identity=(await client.query(`SELECT current_user AS username,current_setting('server_version') AS version,current_setting('statement_timeout') timeout,
   r.rolsuper superuser,r.rolbypassrls bypass_rls,pg_has_role(current_user,'catalog_search_reader','MEMBER') reader_member FROM pg_roles r WHERE r.rolname=current_user`)).rows[0];
  if(!identity||identity.superuser||identity.bypass_rls||!identity.reader_member)throw new Error("Require scoped reader");
  const visibility=(await client.query(`SELECT count(*)::int AS visible,count(*) FILTER(WHERE status<>'published')::int AS drafts FROM public.products`)).rows[0];
  const privileges=(await client.query(`SELECT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname IN('public','dataset_admin') AND c.relkind IN('r','p') AND has_table_privilege(current_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES')) can_write,
   has_schema_privilege(current_user,'public','CREATE') can_create_public,
   EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname IN('public','dataset_admin') AND pg_has_role(current_user,c.relowner,'USAGE')) owns_catalog`)).rows[0];
  const draftChildren=[];
  for(const table of ["product_attribute_values","product_features","product_search_documents","product_content_overrides","product_content_sections"]){const count=(await client.query(`SELECT count(*)::int AS n FROM public.${table} WHERE product_id=$1`,[106045])).rows[0]?.n;draftChildren.push({table,visible:count});}
  const privateTables=["product_embeddings","category_embeddings","embedding_models","category_search_documents"];
  const privateAccess=[];
  for(const table of privateTables){await client.query("SAVEPOINT permission_probe");let denied=false;try{await client.query(`EXPLAIN SELECT * FROM public.${table}`);}catch(e){denied=databaseErrorCode(e)==="42501";}finally{await client.query("ROLLBACK TO SAVEPOINT permission_probe");await client.query("RELEASE SAVEPOINT permission_probe");}privateAccess.push({table,denied});}
  // EXPLAIN ไม่ใส่ ANALYZE จึงไม่รันคำสั่งเขียน แม้สิทธิ์ผิดก็ไม่แก้ข้อมูล
  const writeAccess=[];
  for(const sql of ["EXPLAIN UPDATE public.products SET name_th=name_th WHERE false","EXPLAIN DELETE FROM public.products WHERE false"]){await client.query("SAVEPOINT permission_probe");let denied=false;try{await client.query(sql);}catch(e){denied=databaseErrorCode(e)==="42501";}finally{await client.query("ROLLBACK TO SAVEPOINT permission_probe");await client.query("RELEASE SAVEPOINT permission_probe");}writeAccess.push(denied);}
  const indexes=(await client.query(`SELECT c.relname AS table_name,i.relname AS index_name,pg_get_indexdef(i.oid) AS definition,pg_relation_size(i.oid)::text AS bytes,
   x.indisvalid AS valid,x.indisready AS ready FROM pg_index x JOIN pg_class i ON i.oid=x.indexrelid JOIN pg_class c ON c.oid=x.indrelid JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='public' AND c.relname IN('products','product_attribute_values','product_features','product_search_documents','categories','category_attributes') ORDER BY c.relname,i.relname`)).rows;
  const request=JSON.stringify({version:"1",query:"",filters:{category_ids:[input.categoryId]},page:1,limit:5,sort:"relevance"});
  const probes:Array<{id:string;visibility:string;sql:string;params:unknown[]}>=[
   {id:"keyword_function_wrapper",visibility:"function_wrapper_only",sql:"SELECT catalog_search.search_products_keyword($1::text[],$2::jsonb)",params:[[input.categoryId],request]},
   {id:"published_category_scope",visibility:"underlying_read_probe",sql:"SELECT p.id FROM public.products p WHERE p.status='published' AND p.category_id=$1 ORDER BY p.id",params:[input.categoryId]},
   {id:"batch_attribute_read",visibility:"underlying_read_probe",sql:"SELECT v.product_id,v.attribute_id,v.value_number FROM public.product_attribute_values v JOIN public.products p ON p.id=v.product_id AND p.category_id=v.category_id WHERE p.status='published' AND p.category_id=$1 AND v.attribute_id=$2",params:[input.categoryId,input.attributeId]},
   {id:"batch_feature_read",visibility:"underlying_read_probe",sql:"SELECT pf.product_id,pf.feature_id FROM public.product_features pf JOIN public.products p ON p.id=pf.product_id AND p.category_id=pf.category_id WHERE p.status='published' AND p.category_id=$1",params:[input.categoryId]},
   {id:"facts_by_article",visibility:"underlying_read_probe",sql:"SELECT p.id,p.model,p.price FROM public.products p WHERE p.status='published' AND p.id=$1",params:[input.articleId]},
   {id:"keyword_document_join",visibility:"underlying_read_probe",sql:"SELECT p.id,d.product_id FROM public.products p LEFT JOIN public.product_search_documents d ON d.product_id=p.id WHERE p.status='published' AND p.category_id=$1",params:[input.categoryId]},
  ];
  const plans=[];
  for(const p of probes){await client.query("SAVEPOINT plan_probe");try{const plan=(await client.query("EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON,TIMING OFF) "+p.sql,p.params)).rows[0]?.["QUERY PLAN"];plans.push({id:p.id,visibility:p.visibility,status:"ok",plan});}catch(e){plans.push({id:p.id,visibility:p.visibility,status:"failed",code:databaseErrorCode(e)});}finally{await client.query("ROLLBACK TO SAVEPOINT plan_probe");await client.query("RELEASE SAVEPOINT plan_probe");}}
  await client.query("SAVEPOINT timeout_probe");let timeoutCode="";try{await client.query("SELECT set_config('statement_timeout','25',true)");await client.query("SELECT pg_sleep(0.1)");}catch(e){timeoutCode=databaseErrorCode(e);}finally{await client.query("ROLLBACK TO SAVEPOINT timeout_probe");await client.query("RELEASE SAVEPOINT timeout_probe");}
  const recovered=(await client.query("SELECT 1 AS ok")).rows[0]?.ok===1;
  const accessPassed=visibility?.drafts===0&&!privileges?.can_write&&!privileges?.can_create_public&&!privileges?.owns_catalog&&privateAccess.every(p=>p.denied)&&writeAccess.every(Boolean)&&draftChildren.every(p=>p.visible===0);
  return {status:accessPassed&&recovered&&timeoutCode==="57014"&&plans.every(p=>p.status==="ok")&&indexes.every(i=>i.valid&&i.ready)?"passed":"review_required",identity,visibility,privileges,private_tables:privateAccess,draft_probe_article:106045,draft_children:draftChildren,
   write_probes_denied:writeAccess.every(Boolean),statement_timeout:{code:timeoutCode,recovered},indexes,plans,
   plan_limit:"PL/pgSQL function wrapper ไม่แสดงแผนภายในทั้งหมด; underlying probes เป็นคำสั่งอ่านประกอบ ไม่แทนแผนเต็มของ search function",production_writes:0};
 }finally{await client.query("ROLLBACK");}
}
