# 03 - RAG 在线检索链路（V1）

> **状态**：在线 RAG V1 已实施；RAGAS 评测后置。
> **目标**：先完成从多轮用户问题到可引用上下文的完整在线链路，再以 RAGAS 对“检索 + 最终回答”进行质量验收与回归。
> **前置条件**：[02 离线知识管线](./02-rag-offline-pipeline.md) 已发布 Qdrant `rag_active` revision。

---

## 1. 范围与原则

本阶段只负责“问题如何变成可靠的检索上下文”。它不重建索引、不写入 Qdrant、不自动联网搜索，
也不把评测分数当作功能开发的前置条件。

```text
用户消息与会话历史
  → 明确通用知识问题：直接 RAG；其他问题：Agent 编排
  → 查询规划（可选改写 / 最多两个子查询）
  → 子查询的 Dense + BM25 并行召回
  → revision 对齐检查、RRF 融合与去重
  → 有界候选重排序
  → 父段上下文预算与证据编号
  → Agent 根据 [证据:N] 生成最终回答
  → SSE 同步返回结构化证据卡片
```

设计约束：

1. **普通问题快速通过**：仅有指代或复合问题才调用查询规划模型，普通单轮问题不会额外增加 LLM 调用。
2. **所有扩展都受上限约束**：最近 3 轮历史、最多 2 个子查询、有限候选集、固定上下文预算；禁止递归改写或无限重试。
3. **所有新增能力可回退**：查询规划异常回退原问题；BM25 revision 不一致时回退 dense；重排序可关闭且保留 RRF 基础顺序。
4. **线上只读**：在线过程只读 `rag_active` 与同 revision 的 BM25 工件。资料导入、切分、去重与 revision 发布仍属于 02。
5. **快速路径不牺牲个性化**：仅通用动作、营养和防护问题跳过首次 Agent 工具决策；包含“我的情况”、训练记录、报告或天气的请求仍走完整工具编排。

---

## 2. 完整调用链

```text
frontend/src/views/Chat.vue
  → POST /api/chat
  → app/api/routers/chat.py
  → ReactAgent.execute_stream(messages)
  → 通用知识问题：直接构建 RAG 上下文并调用一次答案模型
    其他问题：middleware.monitor_tool() 写入最近会话历史
  → agent_tools.rag_summarize()
  → RagSummarizeService.build_context()
       ├─ QueryPlanner
       ├─ Qdrant Dense（每个子查询）
       ├─ BM25（每个子查询）
       ├─ RRF / 去重 / LexicalReranker
       └─ ContextBuilder / [证据:N]
  → Agent 流式生成最终回答
  → SSE 返回文本、工具状态和 `evidence` 结构化事件
  → 前端将证据编号渲染为可展开来源卡片
```

| 文件 | 职责 |
|---|---|
| `app/services/query_planner.py` | 多轮指代消解与复合问题拆解；模型异常时回退原问题 |
| `app/services/rag_service.py` | 在线链路编排、并行混合召回、标签加权、RRF、去重、重排序和来源质量软惩罚；返回模型上下文和结构化命中证据 |
| `app/services/reranker.py` | V1 的确定性轻量重排序器；保留替换为 Cross-encoder/API 的边界 |
| `app/services/context_builder.py` | 在固定字符预算内优先保留命中子片段附近的父段文本 |
| `app/services/retrieval_contracts.py` | `RetrievalRequest`、`RetrievalHit`、`RetrievalResult` 稳定契约，以及查询标签和标签匹配得分 |
| `app/services/vector_store.py` / `vector_repository.py` | 查询向量化与 Qdrant 只读适配 |
| `app/services/bm25_retriever.py` | 读取 02 生成的 BM25 工件并执行关键词检索 |
| `app/services/agent_tools.py` | 将 Agent 工具参数、来源过滤和会话历史传给 RAG 服务；生成前端证据卡片和启动预热 |
| `app/services/middleware.py` | 在工具调用边界传递用户、城市和最近会话历史，并转发结构化证据 |

---

## 3. 查询规划

`QueryPlanner` 的输入是当前问题与最近最多 3 轮历史，输出 `QueryPlan`：

```python
QueryPlan(
    original_query="那深蹲呢？",
    rewritten_query="杠铃深蹲的标准动作和常见错误",
    search_queries=("杠铃深蹲的标准动作", "杠铃深蹲常见错误"),
    used_llm=True,
)
```

触发条件：

- 指代或省略问题，如“那深蹲呢”“它有什么好处”；
- 明显复合问题，如“新手减脂怎么练和怎么吃”。

模型必须返回 JSON，最多两个独立检索子查询；无效 JSON、模型超时或网络失败时，回退为原问题的单查询。查询规划不生成答案，也不允许引入用户消息中没有出现的事实。

---

## 4. 混合召回、融合与重排序

对每一个受控子查询，服务使用线程池并行执行：

```text
子查询 A ─┬─ Qdrant dense
          └─ BM25
子查询 B ─┬─ Qdrant dense
          └─ BM25
               ↓
      按 chunk_id 做 RRF 融合
               ↓
        父段重叠去重
               ↓
         有界重排序 → Top-K
```

Dense 和 BM25 仅在两者 revision 一致时融合；工件缺失或 revision 不一致时记录告警并降级为 Dense。

V1 的 `LexicalReranker` 以 RRF 分数为主、以查询词在命中 `child_text` 中的覆盖率为辅。它不增加模型部署、网络调用或运行时依赖，主要解决“同一候选集中关键词更贴切的资料排在后面”的问题。

评测发现，FitKG-CN 的原始三元组能补充长尾概念，但偶尔只因动作关键词相同而排在可直接回答问题的人工审核资料之前。因此最终排序支持按来源前缀配置小幅质量软惩罚：`external/fitkg-cn/` 当前为 8%。该规则不影响 Dense/BM25 召回、不删除外部资料；只在候选分数接近时降低低信息量三元组抢占 Top-1 的概率。每次评测报告会同时记录该排序配置，避免把不同在线配置下的分数误当作同一基线。

它不是 Cross-encoder。若后续需要接入 Cross-encoder 或云端 rerank API，只替换 `reranker.py` 的实现，
不改变 `RagSummarizeService`、Agent 工具或前端接口。

关键配置位于 `config/vector_store.yml`：

```yaml
k: 6
candidate_k: 15
query_history_turns: 3
max_subqueries: 2
reranker_enabled: true
reranker_candidate_k: 12
reranker_base_score_weight: 0.7
query_planner_enabled: true
metadata_tag_boost_enabled: true
metadata_tag_boost_weight: 0.15
source_quality_penalties:
  "external/fitkg-cn/": 0.08
```

`candidate_k` 仍保持 15。查询规划总开关关闭时，任何问题都保持原问题的单查询路径；开启时仍只有指代或复合问题会调用模型。标签加权使用离线和在线共享的确定性规则：查询标签与切片标签重合时，RRF 分数最多提升 15%，不命中或旧 revision 缺少标签时保持原顺序。来源质量惩罚仅作用于已重排序候选，未命中前缀时为 0。是否提高候选池、为标签建立 Qdrant payload 索引或替换为 Cross-encoder，属于基于实际使用和后续评测的调优，不在 V1 里预设结论。

---

## 5. 上下文预算与引用

Qdrant 和 BM25 都用子切片命中，但返回父段。为了避免六个长父段占满 Agent 上下文，
`ContextBuilder` 采用确定性策略：

1. 总上下文预算默认 6000 字符；
2. 单条证据最多 1200 字符；
3. 父段过长时，优先保留 `child_text` 附近内容；
4. 仍保留稳定 `evidence_id`、来源、切片序号与 `[证据:N]` 编号；
5. 预算耗尽时停止追加低排名证据，而不是调用额外 LLM 压缩。

Agent 获得的上下文示例：

```text
[证据:1] 来源=动作指南大全.txt | 证据ID=动作指南大全.txt#<chunk_id>
……与命中动作相关的父段内容……

证据目录：
[证据:1] 动作指南大全.txt#<chunk_id> | 来源=动作指南大全.txt
```

系统提示词要求 Agent 在采用该资料的结论后保留对应 `[证据:N]`，且不得虚构证据编号。

聊天接口不会通过解析最终模型文本来猜测来源。RAG 在构建上下文时同步返回真实 `RetrievalHit`；
SSE 发送裁剪后的 `evidence` 事件（证据编号、来源、命中子片段、标签），前端将其挂在当前回答下方的“证据来源”可展开卡片中。

---

## 6. 启动预热与延迟策略

后端启动时只加载 02 生成的 BM25 工件，不调用 embedding、Qdrant 查询或 LLM。这样首个 RAG 问题不再额外承担本地 BM25 建索引时间。

对明确的通用知识问题，`ReactAgent` 以保守关键词和排除规则直接进入 RAG：检索后仅调用一次模型生成最终回答；涉及个人画像、训练记录、报告、天气或户外场景时，继续由完整 Agent 决定需要的工具。该策略降低模型调用次数，但不绕过 RAG 证据、安全提示或引用规则。

---

## 7. 可观测性与功能验证

每次检索写入 `RAG_RETRIEVAL` 日志，记录：request ID、查询长度、子查询数量、revision、Dense/BM25 候选数、最终命中数、耗时、是否使用查询规划与是否发生回退。日志不记录原始用户问题。

当前功能测试覆盖：

- 普通问题不调用查询模型；
- 指代问题结合历史生成完整查询；
- 无效规划结果回退原查询；
- 两个子查询都会进入混合召回；
- 命中关键词的候选可经重排序前移；
- 长父段在预算内仍保留命中的子片段；
- RAG 工具可产生只含展示字段的结构化证据卡片；
- 通用知识问题进入快速路径，个性化问题保留完整 Agent 编排；
- `RagSummarizeService` 仅暴露结构化 `retrieve()` 和面向 Agent 的 `build_context()` 两个入口。

```powershell
.\.venv\Scripts\python.exe -m ruff format --check app
.\.venv\Scripts\python.exe -m ruff check app
.\.venv\Scripts\python.exe -m pytest app/tests
```

---

## 8. RAGAS 后置阶段

完整在线链路稳定后，再建立“问题、实际检索上下文、最终回答、人工参考答案”的黄金集，
引入 RAGAS 或等价工具评估：

- Context Precision / Recall：召回上下文是否相关且覆盖所需证据；
- Faithfulness：最终回答是否能由提供的上下文支持；
- Answer Accuracy / Relevance：最终回答是否正确回应问题。

RAGAS 使用 LLM 评审，存在成本、模型版本和提示词漂移；因此应固定评审模型、数据集 revision 和运行参数，并将报告与被测索引 revision 关联。它用于全链路质量验收和回归，不替代当前单元测试、错误处理或功能开发。

## 9. 不在 V1 范围内

- 外部搜索 fallback：需要单独明确来源授权、引用展示和安全审核边界；
- 用户反馈闭环：需要先定义数据保留与隐私策略；
- Qdrant 原生 sparse、集群、快照、DOCX/JSON/HTML 接入：属于 02 的数据与运维演进；
- 多 Agent 编排、长期会话摘要与意图路由：属于 04 Agent 对话演进。
