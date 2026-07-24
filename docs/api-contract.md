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
{"type": "error", "content": "..."}
```

在 SSE 开始前发生的参数、认证或会话错误，仍返回本文定义的普通 JSON 响应。

## 前端使用

Axios 保留完整 HTTP 响应，因此普通业务数据位于 `response.data.data`；错误提示位于
`error.response.data.messages`。页面不应依赖旧的 `message`、`detail` 或业务错误码。
