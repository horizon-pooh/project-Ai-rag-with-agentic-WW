-- Independent metadata fixture; source values mirror shape, not the entire live catalog.
CREATE ROLE catalog_search_reader NOLOGIN;
CREATE ROLE search_api LOGIN;
CREATE ROLE anon NOLOGIN;
GRANT catalog_search_reader TO search_api;
CREATE TABLE categories(id text PRIMARY KEY,parent_id text,level smallint,name_th text,name_en text,aliases text[],description text);
CREATE TABLE attribute_definitions(id text PRIMARY KEY,code text,name_th text,aliases text[],data_type text,unit_code text,unit_label text);
CREATE TABLE category_attributes(category_id text,attribute_id text,is_required boolean,is_filterable boolean,min_value numeric,max_value numeric,display_order smallint,PRIMARY KEY(category_id,attribute_id));
CREATE TABLE unit_conversion_rules(from_unit text,to_unit text,dimension text,multiplier numeric,offset_value numeric,enabled boolean,rule_version text,updated_at timestamptz DEFAULT now(),PRIMARY KEY(from_unit,to_unit));
INSERT INTO categories VALUES
 ('TV0000',NULL,1,'ภาพและเสียง','Audio and video','{}',NULL),
 ('TV0100','TV0000',2,'โทรทัศน์','Televisions',ARRAY['ทีวี','TV'],NULL),
 ('TV0108','TV0100',3,'ทีวี OLED','OLED Television',ARRAY['OLED TV','ทีวีOLED'],NULL),
 ('AP0101',NULL,3,'เครื่องซักผ้า','Washing machine',ARRAY['เครื่องซักผ้า'],NULL);
INSERT INTO attribute_definitions VALUES
 ('AT0048','screen_size','ขนาดหน้าจอแนวทแยง',ARRAY['ขนาดหน้าจอ','screen size','ขนาด'],'number','inch','นิ้ว'),
 ('AT0006','width','ความกว้างสินค้า',ARRAY['ความกว้าง','ขนาด'],'number','cm','เซนติเมตร'),
 ('AT0010','package_width','ความกว้างบรรจุภัณฑ์',ARRAY['ความกว้างกล่อง'],'number','cm','เซนติเมตร'),
 ('AT0093','quantum_dot','ควอนตัมดอต',ARRAY['Quantum dot'],'boolean',NULL,NULL),
 ('AT0099','wash_capacity','ความจุซัก',ARRAY['ความจุ'],'number','kg','กิโลกรัม'),
 ('AT0100','power','กำลังไฟ',ARRAY['วัตต์'],'number','W','วัตต์');
INSERT INTO category_attributes VALUES
 ('TV0108','AT0048',true,true,0,NULL,1),('TV0108','AT0006',true,true,0,400,2),
 ('TV0108','AT0010',true,false,0,450,3),('TV0108','AT0093',false,true,NULL,NULL,4),
 ('AP0101','AT0099',true,true,3,25,1),('AP0101','AT0100',true,true,0,3000,2);
INSERT INTO unit_conversion_rules VALUES
 ('cm','mm','length',10,0,true,'units-v1',now()),('mm','cm','length',0.1,0,true,'units-v1',now()),
 ('inch','inch','length',1,0,true,'units-v1',now()),('นิ้ว','inch','length',1,0,true,'units-v1',now()),
 ('inch','cm','length',2.54,0,true,'units-v1',now()),('cm','inch','length',0.3937007874015748,0,true,'units-v1',now()),
 ('kg','g','mass',1000,0,true,'units-v1',now()),('g','kg','mass',0.001,0,true,'units-v1',now()),
 ('L','L','volume',1,0,true,'units-v1',now()),('cm','m','length',0.01,0,false,'units-v1',now()),
 ('°C','°F','temperature',1.8,32,true,'fixture-only',now());
CREATE VIEW category_search_catalog WITH(security_invoker=true) AS
 SELECT c.id category_id,c.parent_id,c.level,c.name_th,c.name_en,c.aliases,c.description,p.name_th parent_name_th
 FROM categories c LEFT JOIN categories p ON p.id=c.parent_id;
CREATE VIEW category_attribute_search_catalog WITH(security_invoker=true) AS
 SELECT ca.category_id,a.id attribute_id,a.code,a.name_th,a.aliases,a.data_type,a.unit_code,a.unit_label,
 ca.is_required,ca.is_filterable,ca.min_value,ca.max_value,ca.display_order
 FROM category_attributes ca JOIN attribute_definitions a ON a.id=ca.attribute_id;
GRANT USAGE ON SCHEMA public TO catalog_search_reader;
GRANT SELECT ON categories,attribute_definitions,category_attributes,unit_conversion_rules,
 category_search_catalog,category_attribute_search_catalog TO catalog_search_reader;
DO $$DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['categories','attribute_definitions','category_attributes','unit_conversion_rules'] LOOP
 EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',t);
 EXECUTE format('CREATE POLICY metadata_read ON %I FOR SELECT TO catalog_search_reader USING(true)',t);
 END LOOP;
END $$;
