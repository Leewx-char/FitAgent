# FitAgent 项目决策记录

## 一、选题变更

| 原选题 | 新选题 |
|--------|--------|
| 智扫通（智能客服） | 智体 / FitAgent（多 Agent 个性化运动教练） |
| 单 Agent + RAG + 基础问答 | 多 Agent + Agentic RAG + 工具调用 + 用户画像 |

**变更理由**：
- 智能客服方向 GitHub 上有 24,000+ 类似项目，没有区分度
- 运动教练方向只有 ~535 个项目且几乎都是玩具级单 Agent，空白领域
- 面试展示技术深度更好：多 Agent 协作、Agentic RAG、工具调用、用户记忆
- 运动教练领域 RAG 知识库天然适配（运动科学、营养学、损伤预防）

---

## 二、已完成的工作（智扫通阶段）

### FastAPI 后端改造

| 文件 | 作用 |
|------|------|
| `server/auth.py` | JWT 认证核心（hash_password, verify_password, create_access_token, get_current_user） |
| `server/routers/auth.py` | 注册/登录/获取当前用户接口 |
| `server/routers/sessions.py` | 会话 CRUD（list/create/delete/update） |
| `server/routers/messages.py` | 消息查询（分页） |
| `server/routers/chat.py` | 聊天 SSE 流式响应（认证、DB 读写、ContextVar、自动建会话、标题更新） |
| `server/main.py` | 路由注册、CORS 配置化 |
| `server/models.py` | User/Session/Message/UserProfile 四表 ORM 模型 |
| `server/schemas.py` | Pydantic 请求/响应模型（含 ProfileCreate/Update/Response + field_validator） |
| `server/database.py` | MySQL 连接池配置 |
| `server/deps.py` | get_db 和 get_agent 依赖注入 |
| `server/routers/profile.py` | GET/POST/PUT /api/profile 画像接口 |
| `agent/tools/agent_tools.py` | 7个工具，含 get_user_profile（SessionLocal 手动会话）、trigger_report |

### 关键技术决策

- 认证方案：JWT Token，不用 Session/Cookie
- 登录接口：OAuth2PasswordRequestForm（表单格式），兼容 Swagger Authorize
- 密码存储：bcrypt 哈希（bcrypt==4.0.1，5.0 与 passlib 不兼容）
- 用户上下文传递：ContextVar + run_in_executor 自动复制
- SSE 流式：哨兵值（_SENTINEL）替代 StopIteration
- 数据隔离：所有查询加 user_id 条件
- 注册与问卷分离：注册只管账号密码，问卷用 POST /api/profile
- UserProfile：核心字段用数据库列，动态字段用 JSON（Text 列存 JSON 字符串）
- ProfileResponse：field_validator 做 json.loads 反序列化
- ProfileUpdate：model_dump(exclude_unset=True) 只更新传了的字段
- User/UserProfile 原子性：db.flush() 获取 user.id 后再创建 profile

---

## 三、用户数据来源方案

### 方案：问卷 + 对话混合

| 数据来源 | 数据类型 | 时机 |
|---------|---------|------|
| 注册 Onboarding 问卷 | 性别、年龄、身高、体重、目标、训练天数、经验、伤病史、饮食限制 | 注册后首次进入 |
| 对话中提取 | 训练量、不适反馈、偏好变化、进步情况 | 每次聊天 |

---

## 四、用户画像存储方案

### 混合存储：核心列 + JSON 扩展

```python
class UserProfile(Base):
    __tablename__ = "user_profiles"
    id              # 自增主键
    user_id         # 外键 → users.id, unique

    # 核心字段（需要查询/计算的）→ 数据库列
    gender          # 性别（必填）
    age             # 年龄
    height          # 身高(cm)
    weight          # 体重(kg)
    goal            # 目标: 减脂/增肌/塑形/耐力/健康（中文直传）
    experience      # 经验: 新手/中级/高级（中文直传，默认"新手"）

    # 动态字段（经常变化的）→ JSON
    injuries        # 伤病史 ["膝盖", "腰椎"]
    diet_restrict   # 饮食限制 ["素食", "低碳"]
    preferences     # 偏好 {"preferred_time": "早上", "equipment": "哑铃", "gym": true}

    created_at
    updated_at
```

**选择理由**：
- 核心字段用列：支持计算（BMI、TDEE）和索引查询
- 动态字段用 JSON：灵活扩展，不用频繁做数据库迁移
- 面试展示时两者兼顾，证明既懂数据库设计又考虑了扩展性
- goal/experience 存中文直传，不用英文 map，简化前端交互

---

## 五、注册流程设计

```
注册（账号密码）
     │
     ▼
前端分步问卷（5-8题，30秒）
  · 性别（必填）
  · 年龄
  · 身高/体重
  · 健身目标
  · 每周训练天数
  · 运动经验
  · 伤病史
  · 饮食限制
     │
     ▼
主界面聊天（Agent 基于画像回答）
  · 持续提取补充信息
  · 画像随时间进化
```

---

## 六、训练记录方案

第一版：简单版，从对话中提取训练信息（动作、组数、重量），存在 message 中，不单独建表。

---

## 七、技术架构变更

| 组件 | 智扫通（原有） | FitAgent（当前） |
|------|--------------|-------------|
| Agent | 单 ReactAgent | 单 ReactAgent + 7工具（多Agent待开发） |
| 知识库 | 扫地机器人文档 RAG | 运动科学5个知识库（415问） |
| Prompt | 客服话术 | 运动教练系统提示词 |
| 数据模型 | User + Session + Message | + UserProfile |
| 用户上下文 | 城市 + 用户ID | 完整画像（目标/体能/伤病史/偏好） |
| 工具调用 | get_weather, search_knowledge | search_knowledge, get_user_profile, trigger_report, calculate_bmi, calculate_tdee, get_exercise_plan, get_nutrition_advice |
| 同义词/归一化 | 扫地机器人5组 | 运动科学19组同义词 + 14组归一化替换 |
| 前端 | Streamlit / Swagger | Vue3（待开发） |

---

## 八、推荐项目架构（FastAPI 最佳实践）

### 当前架构 vs 推荐架构差距

| 关注点 | FastAPI 推荐 | 当前状态 | 差距 |
|--------|-------------|---------|------|
| 路由组织 | APIRouter + prefix + tags | ✅ 已做到 | 无 |
| 依赖注入 | 所有共享逻辑用 Depends() | ✅ 已做到 | 无 |
| Schema 分层 | Create / Update / Response 分离 | ✅ 已做到 | 无 |
| 配置管理 | pydantic-settings BaseSettings | ⚠️ 用 os.getenv | 较大 |
| 业务逻辑 | Service 层抽离 | ❌ chat.py 逻辑重 | 较大 |
| 类型注解 | Annotated 类型别名 | ⚠️ 没用 Annotated | 小 |

### 推荐目录结构

```
app/
├── main.py                 # FastAPI 实例 + include_router
├── dependencies.py         # 共享依赖（get_db, get_current_user 等）
├── models.py               # ORM 模型
├── schemas.py              # Pydantic 请求/响应模型
├── config.py               # pydantic-settings 配置管理
├── auth.py                 # 认证工具（JWT、密码哈希）
├── database.py             # 数据库连接/会话管理
├── routers/                # 路由模块
│   ├── auth.py
│   ├── sessions.py
│   ├── messages.py
│   ├── chat.py
│   └── profile.py          # 用户画像接口（新增）
├── services/               # 业务逻辑层（后续重构）
│   ├── chat_service.py
│   └── profile_service.py
└── tests/
```

---

## 九、开发优先级与进度

### 阶段1：基础改造（✅ 已完成）

| 编号 | 任务 | 状态 |
|------|------|------|
| 1 | UserProfile 模型 + Schema + API | ✅ 完成 |
| 2 | 注册流程改造（注册与问卷分离） | ✅ 完成 |
| 3 | Agent 系统提示词改造（客服 → 运动教练） | ✅ 完成 |
| 4 | 知识库数据替换（5个txt，415问答） | ✅ 完成 |
| 5 | RAG 同义词/归一化/停用词改造 | ✅ 完成 |

### 知识库文件详情

| 文件 | 问答数 | 覆盖内容 |
|------|--------|---------|
| `data/健身基础知识.txt` | 35 | 基础概念、训练方法、常见误区、身体指标 |
| `data/运动损伤预防.txt` | 80 | 热身拉伸、膝/腰/肩/踝/肘腕损伤、安全、恢复、体态等 |
| `data/营养学知识.txt` | 100 | 宏量营养素、饮食计划、补剂、减脂/增肌饮食、微量营养素等 |
| `data/训练计划指南.txt` | 100 | 新手入门、计划设计、减脂/增肌/力量训练、周期化等 |
| `data/动作指南大全.txt` | 100 | 复合动作、各肌群训练、柔韧性、常见错误、进阶技术等 |

### 清理工作（✅ 已完成）

- 删除扫地机器人旧知识库文件（故障排除.txt、100问pdf/txt、维护保养.txt、选购指南.txt、records.csv）
- 清空 chroma_db/ 目录（首次启动自动重建索引）
- synonym_map 改为运动科学19组（含 BMR 等缩写）
- _normalize_query 替换词改为运动科学14组
- stopwords 移除"机器人"相关词，新增"吧"

### 阶段2：多Agent与智能功能（⏳ 待开发）

| 编号 | 任务 | 状态 |
|------|------|------|
| 6 | 多 Agent 协作架构（训练/营养/防护/调度） | ⏳ |
| 7 | 对话中提取用户画像更新 | ⏳ |
| 8 | 工具调用（BMI、TDEE、食物热量） | ⏳ |
| 9 | Agentic RAG（Agent 自主决定检索策略） | ⏳ |

### 阶段3：高级功能（⏳ 待开发）

| 编号 | 任务 | 状态 |
|------|------|------|
| 10 | 知识图谱（运动-肌肉-损伤关联） | ⏳ |
| 11 | Strava/USDA API 对接 | ⏳ |
| 12 | 训练记录表 + 前端历史展示 | ⏳ |

### 前端（🔧 框架已搭建，逐步学习中）

- Vue3 + Vite + Pinia + Axios + Naive UI
- 配色：主色 #42A5F5，浅变体 #c6e4fc，风格清新极简
- 已搭建完成，待逐步理解和学习

---

## 十、前端项目详情

### 技术选型

| 技术 | 版本 | 用途 |
|------|------|------|
| Vue3 | 3.5.34 | 前端框架 |
| Vite | 8.0.x | 构建工具 + 开发服务器 |
| Pinia | 3.0.4 | 状态管理 |
| Axios | 1.16.1 | HTTP 请求 |
| Vue Router | 4.6.4 | 路由 |
| Naive UI | 2.44.1 | UI 组件库 |

### 配色方案（清新极简风）

| 用途 | 色值 | 说明 |
|------|------|------|
| 主色 Primary | `#42A5F5` | 按钮、链接、高亮 |
| 浅变体 | `#c6e4fc` | 背景、标签底色、hover |
| 深变体 | `#1E88E5` | hover 状态、强调 |
| 页面背景 | `#F8FBFF` | 极浅蓝白 |
| 卡片背景 | `#FFFFFF` | 白底 + 微阴影 |
| 文字主色 | `#2C3E50` | 深灰蓝 |
| 文字辅色 | `#8E99A4` | 灰色 |

### 目录结构

```
frontend/
├── vite.config.js           # Vite 配置 + 代理 + @别名
├── index.html               # 入口 HTML
├── package.json
└── src/
    ├── main.js               # 应用入口：Pinia + Router + Naive UI
    ├── App.vue               # 根组件：主题配置
    ├── api/
    │   ├── index.js          # Axios 实例 + JWT 拦截器
    │   ├── auth.js            # 注册/登录/获取当前用户
    │   ├── chat.js            # 会话 CRUD/消息/聊天
    │   └── profile.js        # 画像 GET/POST/PUT
    ├── stores/
    │   ├── auth.js            # 认证状态（token/user/login/logout）
    │   ├── profile.js         # 画像状态（profile/hasProfile/fetchProfile/saveProfile）
    │   └── chat.js            # 聊天状态（sessions/messages）
    ├── router/
    │   └── index.js           # 5 个路由 + 登录守卫
    ├── views/
    │   ├── Login.vue          # 登录页
    │   ├── Register.vue       # 注册页
    │   ├── Onboarding.vue     # 分步问卷（5 步）
    │   ├── Chat.vue           # 聊天页（SSE 流式 + 会话侧边栏）
    │   └── Profile.vue        # 画像查看/编辑页
    └── composables/           # （预留）
```

### 页面路由

| 路径 | 页面 | 需要登录 |
|------|------|---------|
| `/login` | 登录 | 否 |
| `/register` | 注册 | 否 |
| `/onboarding` | 分步问卷 | 是 |
| `/` | 聊天主页 | 是 |
| `/profile` | 画像查看/编辑 | 是 |

### 已验证的 API 接口（12个全部通过）

| # | 接口 | 方法 | 说明 |
|---|------|------|------|
| 1 | `/api/health` | GET | 健康检查 |
| 2 | `/api/auth/register` | POST | 注册 |
| 3 | `/api/auth/login` | POST | 登录（返回JWT） |
| 4 | `/api/auth/me` | GET | 获取当前用户 |
| 5 | `/api/profile` | POST | 创建画像 |
| 6 | `/api/profile` | GET | 查询画像 |
| 7 | `/api/profile` | PUT | 更新画像 |
| 8 | `/api/sessions` | POST | 创建会话 |
| 9 | `/api/sessions` | GET | 会话列表 |
| 10 | `/api/sessions/:id/messages` | GET | 会话消息 |
| 11 | `/api/chat` | POST | SSE 流式聊天（含画像+RAG） |
| 12 | `/api/sessions/:id` | DELETE | 删除会话 |

### Bug fix（本轮修复）

- **问题**：`get_user_profile` 工具在 LangGraph 内执行时获取不到 ContextVar
- **原因**：LangGraph 工具调用上下文隔离，Python ContextVar 无法穿透
- **修复**：通过 `runtime.context` 传递 `user_id`/`city`，在 `monitor_tool` 中桥接回 ContextVar
- **涉及文件**：`agent/react_agent.py`、`agent/tools/middleware.py`、`server/routers/chat.py`

---

## 十一、保留不变的部分

- FastAPI 后端架构（auth, routers, database, deps）
- JWT 认证体系
- SSE 流式响应机制
- ContextVar 上下文传递
- Session / Message 数据模型

---

## 十二、关键知识点

- **bcrypt**: 必须是 4.0.1 版本，5.0+ 与 passlib 不兼容
- **哈希不可逆**: verify_password 是用同样明文重新哈希比对，不是解密
- **ContextVar**: 在 run_in_executor 中自动复制到子线程，但 LangGraph 工具调用上下文隔离
- **LangGraph context 传递**: user_id/city 通过 agent.stream(context={...}) 传入，在 monitor_tool 中从 runtime.context 取出并桥接到 _user_context
- **OAuth2PasswordRequestForm**: 登录接口用表单格式，不是 JSON
- **哨兵值**: SSE 流式生成器用 _SENTINEL 替代 StopIteration
- **get_user_profile 工具**: 用 SessionLocal() 手动创建 DB 会话，不能用 Depends
- **UserProfile JSON 字段**: Text 列存 JSON 字符串，ProfileResponse 用 field_validator 做 json.loads
- **ProfileUpdate**: model_dump(exclude_unset=True) 只更新传了的字段
- **db.flush()**: 获取 user.id 但不提交，确保 User/Profile 原子性
- **goal/experience**: 存中文直传（减脂/增肌/新手），不用英文 map
- **gender/age/height/weight**: 必填字段
- **experience 默认值**: "新手"（中文）
- **BMR/BMI**: 保留原词不做归一化替换，同义词映射中 BMR 归入"基础代谢"组
- **chroma_db**: 已清空，首次启动需重建索引（516个文档切片）
- **SessionModel**: ORM 模型与 SQLAlchemy Session 同名，用 SessionModel 别名区分
- **SSE 流式前端**: POST /api/chat 返回 SSE，前端用 fetch + ReadableStream 逐行解析 data: 前缀
- **Vite 代理**: 开发时 /api 请求代理到 localhost:8000，避免 CORS
- **Pinia 状态持久化**: token 和 user 存 localStorage，页面刷新自动恢复