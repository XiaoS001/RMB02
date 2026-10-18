# API 文档 · RMB02

Base URL：`http://127.0.0.1:5102`
认证：Cookie Session（`POST /api/auth/login` 后用同一 client 请求）

---

## 一、健康检查

### `GET /healthz`

无需认证。只读配置快照（不泄漏密钥原文）。

```json
{"status": "ok", "service": "rmb02", "ai_key_present": false,
 "ai_base_url": "...", "ai_model": "DeepSeek-V4-Flash", "ai_timeout_ms": 30000}
```

---

## 二、认证

### `POST /api/auth/login`

```json
{"username": "eval", "password": "eval123456"}
```
响应 `200`：`{"username": "...", "role": "..."}` · 失败 `401`（不区分用户不存在/密码错）

### `POST /api/auth/logout` · `GET /api/auth/me`

- logout：清 session，返回 `{"status":"ok"}`
- me：返回 `{"username", "role", "permissions"}`（需登录）

---

## 三、靶子与契约

### `GET /api/targets` · `POST /api/targets`

- GET 需 `view_all`；返回靶子列表
- POST 需 `manage_targets`：`{"name","base_url","kind":"http|offline","contract_source"}` → 201

### `GET /api/targets/<id>/contracts` · `POST /api/targets/<id>/contracts`

- GET 需 `view_all`：返回契约（path/method/fields/source/confidence）
- POST 需 `extract_contract`：批量存契约 → `{"saved": n}`

### `POST /api/targets/<id>/generate-cases`

需 `generate_cases`。对靶子全部契约生成用例（正常/边界/异常/authz），
**先校验再入库** → `{"task_id", "generated", "valid", "invalid"}`

---

## 四、任务与统计

### `GET /api/tasks` · `GET /api/tasks/<id>/cases` · `GET /api/tasks/<id>/stats`

- tasks：任务列表（需 view_all）
- cases：某任务用例列表（含 status/invalid_reason/invalid_layer）
- stats：`{"generated","valid","invalid","by_layer","by_reason"}`（阶段 2 硬要求）

---

## 五、标注与一致率

### `POST /api/annotations`

需 `annotate`（标注员）。`{"model_output_id","verdict":"agree|doubt|reject","note"}` → 201
🔴 只写 Annotation 表，**不碰 Score**。

### `GET /api/annotations/<model_output_id>`

需 `view_all`。🔴 **annotator 只能看到自己标过的**（API 层按角色过滤）；evaluator 看全部。

### `GET /api/agreement-rate`

需 `view_all`。`{"n","agreed","rate","skipped_no_score"}` —— **n 只算有机器分的**。

---

## 六、结算

### `POST /api/settlements`

需 `manage_targets`（平台管理员/评测工程师）。`{"annotator_id","task_id","unit_price"}` → 201
🔴 幂等：同一 (annotator, task) 第二次 → 400「已结算」。金额 = 条数 × 单价。

---

## 七、看板

| 接口 | 需权限 | 返回 |
|---|---|---|
| `GET /api/dashboard/tasks` | view_all | 任务列表 + 通过率（带 n） |
| `GET /api/dashboard/tasks/<id>/dimensions` | view_all | 各维度平均分（n + N/A 不计） |
| `GET /api/dashboard/agreement` | view_all | 标注一致率 |
| `GET /api/dashboard/workload` | view_all | 每人工作量 + 应付金额 |

---

## 八、通用错误

```json
{"error": "人类可读的中文说明"}
```
未登录 `401` · 权限不足 `403` · 参数错 `400`。

---

*（RMB02 docs/API.md · 2026-10-18）*
