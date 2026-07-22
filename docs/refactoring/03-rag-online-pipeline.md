# 03 - RAG 在线管线重构方案

> **状态**: 待实施  
> **优先级**: P0（直接影响回答质量和用户感知速度）  
> **预计工时**: 5-7 天

---

## 一、现状诊断 vs 理想在线管线

### 1.1 在线管线完整对照表

| 环节 | 理想流程 | 当前实现 | 差距评估 |
|------|---------|---------|---------|
| **用户提问** | 用户输入 → 意图识别 → 路由分发 | Agent 直接调用 `rag_summarize` 工具 | 无意图分类，所有问题都走 RAG |
| **查询改写** | 指代消解 + 省略补全 + 多轮改写 | 无（单轮查询直接使用） | 多轮对话时指代丢失 |
| **查询构建** | 关键词提取 + 查询扩展 + 子查询拆分 | 仅同义词扩展 (`_expand_query`) | 复杂问题无法拆解 |
| **查询分发** | 按意图路由（RAG/工具/直接回答/闲聊） | 全部走 Agent 由 LLM 决定 | 简单问题也走检索 |
| **检索** | 向量+关键词+图谱多路，候选池 50+ | 向量+BM25 双路，candidate_k=15 (`rag_service.py:31`) | 候选太少，容易漏检 |
| **评估检索文档** | 相关性打分 + 阈值过滤 + 置信度评估 | 仅余弦相似度阈值 0.3 (`rag_service.py:32`) | 无相关性模型，阈值固定 |
| **查询分解/重写** | 检索质量低时自动拆分子问题或改写 | **无** | 一次检索失败就返回空 |
| **求助外部知识源** | 知识库无结果时 fallback web search | **无** | 知识盲区无法覆盖 |
| **重排序** | Cross-encoder 精排 | RRF 融合排序（粗排算法） | 缺少精排模型 |
| **文档压缩** | 冗余内容压缩、关键信息提取 | 无，原始 chunk 直接返回给 LLM | Context 被大量冗余填充 |
| **生成答案** | 多源证据融合 + 引用标注 | rag_summarize 拼接参考资料 (`rag_service.py:284-309`) | 无可溯源引用格式 |
| **答案评估** | 幻觉检测 + 事实核对 + 完整性检查 | **无** | 无法保证答案质量 |
| **反馈优化** | 用户反馈收集 + 检索策略调整 | **无** | 无闭环优化机制 |
| **返回答案** | 带引用 + 置信度 + 追问建议 | 前端手动渲染 Markdown | 无结构化输出 |

### 1.2 当前流程瓶颈

```python
# 当前流程 (rag_service.py retriever_docs)
用户提问
  → _expand_query (归一化 + 同义词)
  → ChromaDB 向量检索 (candidate_k=15)
  → BM25 关键词检索
  → RRF 融合排序
  → Jaccard 去重
  → Top-6 返回
```

**瓶颈分析**：
- 查询扩展仅做同义词替换，不会真正"改写"问题
- 候选池太小（15），RRF 融合前各路的有效候选可能不足
- 无精排，RRF 只考量排名不考量语义相关性
- 无压缩，可能返回大量冗余文本给 LLM
- 检索失败后无重试/降级机制
- 多轮对话无上下文传递

---

## 二、分步骤重构方案

### 步骤 1：查询改写（Query Rewriting）

**目标**：将用户的自然语言问题改写成更适合检索的查询。

**场景**：
- 多轮对话："那深蹲呢？" → "深蹲的标准动作是什么"
- 口语化："怎么减肚子" → "腹部减脂的训练方法"
- 指代消解："它有什么好处" → "深蹲有什么好处"

**方案**：引入 LLM 驱动的查询改写：

```python
# app/services/query_rewriter.py (新文件)

from app.services.factory import get_chat_model

QUERY_REWRITE_PROMPT = """你是一个搜索查询优化助手。根据对话历史，将用户当前问题改写成更精准的检索查询。

规则：
1. 补齐省略的主语和指代（"那个"→"深蹲"）
2. 将口语化表达转为专业术语
3. 拆解复合问题为多个简单子查询（用 | 分隔）
4. 只返回改写后的查询，不要解释

对话历史：
{history}

用户当前问题：{query}

改写后的查询："""

class QueryRewriter:
    def __init__(self):
        self._model = get_chat_model()
    
    def rewrite(self, query: str, history: list[dict] | None = None) -> str:
        """将用户问题改写为检索优化查询"""
        # 单轮查询：只用同义词扩展（快速，不走 LLM）
        if not history or len(history) < 2:
            return query
        
        # 多轮查询：走 LLM 指代消解
        history_text = "\n".join(
            f"{m['role']}: {m['content'][:200]}" 
            for m in history[-6:]  # 最近 3 轮
        )
        prompt = QUERY_REWRITE_PROMPT.format(history=history_text, query=query)
        
        try:
            result = self._model.invoke(prompt)
            rewritten = result.content.strip()
            logger.info(f"查询改写：{query} → {rewritten}")
            return rewritten
        except Exception as e:
            logger.warning(f"查询改写失败：{str(e)}，使用原始查询")
            return query
```

### 步骤 2：查询构建（Query Construction）

**目标**：将用户问题拆解成多个子查询，提高复杂问题的召回率。

**当前问题**："新手减脂需要做什么训练和怎么吃" — 一个查询无法同时命中训练和营养两个领域。

**方案**：

```python
# app/services/query_constructor.py (新文件)

import re

DECOMPOSE_TRIGGERS = [
    r"(.+)和(.+)",       # "A和B怎么"
    r"(.+)以及(.+)",     # "A以及B"
    r"(.+)还有(.+)",     # "A还有B"
    r"(.+)另外(.+)",    # "A另外B"
]

class QueryConstructor:
    @staticmethod
    def decompose(query: str) -> list[str]:
        """将复合查询拆解为独立子查询"""
        # 规则1：识别 "和/以及/还有/另外" 连接的子问题
        for pattern in DECOMPOSE_TRIGGERS:
            match = re.search(pattern, query)
            if match:
                # 简单启发式：拆成两个子查询
                sub1 = match.group(1).strip()
                sub2 = match.group(2).strip()
                # 补全语义
                if sub1 and sub2 and len(sub1) > 2 and len(sub2) > 2:
                    # 获取原始问题的开头部分作为上下文前缀
                    prefix = query[:match.start()].strip()
                    if prefix:
                        return [
                            f"{prefix}{sub1}",
                            f"{prefix}{sub2}",
                        ]
                    return [sub1, sub2]
        
        return [query]  # 无法拆解，返回原查询
    
    @staticmethod
    def construct(query: str) -> list[dict]:
        """
        构建查询列表，每个元素为 {"query": str, "source_filter": list | None}
        """
        sub_queries = QueryConstructor.decompose(query)
        result = []
        for sq in sub_queries:
            # 为每个子查询推断可能的 source 过滤
            source_hints = _infer_source_filter(sq)
            result.append({"query": sq, "source_filter": source_hints})
        return result

def _infer_source_filter(query: str) -> list[str] | None:
    """根据查询内容推断应检索的知识库来源"""
    domain_map = {
        "动作指南": ["动作", "姿势", "标准", "怎么做", "教学"],
        "营养学": ["吃", "营养", "蛋白", "碳水", "脂肪", "饮食"],
        "训练计划": ["计划", "每周", "安排", "频率", "周期"],
        "运动损伤预防": ["伤", "疼", "痛", "预防", "恢复"],
        "健身基础知识": ["基础", "新手", "入门", "原理", "概念"],
    }
    for source, keywords in domain_map.items():
        for kw in keywords:
            if kw in query:
                return [f"{source}.txt"]
    return None
```

### 步骤 3：检索增强（Retrieval Enhancement）

**目标**：扩大候选池 + 添加混合检索第三路。

**方案**：

**3.1 增大候选池**

```yaml
# config/chroma.yml
candidate_k: 30  # 从 15 提升到 30（粗召回不够细排没意义）
```

**3.2 添加基于标签的预过滤**

当查询匹配到已知领域标签时，优先检索该领域的文档：

```python
# app/services/rag_service.py retriever_docs 修改

def retriever_docs(self, query: str, source_filter: list[str] | None = None):
    # 如果 source_filter 为空，尝试用标签推断
    if not source_filter:
        source_filter = self._infer_tag_filter(query)
    
    # ... 后续保留 current_k 索引逻辑 ...
```

**3.3 异步并行检索**

```python
import asyncio

async def _async_vector_search(self, query: str, k: int) -> list[tuple]:
    """异步向量检索（不阻塞 BM25）"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: self.vector_store.vector_store.similarity_search_with_relevance_scores(query, k=k)
    )

async def _async_bm25_search(self, query: str, k: int) -> list[tuple]:
    """异步 BM25 检索"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: self.bm25.search(query, k=k))

async def retriever_docs_async(self, query: str, source_filter: list[str] | None = None):
    """异步双路并行检索"""
    self._ensure_collection_ready()
    self._sync_bm25_index()
    
    expanded_query = self._expand_query(query)
    
    # 并行执行两路检索
    vector_results, bm25_results = await asyncio.gather(
        self._async_vector_search(expanded_query, self.candidate_k),
        self._async_bm25_search(expanded_query, self.candidate_k),
        return_exceptions=True,
    )
    
    # 异常处理
    if isinstance(vector_results, Exception):
        logger.error(f"向量检索异常：{vector_results}")
        vector_results = []
    if isinstance(bm25_results, Exception):
        logger.error(f"BM25检索异常：{bm25_results}")
        bm25_results = []
    
    # 过滤 + 融合 + 去重 （与同步版本相同）
    # ...
```

### 步骤 4：检索文档评估（Retrieval Evaluation）

**目标**：判断检索结果的"可用性"，不可用时自动触发重试/降级。

**方案**：

```python
# app/services/retrieval_evaluator.py (新文件)

class RetrievalEvaluator:
    """评估检索结果质量，决策下一步动作"""
    
    MINIMUM_RETRIEVED_COUNT = 3       # 最少需要 3 条结果
    LOW_QUALITY_THRESHOLD = 0.3       # RRF 分数阈值
    
    @staticmethod
    def evaluate(scored_docs: list[tuple], original_query: str) -> dict:
        """
        返回评估结果：
        {
            "status": "ok" | "low_quality" | "no_results",
            "score": float,         # 整体检索质量分数 (0-1)
            "suggestion": str,      # 下一步建议
        }
        """
        if not scored_docs:
            return {
                "status": "no_results",
                "score": 0.0,
                "suggestion": "decompose_or_rewrite",  # 建议分解或改写查询
            }
        
        # 计算整体质量分：最高分 + 文档数量归一化
        scores = [s for _, s in scored_docs[:10]]
        max_score = max(scores) if scores else 0
        count_score = min(len(scored_docs) / 10, 1.0)  # 10条以上满分
        quality_score = (max_score * 0.7 + count_score * 0.3)
        
        if len(scored_docs) < RetrievalEvaluator.MINIMUM_RETRIEVED_COUNT:
            return {
                "status": "low_quality",
                "score": quality_score,
                "suggestion": "expand_sources_or_fallback",
            }
        
        if max_score < RetrievalEvaluator.LOW_QUALITY_THRESHOLD:
            return {
                "status": "low_quality",
                "score": quality_score,
                "suggestion": "rewrite_or_web_fallback",
            }
        
        return {
            "status": "ok",
            "score": quality_score,
            "suggestion": "proceed",  # 继续重排序
        }
```

### 步骤 5：查询分解/重写 & 外部知识源 Fallback

**目标**：检索质量低时自动降级流程。

**方案**：

```python
# app/services/rag_service.py 新增方法

def _retrieval_fallback(self, query: str, eval_result: dict) -> list[Document]:
    """检索降级策略"""
    suggestion = eval_result.get("suggestion", "proceed")
    
    if suggestion == "decompose_or_rewrite":
        # 策略1：拆解查询重新检索
        sub_queries = QueryConstructor.decompose(query)
        all_docs = []
        for sq in sub_queries:
            if sq != query:  # 避免死循环
                docs = self.retriever_docs(sq)
                all_docs.extend(docs)
        if all_docs:
            logger.info(f"查询拆解后检索到 {len(all_docs)} 篇文档")
            return all_docs
    
    elif suggestion == "rewrite_or_web_fallback":
        # 策略2：LLM 改写查询
        rewritten = QueryRewriter().rewrite(query)
        if rewritten and rewritten != query:
            docs = self.retriever_docs(rewritten)
            if docs:
                return docs
        
        # 策略3：标记需要外部知识（由 Agent 层处理 web search）
        logger.warning(f"知识库未检索到相关内容，建议 Agent 使用通用知识回答或提示用户")
    
    return []  # 所有降级策略失败
```

### 步骤 6：重排序（Reranking）

**目标**：使用 Cross-encoder 对融合结果进行精排。

**当前问题**：RRF 只是粗排算法（只考量排名，不考量语义相关性）。需要用 Cross-encoder 做最终精排。

**方案**：

```python
# app/services/reranker.py (新文件)

class CrossEncoderReranker:
    """使用 Cross-encoder 模型对候选文档精排"""
    
    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3"):
        # 方案A：使用 FlagEmbedding 的本地模型（推荐，速度快）
        # 方案B：使用 DashScope 的 rerank API（免部署，但网络开销）
        self._use_api = os.getenv("RERANKER_MODE", "local") == "api"
        
        if self._use_api:
            self._api_client = None  # DashScope rerank client
        else:
            try:
                from FlagEmbedding import FlagReranker
                self._model = FlagReranker(model_name, use_fp16=True)
                self._model_loaded = True
            except ImportError:
                logger.warning("FlagEmbedding 未安装，降级为 RRF 排序")
                self._model_loaded = False
    
    def rerank(self, query: str, docs: list[Document], top_k: int = 6) -> list[Document]:
        """对候选文档重排序并返回 top_k"""
        if len(docs) <= 1 or not self._model_loaded:
            return docs[:top_k]
        
        # 构建 query-doc 对
        pairs = [[query, doc.page_content] for doc in docs]
        
        # Cross-encoder 打分
        scores = self._model.compute_score(pairs, normalize=True)
        
        # 排序
        ranked = sorted(zip(docs, scores), key=lambda x: x[1], reverse=True)
        
        logger.info(
            f"精排完成：{len(docs)} → {min(top_k, len(docs))}，"
            f"最高分 {ranked[0][1]:.3f}"
        )
        
        return [doc for doc, _ in ranked[:top_k]]
```

### 步骤 7：文档压缩（Context Compression）

**目标**：在返回 LLM 之前压缩冗余内容，让 LLM 更聚焦关键信息。

**当前问题**：原始 chunk 直接拼接返回，可能包含大量不相关内容，浪费 token 且降低回答质量。

**方案**：

```python
# app/services/context_compressor.py (新文件)

from langchain.retrievers import ContextualCompressionRetriever
from langchain.retrievers.document_compressors import LLMChainExtractor

class ContextCompressor:
    """压缩检索文档：提取与 query 相关的核心句子"""
    
    COMPRESSION_PROMPT = """根据用户问题，从以下文档片段中仅提取直接相关的句子。
丢弃与问题无关的内容。保持原文不变，不要改写。

用户问题：{query}

文档片段：
{content}

相关句子（保留原文）："""
    
    def __init__(self):
        self._model = get_chat_model()
    
    def compress(self, query: str, docs: list[Document]) -> list[Document]:
        """对每个文档压缩无关内容"""
        if len(docs) <= 2:
            return docs  # 文档少时不压缩，避免丢失信息
        
        compressed = []
        for doc in docs:
            # 仅压缩超过 300 字的长文档
            if len(doc.page_content) < 300:
                compressed.append(doc)
                continue
            
            try:
                prompt = self.COMPRESSION_PROMPT.format(
                    query=query, content=doc.page_content
                )
                result = self._model.invoke(prompt)
                extracted = result.content.strip()
                
                if extracted and len(extracted) > 20:
                    new_doc = Document(
                        page_content=extracted,
                        metadata=doc.metadata.copy(),
                    )
                    # 标记为压缩版本
                    new_doc.metadata["compressed"] = True
                    # 保留原始长度比率
                    new_doc.metadata["compress_ratio"] = round(
                        len(extracted) / len(doc.page_content), 2
                    )
                    compressed.append(new_doc)
                else:
                    compressed.append(doc)  # 压缩失败，保留原文
            except Exception:
                compressed.append(doc)  # 异常时保留原文
        
        return compressed
```

### 步骤 8：答案生成增强

**目标**：生成带引用、可信度评估的答案。

**方案**：增强 `rag_summarize` 方法的输出格式：

```python
# app/services/rag_service.py rag_summarize 修改

def rag_summarize(self, query: str, source_filter: list[str] | None = None) -> str:
    context_docs = self.retriever_docs(query, source_filter)
    
    if not context_docs:
        return "[RAG]未检索到相关参考资料。"
    
    # 新增：参考来源追踪（用于 LLM 引用）
    references = []
    context_parts = []
    
    for i, doc in enumerate(context_docs, start=1):
        source = doc.metadata.get("source", "未知")
        # 使用 [ref:N] 标记，方便 LLM 在回答中引用
        ref_tag = f"[ref:{i}]"
        references.append(f"{ref_tag} {source}")
        
        context_parts.append(
            f"[资料{ref_tag}] {doc.page_content.strip()}"
        )
    
    context_text = "\n\n---\n".join(context_parts)
    ref_text = "\n".join(references)
    
    # 返回结构化上下文（让 LLM 知道如何引用）
    return (
        f"检索到 {len(context_docs)} 篇参考资料：\n\n"
        f"{context_text}\n\n"
        f"---\n引用标记说明（在回答中使用 [ref:N] 引用）：\n{ref_text}"
    )
```

### 步骤 9：反馈闭环

**目标**：收集用户反馈，优化检索策略。

**方案**：

```python
# app/services/feedback_collector.py (新文件)

class FeedbackCollector:
    """收集用户对 RAG 回答的反馈，用于优化检索策略"""
    
    def __init__(self):
        self._feedback_file = "logs/rag_feedback.jsonl"
    
    def record(self, query: str, docs_count: int, 
               user_rating: int | None = None,  # 用户评分 1-5
               was_useful: bool | None = None,   # 是否解决了问题
               resolution: str | None = None,     # "answered" | "web_fallback" | "gave_up"
               ):
        """记录一次检索反馈"""
        record = {
            "timestamp": datetime.now().isoformat(),
            "query": query,
            "docs_count": docs_count,
            "user_rating": user_rating,
            "was_useful": was_useful,
            "resolution": resolution,
        }
        with open(self._feedback_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    
    def get_low_performance_queries(self, min_occurrences: int = 3) -> list[str]:
        """获取高频低质量查询（用于改进同义词表/知识库）"""
        # 分析反馈日志，找出 was_useful=False 的高频查询
        # 返回需要改进的查询列表
        pass
```

---

## 三、在线管线重构后完整流程

```
用户提问
│
├── 1. 查询改写 ──────────────────────────────────
│   ├── 多轮对话 → LLM 指代消解/省略补全
│   ├── 单轮对话 → 仅同义词扩展（快速路径）
│   └── 输出：改写后的标准查询
│
├── 2. 查询构建 ──────────────────────────────────
│   ├── 复合问题拆解 → 多个独立子查询
│   ├── 领域推断 → source_filter 预过滤
│   └── 输出：查询列表 [{"query", "source_filter"}]
│
├── 3. 查询分发（Agent 决定） ────────────────────
│   ├── 闲聊/问候 → 直接回答（不走 RAG）
│   ├── 运动知识 → RAG 检索
│   ├── 工具调用 → 天气/用户画像/运动数据
│   └── 输出：执行路径
│
├── 4. 检索 (双路并行，candidate_k=30) ──────────
│   ├── 向量检索（ChromaDB）- 语义匹配
│   ├── BM25 检索 - 关键词匹配
│   ├── 标签过滤（tags 预过滤）
│   ├── 异步并行执行
│   └── 输出：粗排候选列表
│
├── 5. 评估检索文档 ──────────────────────────────
│   ├── 结果数量检查（≥3条）
│   ├── RRF 分数检查（max > 0.3）
│   ├── 决策：proceed / decompose / rewrite / web_fallback
│   └── 输出：{"status", "score", "suggestion"}
│
├── 6. 查询分解/重写 ────────────────────────────（评估不合格时）
│   ├── decompose → 拆分子查询 → 重新检索
│   ├── rewrite → LLM 改写 → 重新检索
│   ├── web_fallback → 标记需外部知识
│   └── 输出：重试后的文档列表或空
│
├── 7. 重排序 (Cross-encoder Reranker) ──────────
│   ├── BGE-Reranker-v2-m3 精排
│   ├── 本地模型（快）/ API（省部署）
│   └── 输出：Top-6 精排文档
│
├── 8. 文档压缩 ──────────────────────────────────
│   ├── LLM 提取相关句子
│   ├── 丢弃无关内容
│   └── 输出：压缩后的上下文
│
├── 9. 生成答案 ──────────────────────────────────
│   ├── 带 [ref:N] 引用标记
│   ├── 多源证据融合
│   └── 输出：结构化答案文本
│
├── 10. 答案评估 ─────────────────────────────────（可选）
│   ├── 幻觉检测（回答是否基于提供的文档）
│   ├── 完整性检查（是否回答了问题的所有方面）
│   └── 输出：质量标记
│
├── 11. 反馈优化 ─────────────────────────────────
│   ├── 记录用户反馈
│   ├── 分析低质量查询模式
│   └── 输出：改进建议
│
└── 12. 返回答案 ─────────────────────────────────
    └── SSE 流式输出给前端
```

---

## 四、实施检查清单

- [ ] 1. 创建 `app/services/query_rewriter.py`（LLM 查询改写）
- [ ] 2. 创建 `app/services/query_constructor.py`（查询拆解 + 领域推断）
- [ ] 3. 修改 `rag_service.py` `retriever_docs` 支持并行异步检索 + 增大候选池
- [ ] 4. 创建 `app/services/retrieval_evaluator.py`（检索质量评估）
- [ ] 5. 修改 `rag_service.py` 添加 `_retrieval_fallback`（降级策略）
- [ ] 6. 创建 `app/services/reranker.py`（Cross-encoder 精排）
- [ ] 7. 创建 `app/services/context_compressor.py`（文档压缩）
- [ ] 8. 修改 `rag_service.py` `rag_summarize`（结构化引用输出）
- [ ] 9. 创建 `app/services/feedback_collector.py`（反馈闭环）
- [ ] 10. `config/chroma.yml` `candidate_k` 调至 30
- [ ] 11. `requirements.txt` 添加 `FlagEmbedding` 依赖（精排模型）
- [ ] 12. 为每个新模块编写单元测试
- [ ] 13. 端到端检索质量对比测试（重构前后）

---

## 五、验收标准

1. 多轮对话中省略指代（"那深蹲呢"）能被正确改写为完整查询
2. 复合问题（"怎么吃和怎么练"）被拆解为子查询分别检索
3. 候选池从 15 扩展到 30，召回率提升 20%+
4. 检索质量低时自动降级（拆解→改写→标记降级），不返回空结果
5. Cross-encoder 精排后的 Top-3 相关性评分 > 0.7
6. 长文档被压缩至原始长度的 40-60%，保留核心信息
7. LLM 回答包含 `[ref:1] [ref:2]` 引用标记
8. 整体延迟 < 2 秒（异步并行 + 缓存优化）
