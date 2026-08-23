# FitAgent 文档同步约定

本文定义项目文档与飞书知识库的同步边界，避免代码、Markdown 与在线导读出现不同口径。

## 事实来源与对应关系

| 主题 | 事实来源 | 本地文档 | 飞书知识库目标 |
| --- | --- | --- | --- |
| 系统边界、记忆、训练计划、Coros、健康文档 | `app/`、`frontend/src/` 与测试 | `architecture.md` | 项目总览、核心技术链路、端到端阅读路线、原始架构说明 |
| HTTP 与 SSE 字段 | Router、Pydantic schema 与前端消费代码 | `api-contract.md` | 原始 HTTP API 契约、前后端文件索引、端到端阅读路线 |
| 阅读顺序与文件职责 | 当前文件结构和调用关系 | `learning-guide.md` | 前端/后端文件索引、端到端阅读路线、原始代码学习路线 |

代码与测试优先于文档；发生冲突时先按代码修正本地 Markdown，再用飞书文档的局部更新同步对应段落。`docs/feishu-kb-source/` 是迁移时的静态导出，不是实时事实来源。

## 必检口径

- 近期上下文：最近 10 轮，即 20 条 `messages`；常量为 `RECENT_MESSAGE_LIMIT`。
- SSE：`text`、`tool`、`evidence`、`error` 四类 JSON 事件；正常结束追加 `[DONE]`。
- 前端职责：`Sidebar.vue` 管理会话；`Chat.vue` 管理 SSE 消费与健康文档确认；`Onboarding.vue` 只管理基础画像表单。
- 健康文档：上传和指标提取不等于写入；用户确认且冲突已解决后，才更新 `Profile.health_data`。

## 每次架构变更后的同步步骤

1. 修改代码和相关测试后，更新上述本地权威 Markdown。
2. 在飞书中读取目标章节，按 block 局部更新文字、表格和 Mermaid 图，不覆盖整篇或既有资源。
3. 重新读取线上变更段，核对术语、事件类型、文件职责与图表是否一致。
4. 如果迁移导出需要重新生成，以新的线上版本替换静态导出，并记录对应文档 revision。

## 最新同步记录

2026-08-22 已按当前代码完成一次双向核对和线上修复：

- 导读页：项目总览 revision 5、后端索引 revision 4、前端索引 revision 4、核心技术链路 revision 4、端到端阅读路线 revision 6。
- 原始资料：架构说明 revision 4、代码学习路线 revision 6；HTTP API 契约已在 revision 6 保持正确。
- 本次统一了 10 轮/20 条上下文、四类 SSE 事件与 `[DONE]`，校正了前端页面职责，并增加健康文档的端到端图解。
