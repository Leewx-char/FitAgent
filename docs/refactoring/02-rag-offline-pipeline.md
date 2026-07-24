# 02 - RAG 离线知识管线

> 状态：代码已完成；执行一次索引构建后，当前 Qdrant 才会切换到包含本方案能力的新 revision。
>
> 本文描述当前实现，而非待选技术方案。旧版 Chroma、启动时自愈写入和在线重建均不再属于本架构。

## 1. 目标与边界

本项目是健身知识 RAG Demo。离线管线的目标是用可重复构建、可验证和可回滚的方式，将本地知识资料发布为 Qdrant 中的活动索引。

本阶段已经实现：

- Qdrant 不可变 revision 集合与 `rag_active` 原子别名切换；
- TXT、Markdown、PDF 加载，以及 Markdown 标题感知切分；
- 深度去噪、精确去重、SimHash 近重复去重；
- 父子切片：子切片检索，父段作为 RAG 上下文；
- 本地规则主题标签；
- Dense Qdrant 检索与离线 BM25 工件的混合检索；
- 写入数量校验、构建进度、失败清理和发布清单。

本阶段不实现：DOCX / JSON / HTML 自动加载、Qdrant 原生 sparse、集群、快照策略和 LLM 元数据生成。它们的触发条件见第 10 节。

## 2. 架构

```text
data/（TXT / MD / PDF）
  │
  ├─ 文件 SHA256、加载、基础规范化、FAQ 拆分
  ├─ Markdown 标题切分 / 大窗口父段切分
  ├─ 深度去噪、子切片、内容去重
  ├─ 本地标签增强
  ├─ DashScope 批量 embedding
  └─ 写入 Qdrant revision 集合并校验数量
       │
       ├─ 原子切换 rag_active 别名
       ├─ storage/rag/index_manifest.json
       └─ storage/rag/bm25_documents.json

在线请求
  ├─ Dense：查询 embedding → Qdrant 子切片命中 → 返回父段
  ├─ BM25：子切片关键词命中 → 返回父段
  └─ RRF 融合、去重、返回 Top-K 上下文
```

在线 RAG 只读活动别名和 BM25 工件，绝不在 API 请求中导入资料、重建索引或切换 revision。

## 3. 关键目录与职责

| 位置 | 职责 |
|---|---|
| `app/services/knowledge_indexer.py` | 离线构建入口、revision 发布、构建校验 |
| `app/services/knowledge_enrichment.py` | 深度清洗、内容去重、标签规则 |
| `app/services/vector_repository.py` | Qdrant 集合、别名、向量写入与检索适配 |
| `app/services/vector_store.py` | 在线 dense 查询服务 |
| `app/services/bm25_retriever.py` | 离线 BM25 工件加载与查询 |
| `app/services/rag_service.py` | Dense/BM25 融合和上下文组装 |
| `config/vector_store.yml` | 切片、Qdrant、去重与增强参数 |
| `config/knowledge_sources.yml` | 数据来源、许可和纳入状态 |
| `storage/rag/` | 运行时 manifest、BM25 工件和评测报告，默认不提交 Git |

## 4. 离线构建流程

入口命令：

```powershell
.\.venv\Scripts\python.exe -m app.services.knowledge_indexer
```

### 4.1 加载与版本输入

构建器递归扫描 `data/`，目前允许 `.txt`、`.md`、`.pdf`。每个来源文件先计算 SHA256，再加载并做基础空白规范化；识别为 FAQ 的文本会拆成独立问答文档。

revision 由以下内容计算：来源文件 SHA256、切片参数、父段参数、去重阈值、标签参数、embedding 模型名称与索引 schema 版本。任一项变化都会生成新的 `rag_<revision 前 12 位>` 集合。

这保证“同一份来源 + 同一套配置”产生可预期的版本，而不是覆盖旧集合。

### 4.2 父子切片

Markdown 先按 `#`、`##` 切出章节；没有章节标题的文档前言不单独入库。TXT 和 PDF 直接按递归分隔符切分。

接着使用两层窗口：

| 类型 | 默认参数 | 用途 |
|---|---:|---|
| 父段 | 1200 字符，重叠 160 | 保留更完整的上下文，交给 RAG/LLM |
| 子切片 | 500 字符，重叠 80 | 生成向量和 BM25 索引，用于精准命中 |

每个父段有稳定 `parent_id`。每个子切片保存 `parent_id` 与 `parent_text`：命中向量的仍是子文本，但返回给上层的是父段。原始命中子文本保留为 `child_text`，用于审计与调试。

### 4.3 深度去噪与内容级去重

`DeepTextCleaner` 在父段和子切片两个阶段运行，处理：

- Unicode NFKC、BOM、零宽字符、换行和空白；
- 单独页码、`第 X 页`、URL；
- 常见网页导航、版权声明、免责声明和分隔线；
- 相邻重复行。

去重器在本次构建的全部子切片范围内运行：

1. 去掉空白与标点后计算 SHA256，过滤精确重复；
2. 对其余文本按中文连续字符三元组计算 64 位 SimHash；
3. 用四个 16 位 band 缩小候选集合，再计算汉明距离；
4. 距离不大于 `near_duplicate_hamming_distance`（默认 3）时，过滤为近重复。

这是一条保守规则，不等价于“语义完全相同”。短文本或大幅同义改写仍需要评测集来观察误判与漏判。

### 4.4 标签元数据

每个子切片在离线阶段根据标题和正文中的关键词生成 `tags`，包括动作、营养、防护、上肢、下肢、核心等标签，最多 4 个。

此实现不调用 LLM，因此构建不会因标签增加外部 API 成本。在线检索会用同一套规则识别查询标签，并以小幅分数加成提升同标签候选；它不是强制过滤，也不是医学或运动学分类标准。没有消费者的 `summary` 字段不再生成或写入索引。

### 4.5 写入、校验与发布

构建器先用首个 embedding 确定向量维度，再创建新的 revision 集合和 `source_id` payload 索引。随后按 `batch_size`（默认 32）批量 embedding、写入并每约 5% 输出进度。

全部写入后，构建器以 Qdrant 精确计数校验 `points_count == chunk_count`。通过后才将 `rag_active` 别名原子切到新集合，并写出：

```text
storage/rag/index_manifest.json     # revision、集合名、来源校验和、内容处理统计
storage/rag/bm25_documents.json     # revision、子切片文本及父段元数据
```

如果构建在别名切换前失败，新集合会自动删除，旧 `rag_active` 不受影响。即使“服务端已创建集合、但客户端在响应前超时”，构建器也会确认并清理该未发布集合。如果别名已切换后写工件发生异常，系统不会自动删除新集合，避免误删正在提供服务的索引；日志会明确提示需要人工检查工件与别名状态。

## 5. 索引数据契约

Qdrant point 的向量来自子切片 `text`。payload 的核心字段如下：

| 字段 | 说明 |
|---|---|
| `chunk_id` | 子切片的稳定 UUID |
| `source_id` | 相对 `data/` 的来源路径 |
| `source_revision` | 来源文件 SHA256 |
| `index_revision` | 本次索引 revision |
| `parent_id` | 父段稳定 UUID |
| `parent_text` | 命中后返回给 RAG 的完整父段 |
| `tags` | 逗号分隔的主题标签 |
| `文档标题` / `章节标题` | Markdown 结构元数据（存在时） |

`parent_text` 会在在线读取时从通用 metadata 中取出并作为 `Document.page_content` 返回；`child_text` 则保留在 metadata 中，表示实际命中的证据片段。

## 6. 在线检索行为

1. `VectorStoreService` 对查询文本生成 dense 向量；
2. `QdrantVectorRepository.search()` 查询 `rag_active`，依据子切片得分排序；
3. repository 用 `parent_text` 替换返回文档的正文，并保留 `child_text`；
4. BM25 从同 revision 的离线工件中以子切片建立索引，命中后做相同的父段提升；
5. `RagSummarizeService` 只在 BM25 revision 与 Qdrant 活动 revision 一致时执行 RRF 融合；
6. 融合结果按文本相似度去重后取 Top-K，组装为模型上下文和来源引用。

若 BM25 工件与活动 Qdrant revision 不一致，系统降级为 dense 检索并记录警告，而不是混用两个版本的数据。

## 7. 配置与本地运行

`config/vector_store.yml` 的关键项：

```yaml
url: http://localhost:6333
grpc_port: 6334
prefer_grpc: true
qdrant_timeout_seconds: 60
collection_alias: rag_active
collection_prefix: rag
chunk_size: 500
chunk_overlap: 80
parent_chunk_size: 1200
parent_chunk_overlap: 160
near_duplicate_hamming_distance: 3
max_tags: 4
batch_size: 32
```

`.env` 中至少需要：

```dotenv
DASHSCOPE_API_KEY=你的密钥
QDRANT_URL=http://localhost:6333
QDRANT_API_KEY=本地随机强密钥
```

先启动 Qdrant：

```powershell
docker compose up -d qdrant
docker compose ps qdrant
```

Qdrant 仅绑定本机 `127.0.0.1`；本地 HTTP 使用 API Key 时客户端的“insecure connection”警告属于预期提示。当前客户端优先使用 gRPC `6334`，以规避本机 Docker Desktop REST 代理偶发的 502。

构建完成后可验证：

```powershell
docker compose logs qdrant --tail 100
curl.exe -H "api-key: <QDRANT_API_KEY>" http://localhost:6333/collections
```

不要把真实 `.env`、`storage/` 或 `data/` 中的大型资料提交到 Git。

## 8. 可观测性与故障处理

构建日志会记录 revision、总切片数、批次数、约每 5% 的进度和总耗时。manifest 中的 `content_processing` 记录空切片、精确重复与近重复的过滤数量。

常见失败处理：

| 现象 | 处理 |
|---|---|
| `ProxyError` / 无法连接代理 | 检查或清除当前终端的 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY` 后重试 |
| DashScope 无法访问 | 检查网络、`DASHSCOPE_API_KEY` 和代理配置 |
| Qdrant 502 | 确认 Docker Desktop 与 Qdrant 容器正常；当前构建会优先走 gRPC |
| 写入校验数量不一致 | 新 revision 不会被激活，检查 Qdrant 日志后重新构建 |
| 构建中断 | 若未激活别名，未发布集合会被自动清理；旧索引保持可用 |

## 9. 验证门禁

代码改动后至少执行：

```powershell
.\.venv\Scripts\python.exe -m pytest app/tests -q
.\.venv\Scripts\python.exe -m ruff check app/services app/tests
```

本轮针对清洗、精确去重、标签、父子关系、BM25 父段提升和 Qdrant repository 的单元测试已覆盖。真正发布前还应执行一次完整索引构建，并检查：

- 新 manifest 的 revision 与 Qdrant 活动 revision 一致；
- `chunk_count` 与 Qdrant points count 一致；
- `content_processing` 的去重数量符合预期；
- 动作、营养、防护问题各至少抽样一次，确认返回正文是父段且 metadata 有 `child_text`；
- BM25 revision 一致时混合检索可用，不一致时能降级为 dense。

## 10. 后续演进触发条件

| 能力 | 何时实现 |
|---|---|
| DOCX / JSON / HTML 自动加载 | 确认具体来源、JSON schema、版权和正文提取规则后 |
| 更深的来源级去噪 | 接入网页、扫描 PDF 或发现清洗统计/评测存在明显噪声后 |
| LLM 摘要、实体或专业标签 | 本地规则标签无法满足过滤/展示需求，且接受重建成本与质量审核后 |
| Qdrant 原生 sparse | BM25 + dense 的评测证明词法召回不足，且融合增益可量化后 |
| Qdrant 快照 | 有明确 RPO/RTO 或不可接受“从源资料重建”的恢复时间后 |
| 集群与副本 | 有多节点、高可用或单机容量需求后 |

在这些条件出现前，保持当前单机 Qdrant、离线 revision 与源文件可重建的模式，既能演示生产演进路径，也不会为 Demo 引入没有验证价值的运维复杂度。
