-- Extend Commit 11 fixture into a valid three-level hierarchy.
UPDATE categories SET parent_id='AP0100' WHERE id='AP0101';
INSERT INTO categories VALUES
 ('AP0000',NULL,1,'เครื่องใช้ไฟฟ้า','Appliances',ARRAY['เครื่องใช้ไฟฟ้า'],NULL),
 ('AP0100','AP0000',2,'เครื่องซักผ้าและอบผ้า','Laundry',ARRAY['Laundry'],NULL),
 ('AP0102','AP0100',3,'เครื่องอบผ้า','Tumble dryer',ARRAY['เครื่องอบผ้า'],NULL),
 ('TV0109','TV0100',3,'ทีวี QLED','QLED Television',ARRAY['QLED','QLED TV','ทีวีQLED'],NULL),
 ('TV0107','TV0100',3,'ทีวี NEO QLED','Neo QLED Television',ARRAY['NEO QLED'],NULL),
 ('TV0200','TV0000',2,'อุปกรณ์เสริมทีวี','TV Accessories',ARRAY['อุปกรณ์เสริมทีวี'],NULL),
 ('TV0201','TV0200',3,'ขาแขวนทีวี','TV wall mount',ARRAY['ขาแขวนทีวี'],NULL),
 ('TV0202','TV0200',3,'รีโมตทีวี','TV remote control',ARRAY['รีโมตทีวี'],NULL),
 ('FO0000',NULL,1,'อาหารและเครื่องดื่ม','Food and Beverages',ARRAY['อาหารและเครื่องดื่ม'],NULL),
 ('FO0100','FO0000',2,'อาหารและเครื่องดื่ม','Food and Beverages',ARRAY['อาหารและเครื่องดื่ม'],NULL),
 ('FO0101','FO0100',3,'น้ำดื่ม','Drinking water',ARRAY['น้ำดื่ม'],NULL),
 ('EM0000',NULL,1,'หมวดว่าง','Empty category',ARRAY['หมวดว่าง'],NULL);
UPDATE categories SET aliases=aliases||ARRAY['OLED','OLED TV','ทีวี OLED'] WHERE id='TV0108';
-- Ambiguous alias in unrelated scopes, intentionally retained for clarification.
UPDATE categories SET aliases=aliases||ARRAY['จอพิเศษ'] WHERE id IN ('TV0108','TV0109');
