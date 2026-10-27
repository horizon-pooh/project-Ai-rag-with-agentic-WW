-- Complements the same independent fixture chain used by keyword-search.test.ts.
ALTER TABLE products ADD COLUMN series text,ADD COLUMN regular_price numeric,
 ADD COLUMN currency text NOT NULL DEFAULT 'THB',ADD COLUMN sale_unit text NOT NULL DEFAULT 'ชิ้น',
 ADD COLUMN pack_quantity integer NOT NULL DEFAULT 1,ADD COLUMN stock_quantity integer NOT NULL DEFAULT 10;
ALTER TABLE product_features ADD COLUMN detail text NOT NULL DEFAULT 'การทำงานตามสเปก',
 ADD COLUMN benefit text NOT NULL DEFAULT 'ประโยชน์สำหรับการใช้งาน',
 ADD COLUMN limitation text NOT NULL DEFAULT 'ขึ้นกับอุปกรณ์ที่เชื่อมต่อ',ADD COLUMN display_order smallint NOT NULL DEFAULT 1;
DROP VIEW product_content_sections;
ALTER TABLE product_content_overrides ADD COLUMN title text NOT NULL DEFAULT 'หัวข้อ',
 ADD COLUMN applicability text NOT NULL DEFAULT 'applicable',ADD COLUMN items jsonb NOT NULL DEFAULT '[]',
 ADD COLUMN display_order smallint NOT NULL DEFAULT 1;
CREATE VIEW product_content_sections WITH(security_invoker=true) AS
 SELECT product_id,section_type,title,applicability,items,display_order FROM product_content_overrides;
INSERT INTO product_content_overrides(product_id,section_type,title,applicability,items,display_order) VALUES
 (100000,'description','รายละเอียดสินค้า','applicable','[{"text":"ทีวีทดสอบ"},{"bullets":["ภาพคมชัด","ต่ออุปกรณ์ HDMI"]}]',1),
 (100000,'installation','การประกอบ','not_applicable','[{"reason":"ไม่ต้องประกอบ"}]',2),
 (106045,'description','ข้อมูลฉบับร่าง','applicable','[{"text":"SECRET DRAFT CONTENT"}]',1);
UPDATE products SET series='Cinema',short_description='สำหรับดูหนัง',regular_price=22000 WHERE id=100000;
