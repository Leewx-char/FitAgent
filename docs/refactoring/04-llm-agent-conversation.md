# 04 - LLM Agent 对话与工具调用重构方案

> **状态**: 待实施  
> **优先级**: P0（直接影响对话体验和功能正确性）  
> **预计工时**: 5-7 天

---

## 一、现状诊断

### 1.1 Agent 架构分析

**当前架构**：
```
用户消息 → ReactAgent (LangGraph create_agent)
           ├── model: ChatTongyi (qwen-max)
           ├── 7个工具
           │   ├── rag_summarize ─── RagSummarizeService
           │   ├── get_user_profile ─── MySQL
           │   ├── get_fitness_summary ─── MySQL
           │   ├── get_weather ─── 外部 API
           │   ├── get_user_location ─── ContextVar
           │   ├── get_user_id ─── ContextVar
           │   ├── get_current_month ─── 本地时间
           │   └── trigger_report ─── 信号工具
           ├── 3个中间件
           │   ├── monitor_tool (工具监控 + 上下文注入)
           │   ├── log_before_model (日志)
           │   └── report_prompt_switch (动态提示词)
           └── 输出：SSE 流式消息
```

### 1.2 问题清单

| 类别 | 问题 | 位置 | 影响 |
|------|------|------|------|
| **Agent 实例管理** | 每次请求 `get_agent()` 创建新 `ReactAgent()`，内部 `create_agent()` 开销大 | `deps.py:31-33` | 请求延迟增加 |
| **工具函数** | `get_user_profile` 和 `get_fitness_summary` 各自手动创建 `SessionLocal()`，代码重复 | `agent_tools.py:236,272` | 重复代码，资源泄漏风险 |
| **数据库会话** | 工具函数内直接 `SessionLocal()` 绕过依赖注入 | `agent_tools.py:236,272` | 违反分层原则，不便于测试 |
| **会话管理** | 对话历史从数据库全量加载，无摘要/截断策略 | `chat.py` | Token 超限风险 |
| **上下文传递** | `_user_context` ContextVar 在异步环境中可能串数据 | `agent_tools.py:19` | 并发请求数据交叉 |
| **工具描述** | 工具 `description` 较简单，缺少参数说明和示例 | `agent_tools.py:143` | LLM 调用工具不准确 |
| **错误处理** | `get_fitness_summary` 无 `try/except` 包裹业务逻辑 | `agent_tools.py:263-361` | 数据库异常时崩溃 |
| **代码重复** | `prompt_loader.py` 三个函数结构完全相同 | `prompt_loader.py:6-43` | 维护成本高 |
| **滑动窗口** | 仅保留最近 20 轮（40 条），早于其的信息永久丢失 | `react_agent.py` (无此代码，在 chat.py 中) | 可能截断重要上下文 |
| **事实提取** | `_extract_session_facts` 用正则字典匹配，生硬 | `react_agent.py:80-135` | 无法理解非直接表述 |
| **系统提示词** | 88 行长文本，包含大量安全规则 | `prompts/main_prompt.txt` | 占用宝贵 Token 预算 |
| **工具选择** | 无工具选择策略，LLM 自主决定调用哪些工具 | `react_agent.py:63` | 简单场景调多余工具 |
| **工具编排** | 工具间无依赖声明（如 get_weather 依赖 get_user_location） | `agent_tools.py:151` | LLM 可能忘记先获取位置 |

### 1.3 性能瓶颈

```
单次对话请求延迟拆解（估算）：
  Agent 初始化 create_agent: ~300ms
  LLM 首次推理（决定工具）: ~1-2s
  rag_summarize 工具调用: ~500ms（向量检索 + BM25）
  其他工具调用: ~200ms
  LLM 最终回答（流式）: ~2-3s
  ──────────────────────────────
  总计: 3-6s
```

---

## 二、分步骤重构方案

### 步骤 1：Agent 实例池化

**当前问题**：每次请求新建 `ReactAgent()` → `create_agent()` 重新初始化 agent graph，开销约 300ms。

**方案**：全局单例 + 线程安全隔离：

```python
# app/services/react_agent.py 修改

import threading

class ReactAgent:
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        # 单例模式：整个进程共享一个 ReactAgent
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.agent = create_agent(
            model=get_chat_model(),
            system_prompt=load_system_prompts(),
            tools=[...],
            middleware=[...]
        )

# deps.py 修改
def get_agent():
    """返回全局单例 ReactAgent（进程级复用）"""
    return ReactAgent()  # __new__ 保证单例
```

**注意**：LangGraph 的 `create_agent` 本身是 stateless 的，状态由 `input_dict["messages"]` 传入，所以单例安全。

### 步骤 2：工具数据库会话统一

**当前问题**：工具内直接创建 `SessionLocal()` 绕过依赖注入，违反分层原则。

**方案**：提取公共 DB 上下文管理器 + 通过 `_user_context` 传递：

```python
# app/core/database.py 新增

from contextlib import contextmanager

@contextmanager
def get_db_session():
    """上下文管理器：自动 commit/rollback/close"""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
```

工具函数使用：

```python
# app/services/agent_tools.py 修改

from app.core.database import get_db_session

@tool(description="获取当前用户的完整健身画像...")
def get_user_profile():
    ctx = _user_context.get()
    user_id = ctx.get("user_id")
    if not user_id:
        return "未获取到用户信息，请让用户先登录。"

    with get_db_session() as db:  # ← 统一使用上下文管理器
        profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
        if not profile:
            return "用户尚未填写健身画像..."
        # ... 构建返回内容 ...

@tool(description="获取用户近4周运动数据摘要...")
def get_fitness_summary() -> str:
    ctx = _user_context.get()
    user_id = ctx.get("user_id")
    if not user_id:
        return "未获取到用户信息，请让用户先登录。"

    with get_db_session() as db:  # ← 统一使用
        since = date.today() - timedelta(weeks=4)
        records = (
            db.query(FitnessData)
            .filter(FitnessData.user_id == user_id, FitnessData.date >= since)
            .all()
        )
        # ... 聚合逻辑 ...
```

### 步骤 3：工具能力增强

**3.1 工具 description 优化**

LLM 依赖 description 来决定调用哪个工具。当前过于简略：

```python
# 当前
@tool(description="从知识库检索专业资料原始片段。可选通过source指定领域缩小范围")
def rag_summarize(query: str, source: str = "") -> str:

# 优化后
@tool(description="""从健身知识库中检索专业资料。

**何时使用**：用户询问健身知识、动作标准、训练方法、营养建议时。
**参数说明**：
  - query: str - 检索查询词，应使用专业术语（如"深蹲标准动作"而非"那个怎么做"）
  - source: str - 可选，限定知识领域："动作指南"|"营养学"|"训练计划"|"损伤预防"|"基础知识"
**返回格式**：参考资料编号 + 来源 + 内容原文片段
**示例**：rag_summarize(query="深蹲标准动作", source="动作指南")""")
def rag_summarize(query: str, source: str = "") -> str:
```

**3.2 添加工具依赖声明**

```python
# app/services/agent_tools.py 新增

# 工具依赖关系：{下游工具: [上游工具列表]}
TOOL_PREREQUISITES = {
    "get_weather": ["get_user_location"],  # 查天气前需要先获取位置
    "get_fitness_summary": ["get_user_id"],  # 查运动数据前需要用户 ID
    "get_user_profile": ["get_user_id"],
}

# 在 monitor_tool 中间件中添加前置检查
@wrap_tool_call
def monitor_tool(request, handler):
    tool_name = request.tool_call.get("name", "")
    
    # 检查依赖：如果依赖工具没被调用过，返回提示
    prerequisites = TOOL_PREREQUISITES.get(tool_name, [])
    missing = [p for p in prerequisites if p not in called_tools_history]
    if missing:
        return ToolMessage(
            content=f"调用 {tool_name} 前需要先调用：{', '.join(missing)}",
            tool_call_id=request.tool_call.get("id"),
        )
    # ... 正常执行 ...
```

**3.3 工具结果结构化**

当前工具返回纯文本，不利于 LLM 解析。改为结构化 JSON：

```python
# 示例：天气工具返回
{
    "status": "success",
    "city": "广州",
    "weather": "晴",
    "temperature": 28,
    "advice": "天气晴朗，适合户外跑步"  # 新增：运动建议
}

# 示例：运动数据工具返回
{
    "status": "success",
    "period": "近4周",
    "avg_rhr": {"value": 58, "label": "恢复良好"},
    "total_activities": 12,
    "total_duration_hours": 8.5,
    "trend": "训练负荷呈上升趋势"
}
```

### 步骤 4：对话管理优化

**4.1 对话历史摘要（Conversation Summary）**

**当前问题**：消息窗口只保留最近 20 轮，早前的信息永久丢失。

**方案**：引入滑动窗口 + 摘要：

```python
# app/services/conversation_manager.py (新文件)

class ConversationManager:
    """对话历史管理：滑动窗口 + LLM 摘要"""
    
    MAX_RECENT_MESSAGES = 20        # 保留最近 20 条完整消息
    SUMMARY_INTERVAL = 15           # 每 15 条生成一次摘要
    MAX_SUMMARY_LENGTH = 500        # 摘要最大字数
    
    @staticmethod
    def manage(messages: list[dict]) -> list[dict]:
        """
        处理策略：
        1. 总消息 ≤ 20 条 → 全部保留
        2. 总消息 > 20 条 → 
           - 生成前 N-20 条摘要
           - 保留最近 20 条完整消息
           - 组合返回：[summary_message] + recent_messages
        """
        if len(messages) <= ConversationManager.MAX_RECENT_MESSAGES:
            return messages
        
        # 计算需要摘要的消息数量
        summary_targets = messages[:-ConversationManager.MAX_RECENT_MESSAGES]
        recent = messages[-ConversationManager.MAX_RECENT_MESSAGES:]
        
        # LLM 生成摘要
        summary = ConversationManager._generate_summary(summary_targets)
        
        # 构造摘要消息
        summary_message = {
            "role": "system",
            "content": f"[对话历史摘要] {summary}"
        }
        
        logger.info(
            f"对话压缩：{len(messages)} → {len(recent) + 1}（摘要 + 最近{len(recent)}条）"
        )
        return [summary_message] + recent
    
    @staticmethod
    def _generate_summary(messages: list[dict]) -> str:
        """用 LLM 生成对话摘要"""
        dialog = "\n".join(
            f"{m['role']}: {m['content'][:300]}" for m in messages
        )
        
        prompt = f"""请用不超过200字总结以下对话的关键信息：
- 用户关注的健身目标
- 用户提到的重要事项（伤病史/饮食偏好/训练水平）
- 已讨论过的核心话题和结论

对话：
{dialog}

摘要："""
        
        try:
            result = get_chat_model().invoke(prompt)
            return result.content.strip()[:ConversationManager.MAX_SUMMARY_LENGTH]
        except Exception:
            return "(对话摘要生成失败)"
```

**4.2 会话事实提取升级**

**当前问题**：`_extract_session_facts` 使用硬编码的正则关键词匹配，无法理解复杂表述（"我之前踢球扭到过那里" → 无法提取脚踝伤）。

**方案**：改用 LLM 小模型做结构化提取：

```python
# app/services/react_agent.py 修改

FACT_EXTRACT_PROMPT = """从用户消息中提取健身相关的事实信息。只提取明确提到的信息，不要推测。

输出 JSON 格式（未提及的字段用 null）：
{
    "city": "用户所在城市",
    "training_goal": "增肌|减脂|塑形|耐力|力量",
    "injuries": ["膝盖伤", "腰伤", ...],
    "diet_pref": "素食|低碳水|高蛋白|均衡",
    "experience_level": "零基础|新手|中级|进阶",
    "weekly_available_days": 3,
    "equipment": ["哑铃", "弹力带", ...]
}

用户消息：{message}

JSON："""

@classmethod
def _extract_session_facts_llm(cls, messages: list[dict]) -> dict:
    """使用 LLM 进行会话事实提取（比关键词匹配更准确）"""
    # 合并用户消息
    user_messages = [
        msg for msg in messages 
        if msg.get("role") == "user"
    ]
    if not user_messages:
        return {}
    
    combined = "\n".join(
        m["content"][:500] for m in user_messages[-5:]  # 最近 5 条用户消息
    )
    
    prompt = FACT_EXTRACT_PROMPT.format(message=combined)
    
    try:
        model = get_chat_model()
        # 使用非流式调用节省成本（小响应）
        result = model.invoke(prompt)
        parsed = json.loads(result.content.strip())
        # 清理 null 值
        facts = {k: v for k, v in parsed.items() if v}
        logger.info(f"LLM 提取会话事实：{facts}")
        return facts
    except Exception as e:
        logger.warning(f"LLM 事实提取失败，回退到规则提取：{str(e)}")
        return cls._extract_session_facts(messages)  # 降级到规则提取
```

### 步骤 5：系统提示词分层精简

**当前问题**：88 行长提示词包含角色设定、安全规则、工具说明、输出格式约束，Token 预算占用大。

**方案**：拆分为核心提示词 + 动态注入模块：

```python
# prompts/system_prompts.py (新文件，替代 .txt)

SYSTEM_PROMPTS = {
    "role": """你是一个专业的AI运动教练，名叫FitAgent。你的任务是基于用户的个人画像、
运动数据和专业健身知识库，提供安全、科学、个性化的运动指导。""",
    
    "principles": """指导原则：
1. 安全第一：对有伤病史的用户降低训练强度
2. 个性化：根据年龄/体重/目标定制建议
3. 渐进超负荷：新手从低强度开始，逐步增加
4. 科学依据：所有建议基于运动科学和知识库资料
5. 鼓励支持：用积极语言激励用户坚持锻炼""",
    
    "tools": """可用工具：
- rag_summarize: 检索健身知识库（需要专业知识时使用）
- get_user_profile: 获取用户画像（首次对话时使用）
- get_fitness_summary: 获取运动数据（讨论训练效果时使用）
- get_weather: 查询天气（影响户外训练建议）
- trigger_report: 生成运动报告（用户明确要求时使用）
- get_user_location / get_user_id / get_current_month: 辅助工具""",
    
    "output": """输出规范：
- 使用结构化格式：分点列出训练计划、用 Markdown 表格展示数据
- 引用知识库时用 [ref:N] 标注来源
- 涉及数据时标注日期范围和样本量
- 对话语气友善专业，可适当使用运动领域的鼓励话语""",
    
    "safety": """安全约束：
- 禁止给出医疗诊断建议
- 如有严重伤病，建议先咨询医生
- 不建议危险训练动作
- 保护用户隐私，不泄露个人信息""",
}

def build_system_prompt(context: dict = None) -> str:
    """根据上下文动态组合系统提示词"""
    parts = [
        SYSTEM_PROMPTS["role"],
        "",
        SYSTEM_PROMPTS["principles"],
        "",
        SYSTEM_PROMPTS["output"],
        "",
        SYSTEM_PROMPTS["safety"],
    ]
    
    # 如果用户已有画像，加入个性化提示
    if context and context.get("has_profile"):
        parts.insert(3, "用户已完成画像设置，请基于用户数据提供个性化建议。")
    
    # 如果会话中有事实提取结果
    if context and context.get("session_facts"):
        facts = context["session_facts"]
        fact_lines = [f"- {k}: {v}" for k, v in facts.items()]
        parts.append("\n已知用户信息：\n" + "\n".join(fact_lines))
    
    return "\n".join(parts)
```

### 步骤 6：工具路由优化

**当前问题**：所有问题都通过 Agent 决策，简单问候 ("你好") 也要经过 LLM 推理决定不调工具。

**方案**：添加预处理层，快速判断意图：

```python
# app/services/intent_router.py (新文件)

import re

class IntentRouter:
    """轻量意图分类：快速判断是否需要走 Agent/RAG 流程"""
    
    # 快速匹配模式（不走 LLM，毫秒级）
    GREETING_PATTERNS = [
        r"^(你好|hi|hello|嘿|哟|在吗|在不在)[\s!！。.]*$",
        r"^(早上好|下午好|晚上好|晚安|早安)[\s!！。.]*$",
    ]
    
    SIMPLE_QA = {
        "你是谁": "我是FitAgent，你的AI运动教练。我可以根据你的身体数据和健身目标为你定制训练计划。",
        "你能做什么": "我可以：\n1. 查询健身知识（动作标准、训练方法、营养建议）\n2. 根据你的身体数据制定个性化训练计划\n3. 分析你的运动数据，追踪训练效果\n4. 结合天气情况推荐户外/室内训练\n5. 生成运动报告",
        "你好吗": "我很好，随时准备帮你制定训练计划！今天想了解什么健身知识？",
    }
    
    @classmethod
    def classify(cls, query: str) -> dict:
        """
        返回：{"type": "greeting"|"simple_qa"|"fitness_knowledge"|"training_plan"|"data_analysis", "confidence": float}
        """
        text = query.strip().lower()
        
        # 1. 问候检测
        for pattern in cls.GREETING_PATTERNS:
            if re.match(pattern, text):
                return {"type": "greeting", "confidence": 0.95}
        
        # 2. 简单问答匹配
        for pattern, answer in cls.SIMPLE_QA.items():
            if pattern in text:
                return {"type": "simple_qa", "confidence": 0.9, "answer": answer}
        
        # 3. 健身知识相关（需要 RAG）
        knowledge_keywords = [
            "怎么做", "标准动作", "正确姿势", "吃什么", "怎么练",
            "训练方法", "拉伸", "热身", "受伤", "酸痛", "恢复",
        ]
        if any(kw in text for kw in knowledge_keywords):
            return {"type": "fitness_knowledge", "confidence": 0.7}
        
        # 4. 训练计划（需要 Agent + 画像 + RAG）
        plan_keywords = ["制定计划", "训练计划", "帮我排", "怎么安排", "每周练"]
        if any(kw in text for kw in plan_keywords):
            return {"type": "training_plan", "confidence": 0.75}
        
        # 5. 数据分析（需要 Agent + 运动数据）
        data_keywords = ["运动数据", "训练效果", "进步", "心率", "睡眠", "报告"]
        if any(kw in text for kw in data_keywords):
            return {"type": "data_analysis", "confidence": 0.7}
        
        # 6. 默认走 Agent
        return {"type": "general", "confidence": 0.5}
```

在 `chat.py` 路由中使用：

```python
# app/api/routers/chat.py 修改

@router.post("/chat")
async def chat(request: ChatRequest, ...):
    # 预处理：意图分类
    intent = IntentRouter.classify(request.message)
    
    if intent["type"] == "greeting":
        # 快速路径：直接返回问候，不走 Agent
        yield f'{{"type":"text","content":"你好！我是FitAgent，你的AI运动教练。有什么可以帮你的？"}}\n'
        return
    
    if intent["type"] == "simple_qa" and "answer" in intent:
        # 快速路径：预设回答
        yield f'{{"type":"text","content":"{intent["answer"]}"}}\n'
        return
    
    # 正常路径：走 Agent
    agent = get_agent()
    for chunk in agent.execute_stream(messages, user_id=user_id, city=city):
        yield chunk
```

### 步骤 7：提示词加载去重

**当前问题**：`prompt_loader.py` 三个函数结构完全相同（try/except 读文件）。

**方案**：抽取公共函数：

```python
# app/utils/prompt_loader.py 简化

from functools import lru_cache
from app.utils.config_handler import get_prompts_config
from app.utils.path_tool import get_abs_path
from app.utils.logger_handler import logger

def _load_prompt(config_key: str) -> str:
    """通用提示词加载函数"""
    config = get_prompts_config()
    try:
        path = config[config_key]
    except KeyError:
        logger.error(f"配置中缺少提示词路径：{config_key}")
        raise
    
    abs_path = get_abs_path(path)
    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        logger.error(f"读取提示词文件失败：{abs_path}，{str(e)}")
        raise

def load_system_prompts() -> str:
    return _load_prompt("main_prompt_path")

def load_report_prompts() -> str:
    return _load_prompt("report_prompt_path")

def load_health_extract_prompts() -> str:
    return _load_prompt("health_extract_prompt_path")
```

---

## 三、重构后 Agent 架构

```
用户消息
│
├── IntentRouter (快速分类)
│   ├── greeting → 直接回答（1ms）
│   ├── simple_qa → 预设回答（1ms）
│   └── fitness/training/data → 进入 Agent
│
├── ReactAgent (单例)
│   ├── 预处理：历史摘要 + 事实提取
│   │   ├── ConversationManager.manage(messages)
│   │   └── _extract_session_facts_llm(messages)
│   │
│   ├── 中间件链
│   │   ├── monitor_tool → 上下文注入 + 依赖检查 + 日志
│   │   ├── log_before_model → 调用前日志
│   │   └── report_prompt_switch → 动态提示词
│   │
│   ├── LLM 推理 (ChatTongyi)
│   │   └── 系统提示词：build_system_prompt(context)
│   │
│   └── 工具调用
│       ├── rag_summarize → RAG 服务（重构后）
│       ├── get_user_profile → DB（通过上下文管理器）
│       ├── get_fitness_summary → DB（通过上下文管理器）
│       ├── get_weather → 外部 API（带熔断重试）
│       ├── get_user_location → ContextVar
│       ├── get_user_id → ContextVar
│       ├── get_current_month → 本地
│       └── trigger_report → 信号工具
│
└── SSE 流式输出 → 前端
```

---

## 四、实施检查清单

- [ ] 1. `ReactAgent` 改为单例模式（进程级复用）
- [ ] 2. `database.py` 添加 `get_db_session()` 上下文管理器
- [ ] 3. `agent_tools.py` 工具统一使用 `get_db_session()`
- [ ] 4. `agent_tools.py` 工具 description 重写（添加参数说明+示例+何时使用）
- [ ] 5. `agent_tools.py` 工具添加前置依赖声明
- [ ] 6. 创建 `app/services/conversation_manager.py`（历史摘要管理）
- [ ] 7. `react_agent.py` `_extract_session_facts` 升级为 LLM 驱动
- [ ] 8. `prompts/` 提示词分层结构化（`system_prompts.py`）
- [ ] 9. `prompt_loader.py` 抽取公共函数去重
- [ ] 10. 创建 `app/services/intent_router.py`（快速意图分类）
- [ ] 11. `chat.py` 添加意图路由分流
- [ ] 12. 工具返回格式统一为结构化 JSON
- [ ] 13. `_user_context` ContextVar 安全性审查（异步环境隔离）
- [ ] 14. `get_fitness_summary` 添加异常处理
- [ ] 15. 编写对应单元测试

---

## 五、验收标准

1. Agent 单例复用后，连续请求延迟 < 3000ms（首次 < 5000ms）
2. "你好" → 直接返回问候，不走 Agent（< 5ms）
3. 对话超过 20 轮后，自动生成摘要保持上下文
4. "我膝盖不太好适合什么训练" → LLM 正确提取"膝盖伤"
5. 工具调用前自动检查依赖（如查天气前先获取位置）
6. 提示词文件去重，三个函数收敛到一个公共 `_load_prompt`
7. 所有工具返回 JSON 结构化数据
8. `ruff check app/` 无错误，`pytest app/tests/` 全部通过
