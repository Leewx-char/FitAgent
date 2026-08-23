# FitAgent 项目决策记录

## 一、选题变更

| 原选题 | 新选题 |
|--------|--------|
| 智扫通（智能客服） | FitAgent（可演进的 RAG 运动教练） |
| 单 Agent + RAG + 基础问答 | 证据化 RAG + 按需 Agent 工具调用 + 用户画像 + 多模态文档 |

**变更理由**：运动教练方向便于展示 RAG、个性化数据和工具调用。工程深度来自可追溯的索引发布、混合检索和可观测性，而非无触发条件的多 Agent 编排。

---

## 二、关键架构决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 认证方案 | JWT Bearer Token | 前后端分离标准方案 |
| 密码存储 | bcrypt 4.0.1 | 5.0+ 与 passlib 不兼容 |
| 数据存储 | 核心字段用数据库列 + 动态字段用 JSON | 平衡查询性能与扩展性 |
| 注册与问卷 | 分离：注册只管账号，问卷用 POST /api/profile | 降低注册门槛 |
| SSE 流式 | 哨兵值替代 StopIteration | 解决 run_in_executor 异常 |
| Agent 上下文 | ContextVar + runtime.context 桥接 | LangGraph 工具调用上下文隔离 |
| RAG 检索 | 向量 + BM25 双路检索 + RRF 融合 | 语义理解 + 关键词精确，互补验证 |
| BM25 分词 | 中文拆字、英文/数字保留整体 | 零额外依赖，IDF 自动给稀有字高权重 |
| 项目结构 | FastAPI 标准分层（app/core/api/services） | 职责单一，目录命名符合社区惯例 |
| 健康数据 | health_data TEXT 列存十项固定指标 JSON | 健身场景字段稳定，契约和前端展示更简单 |
| VL 调用 | ChatTongyi (qwen-vl-plus) | 通义千问 VL 中文识别最佳 |
| PDF 处理 | 文字≥200字走 LLM，<200走 VL | 平衡速度与准确性 |
| 前端框架 | Naive UI（非 Element Plus） | 配色 #42A5F5，清新极简风 |
| 长期记忆 | 候选—用户确认—按需读取，不做聊天全文向量化 | 健康偏好和伤病信息错误长期化的代价高；保留来源、撤销、替换和过期能力 |
| 长会话上下文 | 最近 10 轮（20 条）原文 + 可重建确定性摘要 | 控制 token 成本，同时不删除审计原文，也不把摘要伪装成长记忆 |
| 周计划生成 | 结构化 JSON + 确定性安全策略 + RAG 证据 | 不能只靠 prompt 限制模型强度；把可检验约束放在 Pydantic 和服务层 |
| Coros 活动幂等 | `(user_id, data_type, external_id)` | 旧的日期级唯一键会覆盖同一天多次活动 |
| Coros MCP 传输 | 社区 `cygnusb/coros-mcp` 的本地 stdio 串行适配器 | 与现有读取工具契约一致；固定提交并隔离至 `.tools`，不让 FastMCP 传递依赖污染 FastAPI 服务 |
| Coros MCP 权限 | 强制 `readonly` + 隐藏认证工具 | FitAgent 仅同步设备数据；不把写计划、认证或令牌能力暴露给模型工具调用 |
| Coros 缓存隔离 | Provider Runner 在 import 前重定向社区 SQLite cache | 避免 Windows 用户目录的 `.config/coros-mcp` 文件/目录冲突；不篡改令牌依赖的用户 profile |
| Coros 部分同步 | 可用源落库 + `partial` 契约 | 移动端睡眠接口暂不可用时，不因单一源失败回滚日指标/活动；正常的睡眠空数组仍是完整成功 |

---

## 三、开发进度

### 阶段1：基础改造 ✅

- UserProfile 模型 + Schema + API（GET/POST/PUT）
- 注册流程改造（注册与问卷分离）
- Agent 提示词重写（客服 → 运动教练）
- 知识库替换与基础问答整理（该阶段为 Qdrant 重构前的历史实现）
- RAG 同义词/归一化/停用词配置化

### 阶段2：RAG 优化 ✅

- 参数调优 + 去掉中间 LLM 调用（2-4秒提速）
- 同义词映射迁移到 config/synonyms.yml
- source 可选过滤 + Jaccard 去重（阈值0.8）

### 阶段3：多模态健康文档上传 ✅

- 后端：upload_router + doc_parser + VL/LLM 双路线 + 加密PDF检测 + health_data 存储
- 前端：Chat.vue 上传按钮 + 确认面板 + Profile.vue 健康数据展示
- 提示词与上传接口：统一使用 `{code, messages, data}`，仅保留十个健身核心指标和单位
- 去重：身高/体重从体检报告回填到基本信息，不在健康数据区块重复展示
- VL模型 response.content list→str 修复
- 上传按钮 loading 状态替代 title 悬浮提示

### 阶段4：安全加固 + 联调测试 ✅

- 端到端测试（图片/PDF/加密PDF上传流程）
- 上传接口速率限制
- 提取结果后端校验（防恶意 JSON 注入）
- 前端思维链可视化（工具调用过程实时展示）

### 阶段5：可演进 RAG 最小闭环 ✅

- 项目结构重组：按 FastAPI 标准分层（`app/core/` / `app/api/` / `app/services/` / `app/utils/`）
- 中文知识源的 Markdown 标题感知切分、父子关联、内容去重与来源可追溯
- Qdrant revision collection + `rag_active` alias：离线校验后激活，在线只读
- Dense + BM25 双路检索、RRF 融合、元数据增强与可插拔重排序
- 通用知识问题的直接 RAG 路径、SSE 证据卡片、启动期 BM25 预热
- Agent 和 HTTP 访问 MySQL 的统一事务边界

### 阶段6：用户可控个性化闭环 ✅

- 会话记忆从“最近 20 轮全文截断”改为“最近 10 轮（20 条）原文 + `session_summaries` 短期状态”；状态仅从用户消息确定性提取，assistant 输出不可信。
- 新增 `memory_facts`：聊天产生待确认候选，用户确认后才供 Agent 的只读工具使用；支持来源消息、替换关系、撤销和过期。
- 新增训练计划页与 `training_plans` / `training_feedbacks`：显式生成、结构化周计划、单日反馈幂等更新。
- 新增 `TrainingSafetyPolicy`：伤病、训练负荷比、睡眠、疲劳和疼痛/RPE 触发强度降级；计划还校验 7 天覆盖、训练天数、动作强度和证据 ID。
- 运动数据唯一键迁移到 external id；同日多个 Coros 活动不再互相覆盖。
- Coros 本地 MCP 客户端重写为串行 JSON-RPC、Windows 可用超时和失败后重建进程；启动命令纳入环境配置。
- 社区 `cygnusb/coros-mcp` 固定到 `71d594c`，安装策略为隔离 `.tools` 虚拟环境；认证改为用户主动 CLI 操作，后端强制只读工具集。

### 后续演进原则

- 先建立中文检索评测集，再针对召回、排序或生成中的具体短板演进；
- 多 Agent、自动写画像均需要实际任务量、评测或安全机制作为触发证据；会话摘要已以可重建、用户消息限定的最小形式落地；
- 备份恢复、集群和定时清理在进入多环境或出现明确 RPO/RTO、数据增长要求后再实施。

详见 [04 - Agent 编排与工具调用](./refactoring/04-llm-agent-conversation.md) 与 [05 - 数据治理与运行边界](./refactoring/05-data-governance.md)。

### 远期扩展（生态扩展，1-3个月）

- 多设备接入：支持 Garmin、Apple Watch、Strava 等数据源
- 运动姿势多模态反馈：上传训练视频/照片，VL 模型分析动作姿势
- 社交功能：群组挑战、训练分享、排行榜
- 营养追踪：饮食记录、卡路里计算、蛋白质摄入建议
- PWA / 移动端：打包为 PWA 支持离线使用，推送训练提醒

---

## 四、数据模型

### UserProfile 混合存储

```python
# 核心字段 → 数据库列（支持查询/计算）
gender, age, height, weight, goal, weekly_days, experience

# 动态字段 → TEXT 列存 JSON 字符串
injuries          # ["膝盖", "腰椎"]
diet_restrict     # ["素食", "低碳"]
preferences       # {"preferred_time": "早上", "gym": true}
health_data       # 从文档提取的健康指标（10个固定字段）
```

### health_data 字段设计

固定10个健身核心字段（提示词定义，前端映射，中间层透传）：

| 字段 | 中文 | 单位 | 健身关联 |
|------|------|------|---------|
| height_cm | 身高 | cm | → 回填基本信息 height |
| weight_kg | 体重 | kg | → 回填基本信息 weight |
| bmi | BMI | - | 减脂/增肌核心指标 |
| body_fat | 体脂率 | % | 比BMI更准确 |
| heart_rate | 心率 | bpm | 有氧强度区间 |
| blood_pressure | 血压 | - | 运动安全红线 |
| blood_sugar | 血糖 | mmol/L | 低血糖风险 |
| cholesterol | 胆固醇 | mmol/L | 心血管风险 |
| alt | 谷丙转氨酶 | U/L | 肝功能→蛋白摄入 |
| uric_acid | 尿酸 | μmol/L | 痛风→动作选择 |

不在上述字段中的发现不写入画像；页面冲突以候选值和页码返回，由用户确认后保存。

---

## 五、API 接口

| 方法 | 路径 | 说明 | 认证 |
|------|------|------|------|
| POST | `/api/auth/register` | 注册 | 否 |
| POST | `/api/auth/login` | 登录 | 否 |
| GET | `/api/auth/me` | 当前用户 | 是 |
| POST | `/api/profile` | 创建画像 | 是 |
| GET | `/api/profile` | 查询画像 | 是 |
| PUT | `/api/profile` | 更新画像 | 是 |
| POST | `/api/sessions` | 创建会话 | 是 |
| GET | `/api/sessions` | 会话列表 | 是 |
| DELETE | `/api/sessions/:id` | 删除会话 | 是 |
| GET | `/api/sessions/:id/messages` | 会话消息 | 是 |
| POST | `/api/chat` | SSE 流式聊天 | 是 |
| POST | `/api/upload/health-doc` | 上传健康文档 | 是 |
| GET | `/api/memory` | 查询用户记忆，默认隐藏撤销项 | 是 |
| POST | `/api/memory` | 用户主动新增已确认记忆 | 是 |
| PATCH/DELETE | `/api/memory/:id` | 确认或撤销记忆 | 是 |
| GET | `/api/training-plans/current` | 查询当前周生效计划 | 是 |
| POST | `/api/training-plans/generate` | 生成受安全策略约束的周计划 | 是 |
| POST | `/api/training-plans/:id/feedback` | 写入或更新某日执行反馈 | 是 |
| POST | `/api/fitness/sync` | 主动同步 Coros 数据 | 是 |

---

## 六、关键知识点备忘

- **bcrypt**: 必须是 4.0.1，5.0+ 与 passlib 不兼容
- **ContextVar**: run_in_executor 自动复制，LangGraph 工具调用隔离需 runtime.context 桥接
- **SSE**: POST /api/chat，前端 fetch + ReadableStream 逐行解析 data: 前缀
- **ProfileUpdate**: model_dump(exclude_unset=True) 只更新传了的字段
- **health_data**: 十项固定字段由 `HealthDataSchema` 约束；模型和上传接口均返回 `{code, messages, data}`
- **VL模型**: ChatTongyi response.content 可能是 list，需 isinstance(content, list) 转 str
- **MySQL TEXT**: 不支持 DEFAULT 值，由 ORM 层 default="{}" 处理
- **stream_mode="messages"**: yield 格式为 (AIMessageChunk/ToolMessage, metadata) 元组
- **上传流程**: 前端上传 → AI提取 → 用户确认 → PUT /api/profile 保存（身高/体重回填基本信息）
- **RRF 融合**: `RF_score(d) = Σ 1/(60 + rank_i(d))`，向量和BM25按排名合并，k=60
- **BM25 分词**: 中文字级分词（"深蹲"→["深","蹲"]），英文/数字保持整体（"BMI"→["bmi"]），IDF 天然淘汰停用词
