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
| `server/models.py` | User/Session/Message 三表 ORM 模型 |
| `server/schemas.py` | Pydantic 请求/响应模型 |
| `server/database.py` | MySQL 连接池配置 |
| `server/deps.py` | get_db 和 get_agent 依赖注入 |
| `agent/tools/agent_tools.py` | _user_context ContextVar，get_user_id/get_user_location |

### 关键技术决策

- 认证方案：JWT Token，不用 Session/Cookie
- 登录接口：OAuth2PasswordRequestForm（表单格式），兼容 Swagger Authorize
- 密码存储：bcrypt 哈希（bcrypt==4.0.1，5.0 与 passlib 不兼容）
- 用户上下文传递：ContextVar + run_in_executor 自动复制
- SSE 流式：哨兵值（_SENTINEL）替代 StopIteration
- 数据隔离：所有查询加 user_id 条件

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
    age             # 年龄
    height          # 身高(cm)
    weight          # 体重(kg)
    goal            # 目标: cut/bulk/recomp/endurance/health
    experience      # 经验: beginner/intermediate/advanced

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

---

## 五、注册流程设计

```
注册（账号密码）
     │
     ▼
前端分步问卷（5-8题，30秒）
  · 性别
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

| 组件 | 智扫通（现有） | 智体（目标） |
|------|--------------|-------------|
| Agent | 单 ReactAgent | 多 Agent（训练/营养/防护/调度） |
| 知识库 | 扫地机器人文档 RAG | 运动科学 + 营养学 + 损伤预防 RAG |
| Prompt | 客服话术 | 运动教练系统提示词 |
| 数据模型 | User + Session + Message | + UserProfile |
| 用户上下文 | 城市 + 用户ID | 完整画像（目标/体能/伤病史/偏好） |
| 工具调用 | get_weather, search_knowledge | + BMI计算, TDEE计算, 食物热量查询 |
| 前端 | Streamlit / Swagger | Vue3 |

---

## 八、推荐项目架构（FastAPI 最佳实践）

### 当前架构 vs 推荐架构差距

| 关注点 | FastAPI 推荐 | 当前状态 | 差距 |
|--------|-------------|---------|------|
| 路由组织 | APIRouter + prefix + tags | ✅ 已做到 | 无 |
| 依赖注入 | 所有共享逻辑用 Depends() | ✅ 已做到 | 无 |
| Schema 分层 | Create / Update / Response 分离 | ⚠️ 只有 Response | 中等 |
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

## 九、开发优先级

```
阶段1 (当前优先):
  1. 新增 UserProfile 模型 + Schema + API
  2. 改造注册流程（加问卷数据）
  3. 改造 Agent 系统提示词（客服 → 运动教练）
  4. 替换知识库数据（运动科学内容）
  5. 前端分步问卷页面（Vue3）

阶段2 (后续):
  6. 多 Agent 协作架构（训练Agent/营养Agent/防护Agent）
  7. 对话中提取用户画像更新
  8. 工具调用（BMI、TDEE、食物热量）
  9. Agentic RAG（Agent 自主决定检索策略）

阶段3 (锦上添花):
  10. 知识图谱（运动-肌肉-损伤关联）
  11. Strava/USDA API 对接
  12. 训练记录表 + 前端历史展示
```

---

## 十、保留不变的部分

- FastAPI 后端架构（auth, routers, database, deps）
- JWT 认证体系
- SSE 流式响应机制
- ContextVar 上下文传递
- Session / Message 数据模型

---

## 十一、关键知识点

- **bcrypt**: 必须是 4.0.1 版本，5.0+ 与 passlib 不兼容
- **哈希不可逆**: verify_password 是用同样明文重新哈希比对，不是解密
- **ContextVar**: 在 run_in_executor 中自动复制到子线程
- **OAuth2PasswordRequestForm**: 登录接口用表单格式，不是 JSON
- **哨兵值**: SSE 流式生成器用 _SENTINEL 替代 StopIteration
- **User.city**: 字段目前默认空字符串，城市信息回退到环境变量
- **SessionModel**: ORM 模型与 SQLAlchemy Session 同名，用 SessionModel 别名区分