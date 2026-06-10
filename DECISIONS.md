# FitAgent 项目决策记录

## 一、选题变更

| 原选题 | 新选题 |
|--------|--------|
| 智扫通（智能客服） | 智体 / FitAgent（多 Agent 个性化运动教练） |
| 单 Agent + RAG + 基础问答 | 多 Agent + Agentic RAG + 工具调用 + 用户画像 + 多模态文档 |

**变更理由**：运动教练方向项目少、空白领域多，面试展示技术深度更好。

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
| 健康数据 | health_data TEXT 列存 JSON 整体 | 增删字段只改提示词和前端，中间层零改动 |
| VL 调用 | ChatTongyi (qwen-vl-plus) | 通义千问 VL 中文识别最佳 |
| PDF 处理 | 文字≥200字走 LLM，<200走 VL | 平衡速度与准确性 |
| 前端框架 | Naive UI（非 Element Plus） | 配色 #42A5F5，清新极简风 |

---

## 三、开发进度

### 阶段1：基础改造 ✅

- UserProfile 模型 + Schema + API（GET/POST/PUT）
- 注册流程改造（注册与问卷分离）
- Agent 提示词重写（客服 → 运动教练）
- 知识库替换（5个txt，415问答）+ chroma_db 重建
- RAG 同义词/归一化/停用词配置化

### 阶段2：RAG 优化 ✅

- 参数调优 + 去掉中间 LLM 调用（2-4秒提速）
- 同义词映射迁移到 config/synonyms.yml
- source 可选过滤 + Jaccard 去重（阈值0.8）

### 阶段3：多模态健康文档上传 ✅

- 后端：upload_router + doc_parser + VL/LLM 双路线 + 加密PDF检测 + health_data 存储
- 前端：Chat.vue 上传按钮 + 确认面板 + Profile.vue 健康数据展示
- 提示词：10个健身核心字段 + other_findings 扩展 + 置信度标注
- 去重：身高/体重从体检报告回填到基本信息，不在健康数据区块重复展示
- VL模型 response.content list→str 修复
- 上传按钮 loading 状态替代 title 悬浮提示

### 阶段4：安全加固 + 联调测试 🔧

- 端到端测试（图片/PDF/加密PDF上传流程）
- 上传接口速率限制
- 提取结果后端校验（防恶意 JSON 注入）
- 前端思维链可视化（工具调用过程实时展示）

### 阶段5：项目重构 + RAG 升级 ✅

- 项目结构重组：按 FastAPI 标准分层（`app/core/` / `app/api/` / `app/services/` / `app/utils/`）
- RAG 双路检索：BM25 关键词检索（rank-bm25，字级分词 + TF-IDF + 长度归一化）替代旧版 `coverage * 0.3`
- RRF 排名融合：替代线性加权，两路排名公平对待、互不干扰
- 检索耗时保持 ~300ms，BM25 增加延迟 <5ms

### 远期优化

- 多 Agent 协作架构
- 对话中提取用户画像更新
- Agentic RAG（Agent 自主决定检索策略）
- 意图路由（降低延迟到1-3秒）

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
health_data       # 从文档提取的健康指标（10个固定字段 + other_findings）
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

不在上述字段中的发现 → `other_findings` 数组，前端同风格渲染。

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

---

## 六、关键知识点备忘

- **bcrypt**: 必须是 4.0.1，5.0+ 与 passlib 不兼容
- **ContextVar**: run_in_executor 自动复制，LangGraph 工具调用隔离需 runtime.context 桥接
- **SSE**: POST /api/chat，前端 fetch + ReadableStream 逐行解析 data: 前缀
- **ProfileUpdate**: model_dump(exclude_unset=True) 只更新传了的字段
- **health_data**: 增删字段只改提示词（prompts/health_extract.txt）+ 前端映射（fieldLabels），数据库/Schema/API 零改动
- **VL模型**: ChatTongyi response.content 可能是 list，需 isinstance(content, list) 转 str
- **MySQL TEXT**: 不支持 DEFAULT 值，由 ORM 层 default="{}" 处理
- **stream_mode="messages"**: yield 格式为 (AIMessageChunk/ToolMessage, metadata) 元组
- **上传流程**: 前端上传 → AI提取 → 用户确认 → PUT /api/profile 保存（身高/体重回填基本信息）
- **RRF 融合**: `RF_score(d) = Σ 1/(60 + rank_i(d))`，向量和BM25按排名合并，k=60
- **BM25 分词**: 中文字级分词（"深蹲"→["深","蹲"]），英文/数字保持整体（"BMI"→["bmi"]），IDF 天然淘汰停用词