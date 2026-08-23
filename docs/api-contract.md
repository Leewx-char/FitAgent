# HTTP API 契约

除 `POST /api/chat` 的 SSE 数据流外，所有 HTTP JSON 接口均使用同一个响应结构：

```json
{
  "code": 200,
  "messages": [],
  "data": {}
}
```

## 字段约定

- `code`：HTTP 状态码的镜像。它始终与实际 HTTP 状态码一致，不定义独立业务码。
- `messages`：面向用户或调用方的提示字符串数组。普通成功通常为空数组。
- `data`：成功时的业务对象、数组或 `null`；失败时固定为 `null`。

HTTP 状态码是唯一的成功或失败依据：读取或更新成功为 `200`，创建资源为 `201`，
请求错误为 `400` 或 `422`，认证失败为 `401`，资源不存在为 `404`，限流为 `429`，
服务错误为 `5xx`。不再定义额外的业务错误码。

## 示例

创建用户画像：

```json
{
  "code": 201,
  "messages": [],
  "data": {
    "id": 1,
    "user_id": 1,
    "height": 175
  }
}
```

参数校验失败（HTTP `422`）：

```json
{
  "code": 422,
  "messages": ["body.username 参数不合法"],
  "data": null
}
```

健康文档识别也使用同一结构。文件不合法返回 `400`，无法提取健康指标或加密 PDF 返回
`422`；具体原因通过 `messages` 说明。成功时 `data` 包含 `metrics` 和 `conflicts`。

## SSE 聊天

`POST /api/chat` 成功后返回 `text/event-stream`，事件保持流式结构：

```json
{"type": "text", "content": "..."}
{"type": "tool", "name": "..."}
{"type": "evidence", "items": [{"rank": 1, "evidence_id": "source#chunk", "source_id": "source", "snippet": "..."}]}
{"type": "error", "content": "..."}
```

`tool` 只表示用户可见的工具进度，不暴露模型内部推理；`evidence` 是实际命中的结构化 RAG 证据，与回答中的 `[证据:N]` 对应。正常结束时服务端额外发送 `data: [DONE]`。前端必须分别处理四类 JSON 事件，并在收到 `[DONE]` 或 `error` 后收敛加载状态。

在 SSE 开始前发生的参数、认证或会话错误，仍返回本文定义的普通 JSON 响应。

## 接口索引

本文档说明跨端必须遵守的公共契约；下表是当前已注册的 HTTP 入口，精确字段以 Pydantic OpenAPI schema 为准。

| 模块 | 方法与路径 | 用途 |
|---|---|---|
| 健康检查 | `GET /api/health`、`GET /api/health/rag` | 服务与当前 RAG alias 的可读性检查 |
| 认证 | `POST /api/auth/register`、`POST /api/auth/login`、`GET /api/auth/me` | 注册、登录和当前用户 |
| 会话/消息 | `GET/POST /api/sessions`、`PATCH/DELETE /api/sessions/{id}`、`GET /api/sessions/{id}/messages` | 会话生命周期与消息读取 |
| 聊天/轨迹 | `POST /api/chat`、`GET /api/sessions/{id}/agent-runs` | SSE 回答与无原文执行轨迹 |
| 画像/文档 | `GET/POST/PUT /api/profile`、`POST /api/upload/health-doc` | 健身画像与待确认健康指标提取 |
| 记忆 | `GET/POST /api/memory`、`PATCH/DELETE /api/memory/{id}` | 候选、确认、撤销与用户主动创建 |
| 训练计划 | `GET /api/training-plans/current`、`POST /api/training-plans/generate`、`POST /api/training-plans/{id}/feedback` | 读取、显式生成和幂等反馈 |
| Coros 数据 | `POST /api/fitness/sync`、`GET /api/fitness/daily`、`GET /api/fitness/sleep`、`GET /api/fitness/activities` | 用户主动同步及已落库数据读取 |

## 用户可控记忆

`GET /api/memory` 返回当前用户的记忆。聊天自动识别到的信息状态为 `proposed`，不会自动提供给 Agent；前端必须让用户调用 `PATCH /api/memory/{id}` 且 body 为 `{"status":"confirmed"}` 后才成为跨会话记忆。`DELETE /api/memory/{id}` 或 `PATCH` 为 `{"status":"revoked"}` 会撤销记忆。

```json
{
  "id": "32位ID",
  "fact_key": "injuries",
  "display_text": "需注意的不适/伤病：膝盖伤",
  "status": "proposed",
  "expires_at": "2026-09-16T10:00:00"
}
```

`POST /api/memory` 仅表示用户在页面主动保存，创建后即为 `confirmed`；它不接受模型写入。

## 自适应训练计划

`POST /api/training-plans/generate` body 可选 `{"week_start":"YYYY-MM-DD"}`，日期必须是周一。服务不会接受自由文本计划：模型必须返回 `WeeklyTrainingPlan` JSON，后端校验星期 1–7 完整覆盖、训练日不超过用户 `weekly_days`、动作强度不超过安全上限，以及 `evidence_ids` 都确实来自本次 RAG 检索。

该接口包含混合检索与一次结构化模型生成，前端单独使用 120 秒超时；普通读写接口仍保持较短超时。首次检索可能因加载 BM25 工件而更慢，客户端应展示生成中状态，不应将 15 秒网络超时误报为“档案或知识库不可用”。

成功后的 `data` 结构包含：

```json
{
  "id": "32位ID",
  "week_start": "2026-08-17",
  "version": 1,
  "status": "active",
  "plan": {"title": "...", "days": []},
  "safety": {"maximum_intensity": "低", "signals": [], "constraints": []},
  "feedbacks": []
}
```

`POST /api/training-plans/{id}/feedback` 的 body 是 `day_of_week`、`completed`、可选 `rpe`（1–10）、`pain_score`（0–10）和 `notes`。同一计划同一星期重复提交会更新原反馈，而不是创建重复记录。

## Coros 同步

`POST /api/fitness/sync` 是显式同步操作，未传日期时默认请求最近 7 天。它先调用社区 Provider Runner 刷新私有 SQLite 缓存，再通过只读 stdio MCP 将可用数据写入 MySQL；读取和聊天不会自动触发外部同步。

正常的空数组不是失败：例如用户未佩戴手表睡眠时，`sleep` 为空但同步仍是完整成功，`partial` 为 `false`。只有单一设备源（例如移动端睡眠接口）明确暂不可用时，已获取的日指标和活动记录仍会落库，成功响应会明确标记 `partial`：

```json
{
  "upserted": 11,
  "partial": true,
  "unavailable_sources": ["sleep"],
  "cached_source_counts": {"daily": 7, "sleep": 0, "activities": 4}
}
```

## 前端使用

Axios 保留完整 HTTP 响应，因此普通业务数据位于 `response.data.data`；错误提示位于
`error.response.data.messages`。页面不应依赖旧的 `message`、`detail` 或业务错误码。
