# 02 - RAG 离线管线重构方案

> **状态**: 待实施  
> **优先级**: P0（直接影响检索质量和性能）  
> **预计工时**: 4-5 天

---

## 一、现状诊断 vs 理想离线管线

### 1.1 离线管线完整对照表

| 环节 | 理想流程 | 当前实现 | 差距评估 |
|------|---------|---------|---------|
| **数据加载** | 支持多种格式（txt/pdf/docx/markdown/html），支持批量/增量 | 仅 txt + pdf (`file_handler.py:53-57`) | 缺少 docx/markdown/html/json 支持 |
| **文档解析** | 结构化解析（标题/段落/表格/列表），保留层级信息 | PyPDFLoader + TextLoader 原始加载，无结构识别 | 丢失文档结构信息，切分质量差 |
| **结构识别** | 识别标题层级、段落、表格、列表、代码块 | **无** | 切分可能把一个问题切成两半 |
| **清洗** | 去噪（页码/页眉页脚/乱码）、去重（全量+段级）、统一编码 | 仅基本清洗（BOM/空白/换行），无去重 (`file_handler.py:59-77`) | 脏数据影响向量质量 |
| **文本切分** | 语义感知切分（按段落/句子边界）、父子切片（parent-child）、自适应 chunk size | RecursiveCharacterTextSplitter 按字符切分，无语义感知 | 切分破坏语义完整性 |
| **元数据增强** | 生成摘要、提取关键词/实体、标记文档类型/日期/作者 | 仅 source/chunk_index/source_type (`vector_store.py:209-212`) | 缺少摘要/关键词，无法做结构化过滤 |
| **文本向量化** | 批量 embedding、异步、错误重试 | DashScopeEmbeddings 同步逐条 (`factory.py:23`) | 无批量优化，大文档加载慢 |
| **构建索引** | 多索引并存（向量+关键词+图），增量更新 | 仅 ChromaDB 向量索引 + 内存 BM25，MD5 增量 | BM25 内存索引重启丢失 |
| **向量数据保存与持久化** | 支持快照/备份/恢复，版本管理 | ChromaDB 自动持久化，manifest 文件管理 | 无备份恢复、无版本回滚 |

### 1.2 当前代码位置

| 模块 | 文件 | 行号 | 职责 |
|------|------|------|------|
| 数据加载 | `app/utils/file_handler.py` | 36-57 | `listdir_with_allowed_type`, `txt_loader`, `pdf_loader` |
| 文档清洗 | `app/utils/file_handler.py` | 59-91 | `clean_text`, `normalize_documents` |
| QA切分 | `app/utils/file_handler.py` | 93-126 | `split_qa_documents` |
| 文本切分 | `app/services/vector_store.py` | 52-65 | `_build_splitter`, `_get_splitter` |
| MD5去重 | `app/services/vector_store.py` | 67-70, 76-88 | `_build_chunk_id`, `_load_manifest`, `_save_manifest` |
| 加载入口 | `app/services/vector_store.py` | 154-231 | `load_document` |
| 陈旧清理 | `app/services/vector_store.py` | 99-139 | `_delete_documents_by_source`, `_cleanup_stale_documents` |
| 自愈重建 | `app/services/rag_service.py` | 73-81 | `_repair_vector_store` |

---

## 二、分步骤重构方案

### 步骤 1：数据加载增强 — 多格式支持

**当前问题**：仅支持 txt 和 pdf，无法加载 Markdown 文档、Word 文档、JSON 数据字典。

**方案**：在 `file_handler.py` 添加格式分发器：

```python
# app/utils/file_handler.py 新增

from langchain_community.document_loaders import (
    PyPDFLoader, TextLoader, UnstructuredMarkdownLoader,
    Docx2txtLoader, JSONLoader
)

LOADER_MAP = {
    ".txt": TextLoader,
    ".pdf": PyPDFLoader,
    ".md": UnstructuredMarkdownLoader,
    ".docx": Docx2txtLoader,
    # JSON 格式需要 jq 表达式，暂不纳入自动加载，手动导入
}

def smart_loader(filepath: str) -> list[Document]:
    """根据文件后缀自动选择加载器"""
    ext = os.path.splitext(filepath)[1].lower()
    loader_cls = LOADER_MAP.get(ext)
    if loader_cls is None:
        logger.warning(f"不支持的文件格式：{ext}，文件 {filepath} 被跳过")
        return []
    if ext == ".pdf":
        return loader_cls(filepath).load()
    return loader_cls(filepath, encoding="utf-8").load()
```

同时更新 `config/chroma.yml` 的 `allow_knowledge_file_type`:
```yaml
allow_knowledge_file_type: ["txt", "pdf", "md", "docx"]
```

### 步骤 2：结构识别 — 保留文档层级

**当前问题**：`RecursiveCharacterTextSplitter` 不理解文档结构，只能按字符数切分。一个标题和它的正文可能被切开。

**方案**：引入 `MarkdownHeaderTextSplitter`（对 md 文件）和自定义标题识别（对 txt 文件）：

```python
# app/services/vector_store.py 新增方法

from langchain_text_splitters import MarkdownHeaderTextSplitter

HEADERS_TO_SPLIT_ON = [
    ("#", "h1"),
    ("##", "h2"),
    ("###", "h3"),
]

def _structure_aware_split(self, documents: list[Document], file_ext: str) -> list[Document]:
    """结构感知切分：对 markdown 使用标题切分，对 txt 使用递归切分"""
    if file_ext == ".md":
        md_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=HEADERS_TO_SPLIT_ON,
            strip_headers=False,
        )
        result = []
        for doc in documents:
            md_splits = md_splitter.split_text(doc.page_content)
            for split in md_splits:
                split.metadata.update(doc.metadata)
            result.extend(md_splits)
        return result
    
    # txt/pdf 使用 RecursiveCharacterTextSplitter
    return self._get_splitter(f".{file_ext}").split_documents(documents)
```

### 步骤 3：文档清洗增强 — 去噪+去重

**当前问题**：`clean_text` 只做基础空白清理，不去除页码/页眉页脚/乱码，不去重。

**方案**：增加清洗规则配置和内容级去重：

```python
# app/utils/file_handler.py 新增

import hashlib

# 页码/页眉页脚模式
_NOISE_PATTERNS = [
    re.compile(r"^\d{1,4}\s*$", re.MULTILINE),   # 单独的数字行（页码）
    re.compile(r"^第\s*\d+\s*页\s*$", re.MULTILINE),  # "第X页"
    re.compile(r"^\s*版权所有.*$", re.MULTILINE),  # 版权声明
]

def deep_clean(text: str) -> str:
    """深度清洗：去噪 + 统一格式 + 质量评分"""
    if not text:
        return ""
    
    # 基础清洗（原有逻辑）
    cleaned = clean_text(text)
    
    # 去除噪声行
    for pattern in _NOISE_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    
    # 重新压缩空行
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    
    return cleaned.strip()

def deduplicate_documents(documents: list[Document]) -> list[Document]:
    """文档级去重：基于内容 MD5 去除完全相同的文档"""
    seen = set()
    unique = []
    for doc in documents:
        content_hash = hashlib.md5(doc.page_content.encode("utf-8")).hexdigest()
        if content_hash not in seen:
            seen.add(content_hash)
            unique.append(doc)
    if len(unique) < len(documents):
        logger.info(f"文档去重：{len(documents)} → {len(unique)}")
    return unique
```

### 步骤 4：文本切分优化 — 语义切分 + 父子切片

**当前问题**：固定 `chunk_size=240` 太小（约 120 汉字），破坏语义完整性。无重叠上下文容易丢失边界信息。

**方案**：

**4.1 增加语义切分器**

```python
# app/services/vector_store.py 新增

from langchain_text_splitters import SentenceTransformersTokenTextSplitter

def _build_semantic_splitter(self) -> RecursiveCharacterTextSplitter:
    """语义切分器：以句子边界为分割点，避免切断语义"""
    return RecursiveCharacterTextSplitter(
        chunk_size=chroma_conf.get("semantic_chunk_size", 512),     # 增加到 512
        chunk_overlap=chroma_conf.get("semantic_chunk_overlap", 80),
        separators=["\n\n", "\n", "。", "！", "？", ".", "!", "?", "；", ";", "，", ",", " ", ""],
        length_function=len,
    )
```

**4.2 引入父子切片（Parent-Child Chunking）**

> 核心思路：检索用小块（精准命中），返回给 LLM 用大块（完整上下文）

```
┌─────────────────────────────────────────┐
│  Parent Chunk (1024 tokens)              │
│  ┌──────────────────────┐                │
│  │ Child Chunk (256 tok) │  ← 检索命中   │
│  └──────────────────────┘                │
│  上下文上下文上下文上下文上下文上下文      │
└─────────────────────────────────────────┘
```

```python
# app/services/vector_store.py 新增 ParentChildSplitter

class ParentChildSplitter:
    """父子切片：小粒度 chunk 做向量检索，大粒度 chunk 返回 LLM"""
    
    def __init__(self, parent_size=1024, child_size=256, overlap=50):
        self.parent_splitter = RecursiveCharacterTextSplitter(
            chunk_size=parent_size, chunk_overlap=overlap,
            separators=["\n\n", "\n", "。", ".", " ", ""],
        )
        self.child_splitter = RecursiveCharacterTextSplitter(
            chunk_size=child_size, chunk_overlap=overlap,
            separators=["\n\n", "\n", "。", ".", " ", ""],
        )
    
    def split(self, documents: list[Document]) -> tuple[list[Document], list[Document]]:
        """返回 (parent_docs, child_docs)，child_docs 携带 parent_id 元数据"""
        parent_docs = self.parent_splitter.split_documents(documents)
        child_docs = []
        
        for p_idx, parent in enumerate(parent_docs):
            parent_id = f"parent_{p_idx}"
            parent.metadata["parent_id"] = parent_id
            children = self.child_splitter.split_documents([parent])
            for child in children:
                child.metadata["parent_id"] = parent_id
            child_docs.extend(children)
        
        return parent_docs, child_docs
```

检索时：用 child_docs 向量检索命中 → 根据 `parent_id` 返回对应的 parent_doc 给 LLM。

### 步骤 5：元数据增强 — 摘要+关键词

**当前问题**：元数据只有 `source`/`chunk_index`/`source_type`，无法按主题/关键词做结构化过滤。

**方案**：在文档加载入库时，为每个 chunk 生成摘要和关键词：

```python
# app/services/vector_store.py 新增

def _enrich_metadata(self, documents: list[Document]) -> list[Document]:
    """
    为每个文档 chunk 增强元数据：
    - 自动生成摘要（前50字）
    - 提取可能的标签（基于关键词字典匹配）
    - 记录创建时间
    """
    from datetime import datetime
    
    tags_map = {
        "深蹲": "下肢训练",
        "卧推": "上肢训练",
        "硬拉": "下肢训练",
        "减脂": "减脂",
        "增肌": "增肌",
        "营养": "营养学",
        "蛋白": "营养学",
        "热身": "训练安全",
        "拉伸": "训练安全",
        "损伤": "运动防护",
    }
    
    for doc in documents:
        content = doc.page_content
        
        # 摘要：取前 50 个字符
        doc.metadata["summary"] = content[:50]
        
        # 标签：关键词匹配
        tags = set()
        for kw, tag in tags_map.items():
            if kw in content:
                tags.add(tag)
        doc.metadata["tags"] = list(tags)
        
        # 字符数
        doc.metadata["char_count"] = len(content)
        
        # 时间戳
        doc.metadata["indexed_at"] = datetime.now().isoformat()
    
    return documents
```

### 步骤 6：向量化优化 — 批量+异步

**当前问题**：`vector_store.add_documents` 分批写入但 embedding 是逐条调用的，大文档加载慢。

**方案**：

```python
# app/services/vector_store.py 优化 load_document

def load_document(self):
    # ... 文件扫描逻辑保持不变 ...

    for path in allowed_files_path:
        # ... MD5 去重逻辑保持不变 ...
        
        # 新增：结构感知切分
        file_ext = os.path.splitext(path)[1].lower()
        split_docs = self._structure_aware_split(documents, file_ext)
        
        # 新增：语义切分（作为二次切分，处理超大段落）
        semantic_splitter = self._build_semantic_splitter()
        split_docs = semantic_splitter.split_documents(split_docs)
        
        # 新增：元数据增强
        split_docs = self._enrich_metadata(split_docs)
        
        # 优化：增大批次大小，减少 embedding API 调用轮次
        batch_size = 50  # 从 10 提升到 50
        for i in range(0, len(split_docs), batch_size):
            self.vector_store.add_documents(
                split_docs[i:i + batch_size],
                ids=ids[i:i + batch_size],
            )
```

### 步骤 7：BM25 索引持久化

**当前问题**：BM25 索引在内存中构建，服务重启后需要从 ChromaDB 全量拉取重建，耗时。

**方案**：添加磁盘缓存，避免重启后重建：

```python
# app/services/bm25_retriever.py 新增

import pickle
from app.utils.path_tool import get_abs_path

class BM25Retriever:
    def __init__(self, cache_dir: str = "chroma_db"):
        self._index = None
        self._docs = []
        self._lock = threading.Lock()
        self._doc_count_snapshot = 0
        self._cache_path = get_abs_path(f"{cache_dir}/bm25_index.pkl")
        self._load_cache()
    
    def _load_cache(self):
        """尝试从磁盘加载缓存的 BM25 索引"""
        if not os.path.exists(self._cache_path):
            return
        try:
            with open(self._cache_path, "rb") as f:
                data = pickle.load(f)
                self._index = data["index"]
                self._docs = data["docs"]
                self._doc_count_snapshot = data["count"]
            logger.info(f"BM25索引从缓存加载成功，共 {self._doc_count_snapshot} 篇文档")
        except Exception as e:
            logger.warning(f"BM25索引缓存加载失败：{str(e)}，将在首次查询时重建")
    
    def _save_cache(self):
        """将 BM25 索引序列化到磁盘"""
        try:
            with open(self._cache_path, "wb") as f:
                pickle.dump({
                    "index": self._index,
                    "docs": self._docs,
                    "count": self._doc_count_snapshot,
                }, f)
        except Exception as e:
            logger.warning(f"BM25索引缓存保存失败：{str(e)}")
    
    def build(self, documents: list):
        with self._lock:
            self._docs = list(documents)
            tokenized = [self._tokenize(doc.page_content) for doc in documents]
            self._index = BM25Okapi(tokenized)
            self._doc_count_snapshot = len(documents)
            self._save_cache()  # ← 新增：构建完成后保存到磁盘
            logger.info(f"BM25索引构建完成，共 {len(documents)} 篇文档")
```

### 步骤 8：索引备份与版本管理

**当前问题**：无快照机制，索引损坏只能全量重建（耗时）。

**方案**：添加简单的版本号管理：

```python
# app/services/vector_store.py 新增

VERSION_FILE = "chroma_db/.vector_version"

def _get_current_version(self) -> int:
    """读取当前向量库版本号"""
    try:
        with open(self.persist_directory + "/.vector_version", "r") as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return 0

def _bump_version(self) -> int:
    """版本号 +1"""
    version = self._get_current_version() + 1
    with open(self.persist_directory + "/.vector_version", "w") as f:
        f.write(str(version))
    logger.info(f"向量库版本：V{version}")
    return version
```

---

## 三、离线管线重构后完整流程

```
应用启动 / 知识库文件变更
│
├── 1. 数据加载 ─────────────────────────────────────────────
│   ├── 扫描 data/ 目录（支持 txt/pdf/md/docx）
│   ├── 为每个文件选择对应 Loader
│   └── 返回原始 Document 列表
│
├── 2. 文档解析 & 结构识别 ──────────────────────────────────
│   ├── md 文件：MarkdownHeaderTextSplitter 提取标题层级
│   ├── txt 文件：正则识别章节标题（#/第X章/一、二、等模式）
│   └── 保留层级信息到 metadata["header_hierarchy"]
│
├── 3. 清洗 ─────────────────────────────────────────────────
│   ├── 基础清洗：BOM/空白/换行符统一
│   ├── 去噪：页码/页眉页脚/版权声明
│   ├── 文档级去重：基于内容 MD5
│   └── QA 识别：FAQ 格式 → 独立 Q-A 对
│
├── 4. 文本切分 ─────────────────────────────────────────────
│   ├── 一级切分（structure-aware）：按章节/标题边界
│   ├── 二级切分（semantic）：按段落/句子边界
│   ├── 父子切片：大 window 返回 LLM，小 window 做检索
│   └── chunk 元数据：source/chunk_index/summary/tags/char_count
│
├── 5. 元数据增强 ───────────────────────────────────────────
│   ├── 生成摘要（首 50 字）
│   ├── 关键词标签匹配
│   └── 索引时间戳
│
├── 6. 文本向量化 ───────────────────────────────────────────
│   ├── 批量 embedding（batch_size=50）
│   ├── 异常重试（DashScope API 抖动）
│   └── 稳定 ID 生成（source:index:md5）
│
├── 7. 构建索引 ────────────────────────────────────────────
│   ├── ChromaDB 向量索引写入
│   ├── BM25 关键词索引构建 + 磁盘缓存
│   ├── manifest 记录（文件 MD5 → 版本映射）
│   └── 版本号递增
│
└── 8. 向量数据持久化 ──────────────────────────────────────
    ├── ChromaDB 自动持久化（chroma.sqlite3）
    ├── BM25 索引 pickle 缓存（bm25_index.pkl）
    ├── manifest 文件（knowledge_manifest.json）
    └── 版本号文件（.vector_version）
```

---

## 四、实施检查清单

- [ ] 1. `file_handler.py` 添加 md/docx/json 格式加载器
- [ ] 2. `config/chroma.yml` 扩展 `allow_knowledge_file_type`
- [ ] 3. `vector_store.py` 添加 `_structure_aware_split` 方法
- [ ] 4. `vector_store.py` 添加 `_build_semantic_splitter`（更大 chunk + 更好分隔符）
- [ ] 5. `vector_store.py` 实现 `ParentChildSplitter`
- [ ] 6. `file_handler.py` 添加 `deep_clean`（去噪）+ `deduplicate_documents`（去重）
- [ ] 7. `vector_store.py` 添加 `_enrich_metadata`（摘要+标签+时间戳）
- [ ] 8. `vector_store.py` `load_document` 增大 batch_size 到 50
- [ ] 9. `bm25_retriever.py` 添加磁盘缓存持久化
- [ ] 10. `vector_store.py` 添加版本号管理
- [ ] 11. `config/chroma.yml` 补充新参数（semantic_chunk_size 等）
- [ ] 12. 编写对应单元测试

---

## 五、验收标准

1. 支持 txt/pdf/md/docx 四种格式的文档加载
2. Markdown 文件按标题层级切分，保留 `h1/h2/h3` 元数据
3. 语义切分 chunk_size 512，overlap 80，不在句子中间切断
4. 每个文档有 `summary`/`tags`/`char_count` 增强元数据
5. 文档去重后不重复入库
6. BM25 索引重启后从磁盘缓存加载，无需重新构建
7. 10MB 知识库加载时间从 ~5 分钟降到 ~1 分钟（批量 embedding 优化）
