# Qdrant 原生混合检索与 RAG 简化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将受控 Markdown、TXT、PDF 知识库的检索路径收敛为一个 Qdrant collection 中的 dense + 原生 BM25 sparse 命名向量，并删除离线 BM25 工件、双请求并发、手写 RRF、网页式清洗和 Alembic。

**Architecture:** 索引命令显式重建唯一的 `fitagent_knowledge` collection；每个 chunk 同时写入 DashScope dense 向量和 Qdrant BM25 sparse 向量。在线请求只生成一次 dense 查询向量，然后通过一次 Qdrant `query_points(prefetch=[...], query=FusionQuery(RRF))` 获得最终结果；应用层只负责把结果转为证据和上下文。

**Tech Stack:** Python 3.13、FastAPI、SQLAlchemy、LangChain loaders/text splitters、qdrant-client 1.18、Qdrant 1.18、DashScope embeddings、pytest、Ruff。

## 阅读顺序与边界

### 最终形态（本计划的唯一目标）

```text
受控的 data/*.md | *.txt | *.pdf
  -> LangChain TextLoader / PyPDFLoader
  -> clean_text(): NFKC + 去 BOM/零宽字符 + 空白规范化
  -> MarkdownHeaderTextSplitter（仅 Markdown）
  -> RecursiveCharacterTextSplitter（所有来源）
  -> SHA-256 精确去重
  -> DashScope dense embedding + Qdrant/bm25 sparse document
  -> Qdrant: fitagent_knowledge

用户原始问题
  -> DashScope dense query embedding
  -> 一次 Qdrant Query API：dense prefetch + BM25 prefetch + RRF
  -> Top-K 证据
  -> ContextBuilder
```

collection 内部只有一种知识实体：**一个可引用的 chunk**。

```text
fitagent_knowledge point
├── id: UUID
├── vectors
│   ├── dense: DashScope embedding                 # 语义相近
│   └── sparse: Qdrant/bm25 Document               # 关键词匹配
└── payload
    ├── text: string
    ├── source_id: string
    ├── chunk_id: string
    ├── source_type: "md" | "txt" | "pdf"
    ├── ordinal: integer
    └── Markdown 标题或 PDF 页码等原始 loader 元数据
```

### 术语约定

- `sparse` 不是语义 embedding。它是一份稀疏的“词项 -> BM25 权重”向量，只有少数位置非零；Qdrant 用它做关键词排序。
- `dense` 是 DashScope 产生的浮点语义向量。
- `hybrid_search(...)` 是项目自己定义的仓储方法，不是 Qdrant SDK 的同名 API。其内部调用 Qdrant 的 `query_points(...)`。
- RRF 只按两条候选列表中的**排名**融合，不直接相加 dense cosine 分数和 BM25 分数。因此适合作为无训练数据时的起点；不得先加权或手调权重。

### 不做的事情

- 不保留 `rag_*` revision collection、alias、索引 manifest、回滚或新旧 schema 兼容层。
- 不支持网页抓取，所以不引入 `Unstructured`、HTML loader 或页面导航/版权/URL 去噪规则。
- 不保留 process-local `rank-bm25`、JSON BM25 artifact、SimHash、手写 RRF、并发双检索、查询规划、同义词扩展、词法 rerank、标签 boost、来源惩罚、在线 Jaccard 去重。
- 不在 FastAPI 启动时重建 Qdrant collection，也不在应用启动时 `drop_all()` 关系数据库。
- 不在本计划中删除 `MemoryFact` 或改变 mem0 的数据迁移语义；它们与“用 `create_all()` 代替 Alembic”是独立问题。

## Global Constraints

- **不兼容旧设计。** 执行前由操作者备份需要保留的数据；索引重建会删除唯一的 Qdrant collection，关系库切换到 `create_all()` 前必须使用空的、可丢弃的开发数据库。
- Qdrant collection 固定名为 `fitagent_knowledge`；dense 命名向量固定为 `dense`，BM25 sparse 命名向量固定为 `sparse`。
- 输入范围固定为 `data/` 下的 `.md`、`.txt`、`.pdf`；不扩展为网页采集。
- 文本清洗只保留 Unicode/空白规范化；源文件内容不正确时修源文件，不在代码中累积猜测性正则。
- 查询的 `source_filter` 必须同时传入两个 `Prefetch`，否则不允许的来源会占满候选池再被最终过滤。
- Qdrant BM25 使用中文 + multilingual tokenizer 配置；`bm25_avg_len` 只能依据标注检索集的评估结果调整，不能凭感觉设置。
- 关系表仍统一定义在 `app/models.py`；`Base.metadata.create_all()` 仅创建缺失表，**不会**修改既有列、索引或约束。

## 变更清单

| 文件 | 动作 | 最终职责 |
|---|---|---|
| `app/services/vector_repository.py` | 重写 | 唯一 collection 的创建、重建、双向量 upsert、单次 Qdrant 混合查询。 |
| `app/services/vector_store.py` | 简化 | 生成 dense query embedding 并委托 `hybrid_search`。 |
| `app/services/knowledge_indexer.py` | 重写 | 加载、最小规范化、LangChain 切块、SHA-256 精确去重、显式重建索引。 |
| `app/utils/file_handler.py` | 修改 | 集中保存唯一的 `clean_text()`。 |
| `app/services/rag_service.py` | 重写 | 原始 query -> `VectorStoreService.hybrid_search()` -> 证据 -> `ContextBuilder`。 |
| `app/services/retrieval_contracts.py` | 简化 | 仅暴露证据内容和最终 Qdrant score。 |
| `app/services/agent_tools.py`、`app/main.py` | 修改 | 移除 BM25 预热与相关启动动作。 |
| `config/vector_store.yml` | 重写 | 只保留 collection、向量、分块、检索、上下文和预检配置。 |
| `app/core/database.py`、`app/main.py` | 修改 | 显式初始化空数据库 schema。 |
| `app/services/knowledge_enrichment.py` | 删除 | 不再需要网页式清洗、SimHash 或标签生成。 |
| `app/services/bm25_retriever.py` | 删除 | BM25 由 Qdrant sparse vector 承担。 |
| `app/services/query_planner.py`、`app/services/reranker.py` | 删除 | 不再有查询重写或应用层重排序。 |
| `alembic/`、`alembic.ini` | 删除 | 不再使用迁移版本链。 |
| `app/tests/test_*rag*.py` 等 | 重写/删除 | 测试新契约，不测试已经删除的实现细节。 |
| `README.md`、`docs/learning-guide.md`、旧 migration 文档 | 修改/删除 | 不再教用户启动 Alembic 或描述旧检索路径。 |

## 目标接口

以下是后续任务必须共同遵守的最小边界。外部 SDK 类型仅停留在 `vector_repository.py`。

```python
# app/services/vector_repository.py
class VectorRepository(Protocol):
    def rebuild(self, chunks: list[IndexedChunk], dense_vectors: list[list[float]]) -> None: ...

    def hybrid_search(
        self,
        query: str,
        dense_vector: list[float],
        *,
        limit: int,
        candidate_limit: int,
        source_filter: tuple[str, ...] = (),
    ) -> list[ScoredChunk]: ...


# app/services/vector_store.py
class VectorStoreService:
    def hybrid_search(
        self, query: str, *, limit: int, source_filter: tuple[str, ...] = ()
    ) -> list[ScoredChunk]: ...
```

```python
# app/services/retrieval_contracts.py
@dataclass(frozen=True)
class RetrievalHit:
    evidence_id: str
    source_id: str
    chunk_id: str
    text: str
    rank: int
    score: float
    metadata: dict[str, str | int | float | bool]


@dataclass(frozen=True)
class RetrievalResult:
    request: RetrievalRequest
    hits: tuple[RetrievalHit, ...]
    elapsed_ms: int
```

## Task 1: 建立 Qdrant 单 collection 的 native hybrid repository

**Files:**
- Modify: `app/services/vector_repository.py`
- Modify: `app/tests/test_vector_repository.py`
- Modify: `config/vector_store.yml`

**Consumes:** `IndexedChunk(chunk_id, text, metadata)` 和 DashScope 已生成的 `list[list[float]]`。

**Produces:** `QdrantVectorRepository.rebuild(...)` 与 `QdrantVectorRepository.hybrid_search(...)`；后续索引器和在线服务只依赖这两个方法。

- [ ] **Step 1: 先写 repository 的失败测试。**

  用假 `QdrantClient` 捕捉参数，验证 collection 同时具有 `dense` 与 `sparse` 命名向量；验证每个 point 的 vector dict 中有两种向量；验证搜索只调用一次 `query_points`，其中有两个 `Prefetch`，外层 query 为 `FusionQuery(fusion=RRF)`，且 source filter 同时位于两个 prefetch。

  ```python
  def test_hybrid_search_uses_two_prefetches_and_one_rrf_query():
      client = CapturingClient()
      repository = QdrantVectorRepository("fitagent_knowledge", "http://unused", client=client)

      repository.hybrid_search(
          "深蹲膝盖内扣",
          [0.1, 0.2],
          limit=6,
          candidate_limit=15,
          source_filter=("动作.md",),
      )

      assert client.query_points_calls == 1
      assert len(client.kwargs["prefetch"]) == 2
      assert client.kwargs["query"].fusion == models.Fusion.RRF
      assert all(prefetch.filter is not None for prefetch in client.kwargs["prefetch"])
  ```

- [ ] **Step 2: 实现 collection schema 与显式 rebuild。**

  `rebuild()` 是索引命令专用的破坏性操作：调用一次 `recreate_collection`，建立 `source_id` keyword payload index，再批量 upsert。不要在 `health()`、查询方法或 FastAPI lifespan 调用它。

  ```python
  self.client.recreate_collection(
      collection_name=self.collection_name,
      vectors_config={
          "dense": models.VectorParams(size=dense_size, distance=models.Distance.COSINE),
      },
      sparse_vectors_config={
          "sparse": models.SparseVectorParams(modifier=models.Modifier.IDF),
      },
  )

  point = models.PointStruct(
      id=chunk.chunk_id,
      vector={
          "dense": dense_vector,
          "sparse": models.Document(
              text=chunk.text,
              model="Qdrant/bm25",
              options=models.Bm25Config(
                  tokenizer=models.TokenizerType.MULTILINGUAL,
                  language="chinese",
                  avg_len=self.bm25_avg_len,
              ),
          ),
      },
      payload={"text": chunk.text, **chunk.metadata},
  )
  ```

  `bm25_avg_len` 从 `config/vector_store.yml` 读取，初始值为 `256`；它是可评估的检索参数，而不是隐藏常量。

- [ ] **Step 3: 实现一次 Query API 混合检索。**

  不自行并行，也不取回两组结果后手写融合。两个 prefetch 由 Qdrant 在一次请求内执行，外层 RRF 产生最终排序。

  ```python
  query_filter = _source_filter(source_filter)  # () 时返回 None
  response = self.client.query_points(
      collection_name=self.collection_name,
      prefetch=[
          models.Prefetch(
              query=dense_vector,
              using="dense",
              filter=query_filter,
              limit=candidate_limit,
          ),
          models.Prefetch(
              query=models.Document(
                  text=query,
                  model="Qdrant/bm25",
                  options=self._bm25_config(),
              ),
              using="sparse",
              filter=query_filter,
              limit=candidate_limit,
          ),
      ],
      query=models.FusionQuery(fusion=models.Fusion.RRF),
      limit=limit,
      with_payload=True,
      with_vectors=False,
  )
  ```

  `ScoredChunk` 只从 `response.points` 构造；payload 的 `text` 是 `Document.page_content`，其余 payload 是 metadata。不得把 Qdrant 的 `ScoredPoint` 传出仓储层。

- [ ] **Step 4: 运行目标测试。**

  ```bash
  ./.venv/bin/python -m pytest app/tests/test_vector_repository.py -q -p no:cacheprovider
  ```

  预期：测试覆盖命名向量 schema、双向量写入、一个 Query API 请求、RRF 和双 prefetch filter。

- [ ] **Step 5: Commit。**

  ```bash
  git add app/services/vector_repository.py app/tests/test_vector_repository.py config/vector_store.yml
  git commit -m "refactor: use qdrant native hybrid retrieval"
  ```

## Task 2: 将离线知识处理收敛为 LangChain 分块和精确去重

**Files:**
- Modify: `app/utils/file_handler.py`
- Modify: `app/services/knowledge_indexer.py`
- Modify: `app/tests/test_markdown_knowledge_indexer.py`
- Delete: `app/services/knowledge_enrichment.py`
- Delete: `app/tests/test_knowledge_enrichment.py`

**Consumes:** `TextLoader`、`PyPDFLoader` 返回的 LangChain `Document`，以及 Task 1 的 `repository.rebuild`。

**Produces:** 一份没有网页式清洗、父子双层切片、SimHash、标签或 artifacts 的 `list[IndexedChunk]`。

- [ ] **Step 1: 先测试最小规范化。**

  `clean_text()` 是唯一清洗入口：验证 NFKC、BOM、零宽字符、换行和连续空行；不要再测试 URL、页码、版权提示或重复行的猜测性删除。

  ```python
  def test_clean_text_normalizes_unicode_and_whitespace():
      assert clean_text("\ufeffＡ\u200b\r\n\r\n  深蹲\t训练  ") == "A\n\n深蹲 训练"
  ```

- [ ] **Step 2: 实现唯一的 `clean_text()`。**

  在 `app/utils/file_handler.py` 修改既有函数而非新增第二个 cleaner：先 `unicodedata.normalize("NFKC", text)`，再移除 `\ufeff` 与 `\u200b`，统一换行、行内空白和最多一个空行。`normalize_documents()` 继续在 loader 后立刻调用它。

- [ ] **Step 3: 删除双层 chunk 与内容增强。**

  `KnowledgeIndexer` 只保留：

  1. `MarkdownHeaderTextSplitter`：Markdown 按 `#`、`##` 保存标题元数据；
  2. `RecursiveCharacterTextSplitter`：对上述 section 和 TXT/PDF 文档切最终 chunk；
  3. 一个局部 `seen_hashes: set[str]`：对最终 `chunk.text` 的 UTF-8 `sha256` 去重；
  4. `uuid5(_INDEX_NAMESPACE, f"{source}:{document_ordinal}:{chunk_ordinal}")`：产生本次 rebuild 可用的 point ID。

  删除 `_split_parent_documents`、`parent_text`、`parent_id`、`DeepTextCleaner`、`ContentDeduplicator`、`MetadataEnricher`、`_build_revision`、`_attach_index_revision`、`_write_artifacts` 和 `IndexBuildResult.revision`。

  最终 metadata 只保留以下内容及 loader 已有的标量元数据：

  ```python
  metadata = {
      "chunk_id": chunk_id,
      "source_id": source,
      "source_type": Path(source).suffix.lstrip(".").lower(),
      "ordinal": len(chunks),
      **scalar_loader_metadata,
  }
  ```

- [ ] **Step 4: 让构建过程只做一件可见的破坏性操作。**

  `build()` 先完成 source count/chunk count 预检和所有 dense embedding，再调用一次 `repository.rebuild(chunks, vectors)`；如果 embedding 失败，Qdrant collection 保持旧内容。不要创建临时 revision collection、写 alias 或写 JSON artifact。

  ```python
  chunks = self.preflight()
  vectors = get_embedding_model().embed_documents([chunk.text for chunk in chunks])
  self.repository.rebuild(chunks, vectors)
  return IndexBuildResult(collection_name="fitagent_knowledge", chunk_count=len(chunks))
  ```

- [ ] **Step 5: 重写索引测试并移除旧测试。**

  保留并覆盖 Markdown 标题分块、PDF/TXT loader、空 source/chunk 门槛、精确 hash 去重和 embedding 失败时**不调用** `repository.rebuild`。删除网页导航清洗、SimHash、标签、parent context、revision/alias/artifact 的断言。

- [ ] **Step 6: 运行目标测试。**

  ```bash
  ./.venv/bin/python -m pytest app/tests/test_markdown_knowledge_indexer.py -q -p no:cacheprovider
  ./.venv/bin/python -m ruff check app/services/knowledge_indexer.py app/utils/file_handler.py
  ```

- [ ] **Step 7: Commit。**

  ```bash
  git add app/utils/file_handler.py app/services/knowledge_indexer.py app/tests/test_markdown_knowledge_indexer.py
  git rm app/services/knowledge_enrichment.py app/tests/test_knowledge_enrichment.py
  git commit -m "refactor: simplify knowledge indexing pipeline"
  ```

## Task 3: 删除应用层检索编排，保留最小证据契约

**Files:**
- Modify: `app/services/vector_store.py`
- Modify: `app/services/rag_service.py`
- Modify: `app/services/retrieval_contracts.py`
- Modify: `app/services/context_builder.py`
- Modify: `app/services/agent_tools.py`
- Modify: `app/main.py`
- Modify: `app/tests/test_rag_service.py`
- Modify: `app/tests/test_online_rag_pipeline.py`
- Modify: `app/tests/test_agent_rag_context.py`
- Modify: `app/tests/test_agent_runtime_context.py`
- Delete: `app/services/bm25_retriever.py`
- Delete: `app/services/query_planner.py`
- Delete: `app/services/reranker.py`
- Delete: `app/tests/test_bm25_retriever.py`

**Consumes:** Task 1 的 `VectorRepository.hybrid_search()` 和已有 `ContextBuilder`。

**Produces:** 对调用方稳定的 `RagContext(content, result)`；每个 hit 只有最终排名与最终 Qdrant score。

- [ ] **Step 1: 先写“单调用”测试。**

  使用 fake `VectorStoreService`，断言 `RagSummarizeService.retrieve()` 只向它传一次用户原始 query；不接受 history，不构造子查询，不加载工件，不创建线程池。

  ```python
  def test_retrieve_delegates_original_query_once():
      store = CapturingVectorStore()
      result = RagSummarizeService(vector_store=store).retrieve("深蹲时膝盖怎么放？")

      assert store.calls == [("深蹲时膝盖怎么放？", 6, ())]
      assert result.hits[0].rank == 1
      assert result.hits[0].source_id == "动作.md"
  ```

- [ ] **Step 2: 简化 `VectorStoreService`。**

  它只嵌入 query 一次并转调 repository。删除 `similarity_search()` 和 `active_revision()`。

  ```python
  def hybrid_search(self, query: str, *, limit: int, source_filter: tuple[str, ...] = ()):
      dense_vector = get_embedding_model().embed_query(query)
      return self.repository.hybrid_search(
          query,
          dense_vector,
          limit=limit,
          candidate_limit=self.candidate_limit,
          source_filter=source_filter,
      )
  ```

- [ ] **Step 3: 简化 `RagSummarizeService`。**

  `__init__` 只注入 `VectorStoreService` 与 `ContextBuilder`。`retrieve()` 计时、调用一次 `hybrid_search()`、将 `ScoredChunk` 映射成 `RetrievalHit`、按返回顺序赋 rank。`build_context()` 继续复用 `ContextBuilder` 的预算和证据标识功能。

  删除 `ThreadPoolExecutor`、`BM25Retriever`、`QueryPlanner`、`LexicalReranker`、`MetadataEnricher`、同义词配置、`_rrf_fusion`、`_deduplicate_docs`、标签 boost 与来源惩罚。

- [ ] **Step 4: 收缩证据契约与消费者。**

  从 `RetrievalHit` 删除 `parent_id`、`child_text`、`dense_rank`、`bm25_rank`、`rerank_score`、`metadata_tag_score`、`source_quality_penalty`；从 `RetrievalResult` 删除 expanded query、subquery、revision、两路候选数、BM25 和 planner 诊断。更新 `ContextBuilder` 只使用 `hit.text`。

- [ ] **Step 5: 移除启动预热。**

  删除 `warm_rag_retriever()` 以及 `app/main.py` lifespan 中的 `asyncio.to_thread(warm_rag_retriever)`。线上请求不应该读取本地 JSON，也不应该在启动时构造 Python BM25 索引。

  同时在 `agent_tools.py` 中将证据卡片摘要改为 `hit.text`，其 score 直接使用 `hit.score`；不得再读取 `child_text` 或 `rerank_score`。

- [ ] **Step 6: 运行目标测试。**

  ```bash
  ./.venv/bin/python -m pytest \
    app/tests/test_rag_service.py \
    app/tests/test_online_rag_pipeline.py \
    app/tests/test_agent_rag_context.py \
    app/tests/test_agent_runtime_context.py -q -p no:cacheprovider
  ```

- [ ] **Step 7: Commit。**

  ```bash
  git add app/services/vector_store.py app/services/rag_service.py app/services/retrieval_contracts.py app/services/context_builder.py app/services/agent_tools.py app/main.py app/tests/
  git rm app/services/bm25_retriever.py app/services/query_planner.py app/services/reranker.py app/tests/test_bm25_retriever.py
  git commit -m "refactor: reduce rag to qdrant hybrid query"
  ```

## Task 4: 用 models + `create_all()` 取代 Alembic

**Files:**
- Modify: `app/core/database.py`
- Modify: `app/main.py`
- Modify: `app/tests/test_bootstrap.py`
- Delete: `alembic/`
- Delete: `alembic.ini`
- Delete: `app/tests/test_local_agent_run_logging_migrations.py`
- Modify: `pyproject.toml`
- Modify: `README.md`
- Modify: `docs/learning-guide.md`
- Delete: `docs/superpowers/plans/2026-09-04-local-agent-run-logging.md`
- Modify: any remaining files found by `rg -n "alembic" .`

**Consumes:** `Base` 与 SQLAlchemy engine；全部 ORM model 必须仍由 `app/models.py` 定义。

**Produces:** 一个仅适用于新建/可丢弃数据库的 schema 初始化路径。

- [ ] **Step 1: 先写 schema bootstrap 测试。**

  使用 sqlite memory engine，确认 `initialize_schema()` 创建 `users`、`sessions`、`agent_runs` 等 model 表；不要断言已存在表被 ALTER。

  ```python
  def test_initialize_schema_creates_all_models(monkeypatch):
      engine = create_engine("sqlite+pysqlite:///:memory:")
      monkeypatch.setattr(database, "engine", engine)

      database.initialize_schema()

      assert "users" in inspect(engine).get_table_names()
      assert "agent_runs" in inspect(engine).get_table_names()
  ```

- [ ] **Step 2: 实现显式的 schema 初始化。**

  `initialize_schema()` 先执行既有的 `ensure_database_exists()`，显式 import `app.models` 以注册 mappings，最后执行 `Base.metadata.create_all(bind=engine)`。它不得调用 `drop_all()`。

  ```python
  def initialize_schema() -> None:
      ensure_database_exists()
      import app.models  # noqa: F401
      Base.metadata.create_all(bind=engine)
  ```

  在 FastAPI lifespan 的最早阶段调用一次 `initialize_schema()`；应用启动失败应暴露真实连接/权限错误，不能静默继续。

- [ ] **Step 3: 移除迁移系统而不是保留空壳。**

  删除 `alembic/`、`alembic.ini`、`alembic` dependency 和只验证历史 migration 文件存在的测试。更新 README 的 macOS、Windows、升级说明和项目树，使用户只需启动数据库后运行应用即可建空 schema。

  迁移前的人工操作写入 README：**现有数据库必须先备份并由操作者删除/新建为开发数据库；`create_all()` 不是升级工具。**

- [ ] **Step 4: 运行目标测试与静态检查。**

  ```bash
  ./.venv/bin/python -m pytest app/tests/test_bootstrap.py app/tests/test_database_session.py -q -p no:cacheprovider
  ./.venv/bin/python -m ruff check app/core/database.py app/main.py app/models.py
  rg -n -i "alembic" README.md app config pyproject.toml docs \
    --glob '!docs/superpowers/plans/**' || true
  ```

  预期：前两条命令通过；最后一条不返回运行时或安装文档中的 Alembic 引用。

- [ ] **Step 5: Commit。**

  ```bash
  git add app/core/database.py app/main.py app/tests/test_bootstrap.py app/tests/test_database_session.py pyproject.toml README.md docs/
  git rm -r alembic alembic.ini app/tests/test_local_agent_run_logging_migrations.py docs/superpowers/plans/2026-09-04-local-agent-run-logging.md
  git commit -m "refactor: initialize database schema from models"
  ```

## Task 5: 收缩配置、依赖、文档与评估基线

**Files:**
- Modify: `config/vector_store.yml`
- Modify: `pyproject.toml`
- Modify: `README.md`
- Modify: `docs/learning-guide.md`
- Modify: `app/evaluation/retrieval_evaluator.py`
- Modify: `app/evaluation/retrieval_cases.json`
- Modify: `app/tests/test_retrieval_evaluator.py`

**Consumes:** Tasks 1–4 的新 API 与真实受控知识源。

**Produces:** 可理解、可配置、可评估的最小方案；不是靠隐藏 weights 的“调参方案”。

- [ ] **Step 1: 将 `config/vector_store.yml` 收敛为以下字段。**

  ```yaml
  backend: qdrant
  url: http://localhost:6333
  grpc_port: 6334
  prefer_grpc: true
  qdrant_timeout_seconds: 60

  collection_name: fitagent_knowledge
  dense_vector_name: dense
  sparse_vector_name: sparse
  sparse_model: Qdrant/bm25
  sparse_language: chinese
  sparse_tokenizer: multilingual
  bm25_avg_len: 256

  data_path: data
  allow_knowledge_file_type: ["txt", "md", "pdf"]
  chunk_size: 500
  chunk_overlap: 80
  batch_size: 32
  min_source_count: 1
  min_chunk_count: 1
  k: 6
  candidate_k: 15
  max_context_chars: 6000
  max_chars_per_evidence: 1200
  evaluation_cases_path: app/evaluation/retrieval_cases.json
  ```

  删除 alias、prefix、manifest、artifact、revision、parent chunk、SimHash、planner、reranker、同义词、tag 和 source penalty 配置。

- [ ] **Step 2: 移除无消费者依赖。**

  从 `pyproject.toml` 删除 `rank-bm25` 和 `alembic`。不要因为没有网页输入而新增 `unstructured` 或 HTML 解析依赖。保留现有 LangChain document loader / text splitter 依赖。

- [ ] **Step 3: 重写检索评估为结果质量门槛。**

  评估数据保留 query 与期望 `source_id`/`chunk_id`，计算 `Recall@6` 与 MRR；不要断言 dense/BM25 的内部 rank 或人为加权。建立至少以下三类中文 case：精确术语（关键词价值）、同义表述（dense 价值）、混合问题（RRF 价值）。

  ```python
  assert report.recall_at_k >= 0.90
  assert report.mrr >= 0.70
  ```

  这些数值是合并前基线门槛；若现有标注集太小，先补至少 20 条人工标注问题，再决定是否修改 `bm25_avg_len` 或 chunk 参数。

- [ ] **Step 4: 更新 README 的运行与架构说明。**

  README 必须明确：

  1. 首次执行 `docker compose up -d qdrant` 后运行 `python -m app.services.knowledge_indexer` 会**重建** `fitagent_knowledge`；
  2. 应用启动只创建缺失关系表，不重建知识库；
  3. RAG 是“一次 Qdrant Query API 的 dense + BM25 prefetch + RRF”；
  4. 清洗范围是受控文件的 Unicode/空白规范化，不是网页内容清洗。

- [ ] **Step 5: Commit。**

  ```bash
  git add config/vector_store.yml pyproject.toml README.md docs/ app/evaluation/ app/tests/test_retrieval_evaluator.py
  git commit -m "docs: document simplified hybrid rag architecture"
  ```

## Task 6: 端到端验收与人工切换

**Files:**
- Modify only if a verification uncovers a real defect in Tasks 1–5.

- [ ] **Step 1: 在可丢弃环境清理旧索引和关系库。**

  此步骤具有破坏性，执行者先确认已备份所需数据。不要把删除命令写进应用代码、Makefile 或启动脚本。

  ```bash
  docker compose up -d qdrant
  # 使用新的、空的开发 MySQL database；不要对未知数据库执行 DROP。
  ```

- [ ] **Step 2: 构建唯一 collection。**

  ```bash
  ./.venv/bin/python -m app.services.knowledge_indexer
  ```

  预期日志：collection 为 `fitagent_knowledge`，point count 等于预检后的 chunk count，没有 alias/revision/artifact 写入。

- [ ] **Step 3: 运行 Qdrant 真机 smoke test。**

  以真实 Qdrant 和真实 embedding 跑三条已标注中文问题，检查每个查询均返回证据，并手工确认精确动作术语、营养术语和改述问题至少各命中一条预期 source。记录请求日志，确认每条问题只出现一次 `query_points` 调用。

- [ ] **Step 4: 运行完整测试和质量门禁。**

  ```bash
  ./.venv/bin/python -m pytest app/tests -q -p no:cacheprovider
  ./.venv/bin/python -m ruff check app
  ./.venv/bin/python -m ruff format --check app
  ./.venv/bin/python -m app.evaluation.retrieval_evaluator
  ```

  预期：无已删除模块 import；所有可连接依赖的测试通过；检索评估达到 Task 5 的门槛。若 MySQL/DashScope 在 CI 中不可用，使用明确的 mock/integration 标记隔离外部依赖，不能把连接失败伪装成成功。

- [ ] **Step 5: 最终变更复核并 Commit。**

  ```bash
  git diff --check
  git status --short
  git add -A
  git commit -m "refactor: simplify fitagent retrieval architecture"
  ```

## 实施后应当成立的断言

```text
Qdrant collection 数量（RAG）     = 1
RAG collection 名称                = fitagent_knowledge
每个 point 的检索表示              = dense + sparse
每个用户问题的 Qdrant 查询次数     = 1
应用层 BM25 / RRF 实现              = 0
离线 JSON BM25 artifact            = 0
网页清洗规则 / SimHash / tags       = 0
索引切换 alias / revision collection = 0
Alembic 运行时依赖和文件            = 0
自动重建索引 / 自动 drop database   = 0
```

## 决策理由与风险控制

1. **为什么用 RRF：** dense cosine 与 BM25 的分值量纲不同。RRF 使用排名而非原始分数，是无标注数据时的合理基线；不应先做线性加权融合。
2. **为什么 sparse 仍叫“向量”：** 倒排/词项权重以稀疏向量表示，绝大多数维度为零；它保留关键词能力，不承担语义理解。
3. **为什么不替换为 LangChain “清洗 API”：** LangChain 在本项目中已经适合承担 loader 和 splitter；它没有能正确替代中文受控知识库规范化与去重的通用 API。增加 Unstructured 只会为当前输入范围引入依赖与不确定性。
4. **为什么保留 `clean_text()`：** NFKC、BOM、零宽字符和空白规范化是确定性的输入卫生，不是业务排名逻辑。
5. **为什么只做 SHA-256：** 受控文件由人维护，精确重复足以避免重复 chunk；近似去重可能错误删除语义相近但并不相同的训练安全建议。
6. **为什么 `create_all()` 有边界：** 它只适合新建 schema。以后如果需要保留生产数据的结构演进，再单独恢复一套迁移策略；不要假装它能安全 ALTER 既有数据库。

## 参考资料

- [Qdrant Hybrid Queries](https://qdrant.tech/documentation/search/hybrid-queries/)
- [Qdrant Named Vectors](https://qdrant.tech/documentation/concepts/vectors/)
- [Qdrant BM25 Inference](https://qdrant.tech/documentation/inference/inference-bm25/)
- [LangChain document loaders](https://docs.langchain.com/oss/python/integrations/document_loaders/index)
- [LangChain text splitters](https://docs.langchain.com/oss/python/integrations/splitters/index)
