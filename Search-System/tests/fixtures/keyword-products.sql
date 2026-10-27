-- Product fixture complements existing independent metadata fixtures and 0001 access migration.
CREATE TABLE public.products(id integer PRIMARY KEY,category_id text NOT NULL,category_level smallint DEFAULT 3,
 brand_id text NOT NULL,model text NOT NULL UNIQUE,name_th text NOT NULL,name_en text NOT NULL DEFAULT '',short_description text DEFAULT '',
 price numeric,status text NOT NULL,is_mock boolean NOT NULL DEFAULT true,mock_notice text NOT NULL DEFAULT 'ข้อมูลจำลองสำหรับการศึกษา');
CREATE TABLE public.product_attribute_values(product_id integer,category_id text,attribute_id text,data_type text,
 value_number numeric,value_text text,value_boolean boolean,PRIMARY KEY(product_id,attribute_id));
CREATE TABLE public.product_features(product_id integer,category_id text,feature_id text,PRIMARY KEY(product_id,feature_id));
CREATE TABLE public.product_search_documents(product_id integer PRIMARY KEY,keyword_text text NOT NULL,embedding_text text DEFAULT '',content_hash text DEFAULT '',template_version text DEFAULT 'fixture',updated_at timestamptz DEFAULT now());
CREATE TABLE public.product_content_overrides(product_id integer,section_type text,body text);
CREATE TABLE public.category_content_templates(category_id text,section_type text,body text);
CREATE VIEW public.product_content_sections WITH(security_invoker=true) AS
 SELECT product_id,section_type,body FROM public.product_content_overrides;
CREATE SCHEMA dataset_admin;
CREATE TABLE dataset_admin.import_runs(id integer);
CREATE TABLE public.embedding_models(id text);
CREATE TABLE public.product_embeddings(product_id integer);
CREATE TABLE public.category_embeddings(category_id text);
CREATE TABLE public.category_search_documents(category_id text);
DO $$DECLARE t text;BEGIN
 FOREACH t IN ARRAY ARRAY['products','product_attribute_values','product_features','product_search_documents','product_content_overrides','category_content_templates'] LOOP
  EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',t);
 END LOOP;
END $$;
CREATE FUNCTION public.search_products(p_query text DEFAULT '',p_category_id text DEFAULT NULL,
 p_brand_id text DEFAULT NULL,p_min_price numeric DEFAULT NULL,p_max_price numeric DEFAULT NULL,
 p_feature_ids text[] DEFAULT '{}',p_specs jsonb DEFAULT '[]',p_sort text DEFAULT 'relevance',p_limit integer DEFAULT 20,p_offset integer DEFAULT 0)
RETURNS TABLE(id integer) LANGUAGE sql STABLE SECURITY INVOKER AS
 'SELECT p.id FROM public.products p WHERE p.status=''published'' AND (p_query='''' OR p.model=p_query) ORDER BY p.id LIMIT p_limit OFFSET p_offset';
REVOKE ALL ON FUNCTION public.search_products(text,text,text,numeric,numeric,text[],jsonb,text,integer,integer) FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
-- Alternate category spec definitions are explicit, not inferred by search.
INSERT INTO category_attributes SELECT 'TV0109',attribute_id,is_required,is_filterable,min_value,max_value,display_order
 FROM category_attributes WHERE category_id='TV0108';
INSERT INTO category_features(category_id,feature_id) VALUES('TV0109','FT0023'),('TV0109','FT0074');
INSERT INTO attribute_definitions VALUES('AT0201','dry_capacity','ความจุอบ',ARRAY['ความจุอบ'],'number','kg','กิโลกรัม');
INSERT INTO category_attributes VALUES('AP0102','AT0201',true,true,1,20,1);
INSERT INTO products(id,category_id,brand_id,model,name_th,name_en,price,status) VALUES
 (100000,'TV0108','BR0002','TV0108-001','ทีวี OLED LG รุ่นหนึ่ง','OLED TV Cinema',20000,'published'),
 (100001,'TV0108','BR0002','TV0108-002','ทีวี OLED LG รุ่นใหญ่','OLED TV Large',30000,'published'),
 (100002,'TV0108','BR0001','TV0108-003','ทีวี OLED Bosch รุ่นโลหะ','OLED TV Steel',15000,'published'),
 (100003,'TV0108','BR0002','TV0108-004','ทีวี OLED LG ไม่ทราบขนาด','OLED TV Unknown',22000,'published'),
 (100004,'TV0108','BR0002','TV0108-005','ทีวี OLED LG ไม่มีความกว้าง','OLED TV Width unknown',23000,'published'),
 (100005,'TV0108','BR0002','TV0108-006','ทีวี OLED LG ควอนตัม','OLED TV Quantum',24000,'published'),
 (100006,'TV0108','BR0002','TV0108-007','ทีวี OLED LG รุ่นสมดุล','OLED TV Balanced',25000,'published'),
 (100007,'TV0108','BR0002','TV0108-008','ทีวี OLED LG ราคาศูนย์','OLED TV Zero',0,'published'),
 (100008,'TV0108','BR0002','TV0108-009','ทีวี OLED LG ไม่ทราบควอนตัม','OLED TV Boolean unknown',21000,'published'),
 (100009,'TV0108','BR0002','TV0108-010','ทีวี OLED LG ค่าขนาดผิด','OLED TV Invalid number',26000,'published'),
 (100010,'TV0108','BR0002','TV0108-011','ทีวี OLED LG ชนิดค่าขนาดผิด','OLED TV Invalid type',27000,'published'),
 (100011,'TV0109','BR0002','TV0109-001','ทีวี QLED LG','QLED TV',19000,'published'),
 (100012,'AP0101','BR0002','AP0101-001','เครื่องซักผ้า LG','Washing machine',15000,'published'),
 (100013,'AP0102','BR0002','AP0102-001','เครื่องอบผ้า LG','Tumble dryer',17000,'published'),
 (106045,'TV0108','BR0002','TV0108-900','ทีวี OLED SECRET-DRAFT','DRAFTONLYSECRET',1,'draft'),
 (106046,'TV0109','BR0002','TV0109-900','ทีวี QLED SECRET-DRAFT','DRAFTONLYSECRET',1,'draft');
INSERT INTO product_attribute_values
 SELECT p.id,p.category_id,'AT0048','number',CASE WHEN id=100001 THEN 65 WHEN id=100007 THEN 42 ELSE 55 END,NULL,NULL
 FROM products p WHERE p.category_id IN ('TV0108','TV0109') AND p.id NOT IN (100003,100009,100010);
INSERT INTO product_attribute_values
 SELECT p.id,p.category_id,'AT0006','number',CASE WHEN id=100001 THEN 110 WHEN id=100002 THEN 90 WHEN id=100007 THEN 80 ELSE 100 END,NULL,NULL
 FROM products p WHERE p.category_id IN ('TV0108','TV0109') AND p.id<>100004;
INSERT INTO product_attribute_values
 SELECT p.id,p.category_id,'AT0093','boolean',NULL,NULL,id IN (100005,100011)
 FROM products p WHERE p.category_id IN ('TV0108','TV0109') AND p.id<>100008;
INSERT INTO product_attribute_values
 SELECT p.id,p.category_id,'AT0014','text',NULL,CASE WHEN id=100002 THEN 'Steel' ELSE 'Wood' END,NULL
 FROM products p WHERE p.category_id IN ('TV0108','TV0109');
INSERT INTO product_attribute_values VALUES
 (100004,'TV0108','AT0006','number',NULL,NULL,NULL),
 (100008,'TV0108','AT0093','boolean',NULL,NULL,NULL),
 (100009,'TV0108','AT0048','number','NaN',NULL,NULL),
 (100010,'TV0108','AT0048','text',NULL,'55',NULL),
 (100012,'AP0101','AT0099','number',8,NULL,NULL),
 (100013,'AP0102','AT0201','number',6,NULL,NULL);
INSERT INTO product_features SELECT p.id,p.category_id,'FT0023' FROM products p
 WHERE p.category_id IN ('TV0108','TV0109') AND id NOT IN (100003,100007);
INSERT INTO product_features SELECT p.id,p.category_id,'FT0074' FROM products p
 WHERE p.id IN (100000,100005,100006,100011,106045,106046);
INSERT INTO product_search_documents(product_id,keyword_text)
 SELECT id,name_th||' '||name_en||' '||CASE WHEN id IN (100000,100006) THEN 'ดูหนังในห้องนั่งเล่น' ELSE '' END FROM products;
