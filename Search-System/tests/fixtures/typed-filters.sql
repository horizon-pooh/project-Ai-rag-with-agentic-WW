CREATE TABLE brands(id text PRIMARY KEY,name text,name_th text,aliases jsonb,is_real_brand boolean DEFAULT true);
CREATE TABLE features(id text PRIMARY KEY,code text,name_th text,name_en text,description text,aliases jsonb);
CREATE TABLE category_features(category_id text,category_level smallint DEFAULT 3,feature_id text,PRIMARY KEY(category_id,feature_id));
INSERT INTO brands(id,name,name_th,aliases) VALUES
 ('BR0001','Bosch','บ๊อช','["BOSCH"]'),('BR0002','LG','แอลจี','["LG"]'),
 ('BR0003','Example A','ตัวอย่างเอ','["แบรนด์ร่วม"]'),('BR0004','Example B','ตัวอย่างบี','["แบรนด์ร่วม"]');
INSERT INTO features VALUES
 ('FT0023','hdmi_input','รับสัญญาณ HDMI','HDMI input',NULL,'["HDMI"]'),
 ('FT0074','variable_refresh','ปรับรีเฟรชตามสัญญาณ','variable refresh',NULL,'["VRR","ฟีเจอร์ร่วม"]'),
 ('FT0075','example_feature','ฟีเจอร์ตัวอย่าง','example feature',NULL,'["ฟีเจอร์ร่วม"]'),
 ('FT0099','wash_mode','โหมดซัก','Wash mode',NULL,'["โหมดซัก"]');
INSERT INTO category_features(category_id,feature_id) VALUES
 ('TV0108','FT0023'),('TV0108','FT0074'),('TV0108','FT0075'),('AP0101','FT0099');
INSERT INTO attribute_definitions VALUES
 ('AT0014','main_material','วัสดุหลัก',ARRAY['วัสดุ'],'text',NULL,NULL),
 ('AT0200','door_count','จำนวนประตู',ARRAY['ประตู'],'number','door','ประตู');
INSERT INTO category_attributes VALUES
 ('TV0108','AT0014',true,true,NULL,NULL,5),('AP0101','AT0200',true,true,1,6,4);
GRANT SELECT ON brands,features,category_features TO catalog_search_reader;
DO $$DECLARE t text;BEGIN
 FOREACH t IN ARRAY ARRAY['brands','features','category_features'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY metadata_read ON %I FOR SELECT TO catalog_search_reader USING(true)',t);
 END LOOP;
END $$;
