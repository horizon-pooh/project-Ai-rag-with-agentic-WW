# ไฟล์ใน line_ (MVP) ย้ายไปไหน

| ไฟล์ใน `line_` | ไปที่ `line_prod` | หมายเหตุ |
|---|---|---|
| `server.js` (1,263 บรรทัด) | `src/server.js`, `src/app.js`, `src/routes/*`, `src/middleware/lineSignature.js`, `src/conversation/*`, `src/jobs/softnixWorker.js` | แตกเป็นหลายไฟล์ตามหน้าที่ |
| `config.js` | `src/config/index.js` | เพิ่มการหยุดทันทีเมื่อค่าขาด |
| `sessionStore.js` | `src/store/index.js`, `memoryStore.js` + `redisStore.js` ใหม่ | interface เดิม |
| `dedupe.js` | `src/pipeline/dedupe.js` | ย้ายที่เก็บไป Redis |
| `userQueue.js` | `src/pipeline/userQueue.js` | lock ย้ายไป Redis |
| `filter.js` | `src/pipeline/filter.js` | เพิ่ม keyword ของ Vendor 1 |
| `aiClient.js` | `src/ai/aiClient.js` | |
| `llmClient.js` | `src/ai/llmClient.js` | |
| `softnixClient.js` | `src/ai/softnixParser.js` | เปลี่ยนชื่อให้ตรงหน้าที่ |
| `lineClient.js` (1,053 บรรทัด) | `src/line/lineApi.js`, `src/line/messages/*` | แยกส่วนยิง API ออกจากส่วนประกอบข้อความ |
| `logger.js` | `src/observability/logger.js` | เพิ่ม `redact.js` (PDPA) |
| `turnMetrics.js` | `src/observability/metrics.js` | |
| `debugRoutes.js` | `dev/debugRoutes.js` | production ไม่โหลด |
| `test-stubs.js`, `test/helpers/*` | `dev/stubs/`, `test/` | |
| `test/unit`, `test/integration`, `test/scenarios`, `load-test.js` | `test/*` | ย้ายก่อนเขียนโค้ดใหม่ |
| `messageBatcher.js`, `clarifier.js`, `answerCache.js`, `tokenCounter.js`, `survey.js` | — | **ตัดจากเวอร์ชันแรก** (ดู `decisions.md` D-004) |
| `ioLog.js`, `traceLog.js`, `dashboard.html`, `test/chaos/` | — | **ตัดจากเวอร์ชันแรก** |
| — | `src/line/sendGuard.js`, `src/jobs/softnixQueue.js`, `src/notify/*`, `src/ops/*`, `src/store/redisStore.js`, `src/observability/redact.js`, `src/catalog/` | **ของใหม่** |
