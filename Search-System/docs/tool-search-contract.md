# Search Tool Contract v1 — Commit 9

เป็นสัญญาข้อมูล ไม่ใช่ API หรือระบบค้นหาที่เปิดใช้งานแล้ว ไม่มีการเชื่อมฐานใน commit นี้

## Request

- version เป็น "1"; ไม่ส่งจะใช้ "1". query default "", filters default {}, sort default relevance, page default 1, limit default 20
- query ยาวไม่เกิน 200 code points; semantic_query ไม่เกิน 2,000; limit 1–100; offset=(page-1)*limit ไม่เกิน 10,000
- ใช้ query หรือ semantic_query หรือ filter ที่มีค่า หรือ exact อย่างน้อยหนึ่งอย่าง ราคาเป็น null หมายถึงไม่มีขอบเขตนั้น ราคา 0 เป็นเงื่อนไขที่มีค่า
- price: min/max เป็น number หรือ null, ไม่ติดลบ, min<=max; currency THB เท่านั้น default THB เมื่อมี price
- brand_queries เป็น OR; feature_queries_all เป็น AND; specs เป็น AND ภายใน branch หมวด; แต่ละรายการไม่เกิน 20
- ตัวเลขรองรับ eq/gte/lte และ unit แยก ข้อความและ boolean รองรับ eq และไม่รับ unit ไม่มีการแปลง kg เป็น L หรือแปลงหน่วยใดใน Commit 9
- additionalProperties=false ทุก object ไม่รับ SQL, vectors, model settings, category_id/attribute_id จากผู้เรียก
- Article เป็น integer 100000–199999 ตามขอบเขต catalog; exact.model เทียบตรงไม่ fuzzy; การยืนยัน exact สองช่องตรงกันและขัดกับ filters หรือไม่ เป็นงานฐานใน commit หลัง
- โครงสร้าง schema แยก number/string/boolean และไม่ coerce ชนิด: eq value "55" ยังคงเป็นข้อความที่อาจเป็นรหัสสีได้ ไม่เดาว่าเป็นตัวเลขจากชื่อ attribute_query การตรวจว่า "55" ใช้กับสเปกจอชนิด number ไม่ได้ หรือ "true" ใช้กับสเปก boolean ไม่ได้ ต้องตรวจ metadata จริงใน Commit 13
- semantic_query เป็นความต้องการเชิงความหมาย ไม่ใช่เงื่อนไขบังคับ ห้ามตีความเป็นสเปกเพิ่มเอง หาก keyword-only รับ semantic-only ที่ค้นคำไม่ได้ต้องคืน needs_clarification ไม่ค้นทั้งฐานเงียบ ๆ (service ใน Commit 16)
- parser คืนสำเนาที่ใส่ defaults; ไม่แก้ input และไม่เปลี่ยน model/ข้อความ value/ตัวเลขทศนิยม

## Response

- status: ok, no_matches, needs_clarification, invalid_request, constraint_conflict, search_unavailable
- ok หมายถึงการค้นสำเร็จ หน้าที่เกินผลอาจว่างได้ แม้ total_matches>0; no_matches ใช้เมื่อยอด strict/alternative ทั้งหมดเป็น 0 จริง ห้ามใช้แทน DB error หรือ provider error
- total_matches/total_alternatives เป็นจำนวนทั้งกลุ่มก่อนแบ่งหน้า; ถ้ายังนับไม่ได้คืน null ไม่เอาจำนวนหน้ามาแทน returned_count คือจำนวนผลในหน้านี้
- matches_applied_filters หมายถึงผ่านข้อที่ตรวจได้ ไม่ได้ยืนยันทุกข้อใน request เมื่อ unapplied_filters ไม่ว่าง all_requested_filters_applied ต้องเป็น false
- applied_filters และ unapplied_filters ใช้ category_id แยกหมวด และ path เป็น JSON Pointer ชี้ข้อกำหนดจาก request; เก็บ index ของ specs เดิมไว้ แม้มี attribute_query ซ้ำเพื่อกำหนด gte/lte
- alternative ต้องมี unmet_filters หรือ unknown_filters อย่างน้อยหนึ่งข้อ unknown actual_value=null ไม่ใส่ 0/false แทนข้อมูลที่ไม่มี
- ผลตรงมาก่อน alternatives และ Article ไม่ซ้ำ สถานะจริง/สิทธิ์ published/หมวดและค่าสเปกตรงฐานต้องตรวจใน SQL/repository ภายหลัง validator นี้ยืนยันเฉพาะสัญญา ไม่ใช่รับรองความจริงในฐาน
- mock facts ต้องมี mock_notice; ห้ามคืน raw vectors/configuration/secrets
- semantic_available=false ต้องมี warning code semantic_unavailable; availability/coverage ที่คำนวณจากของจริงเป็นงานขั้น hybrid
- request_id สร้างฝั่ง server ใน Commit 16 ไม่รับจาก JSON ลูกค้าใน v1
- ตอนเชื่อม Fastify ต้องใช้ทั้ง schema และ parseToolSearchRequest สำหรับกฎข้าม field; ตรวจ validateToolSearchResponse ก่อน serialize เพราะ serializer ไม่ใช่ตัวรับรองกฎธุรกิจทั้งหมด ห้ามส่ง Ajv errors ที่มี input/rawข้อมูลลับออกไปโดยตรง

## Tests

node:test + tsx, Ajv 8.20.0, Fastify fixture route เฉพาะใน test ไม่มี route ค้นหาจริงเพิ่มใน app.ts

ตัวอย่าง docs/examples/tool-search-v1 เป็น fixtures สมมติเพื่ออธิบายรูปแบบ ไม่ใช่ผลค้นฐานหรือหลักฐาน mapping ของสินค้ารุ่นจริง