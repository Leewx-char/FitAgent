import pytest
from langchain_core.documents import Document
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

from app.services import knowledge_indexer
from app.services.fitkg_markdown_builder import render_markdown
from app.services.knowledge_indexer import KnowledgeIndexer
from app.services.vector_repository import IndexedChunk
from app.utils.file_handler import txt_loader


def test_fitkg_markdown_uses_explicit_title_boundaries():
    """验证 FitKG Markdown 使用明确的文档和样本标题层级。"""
    markdown = render_markdown(
        [
            {
                "tokens": ["深", "蹲", "锻", "炼", "腿", "部"],
                "entities": [
                    {"type": "健身动作", "start": 0, "end": 2},
                    {"type": "身体部位", "start": 4, "end": 6},
                ],
                "relations": [{"type": "锻炼", "head": 0, "tail": 1}],
            }
        ],
        "train",
    )

    assert markdown.startswith("# FitKG-CN 中文科学健身知识图谱（训练集）")
    assert "## 样本 00001" in markdown
    assert "深蹲 ——锻炼→ 腿部" in markdown


def test_markdown_is_split_by_heading_before_recursive_chunking():
    """验证 Markdown 先按标题分段，再进行递归切片。"""
    indexer = object.__new__(KnowledgeIndexer)
    indexer.markdown_header_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=[("#", "文档标题"), ("##", "章节标题")],
        strip_headers=False,
    )
    indexer.recursive_splitter = RecursiveCharacterTextSplitter(
        chunk_size=80,
        chunk_overlap=10,
        separators=[
            "\n\n",
            "\n",
            "。",
            "",
        ],
        length_function=len,
    )
    markdown = (
        "# 动作知识\n\n> 此段是文档说明，不应单独进入检索。"
        "\n\n## 深蹲\n深蹲保持脊柱中立。\n\n## 硬拉\n硬拉保持背部稳定。"
    )

    chunks = indexer._split_source_documents("fitkg.md", [Document(page_content=markdown)])

    assert len(chunks) == 2
    assert [chunk.metadata["章节标题"] for chunk in chunks] == ["深蹲", "硬拉"]
    assert all(chunk.metadata["文档标题"] == "动作知识" for chunk in chunks)


def test_text_loader_reads_utf8_markdown(tmp_path):
    """验证文本加载器以 UTF-8 正确读取中文 Markdown。"""
    source = tmp_path / "fitkg.md"
    source.write_text("# 中文标题\n\n## 样本\n深蹲。", encoding="utf-8")

    documents = txt_loader(str(source))

    assert documents[0].page_content.startswith("# 中文标题")


def test_indexer_cleans_unpublished_collection_when_batch_embedding_fails(monkeypatch):
    """验证批量向量化失败时删除尚未发布的临时集合。"""
    class FailingEmbeddingModel:
        def __init__(self):
            """初始化批量调用次数，用于在第二批模拟连接失败。"""
            self.calls = 0

        def embed_documents(self, _texts):
            """首批返回向量，后续批次模拟代理连接异常。"""
            self.calls += 1
            if self.calls == 1:
                return [[0.1, 0.2]]
            raise RuntimeError("ProxyError: unable to connect to proxy")

    class FakeRepository:
        def __init__(self):
            """初始化记录创建与删除集合名称的列表。"""
            self.created: list[str] = []
            self.deleted: list[str] = []

        def create_collection(self, collection_name, _vector_size):
            """记录创建临时集合的请求。"""
            self.created.append(collection_name)

        def upsert(self, _collection_name, _chunks, _vectors):
            """若向量化失败后仍写入集合，则使测试失败。"""
            raise AssertionError("向量化失败后不应继续写入")

        def delete_collection(self, collection_name):
            """记录删除未发布临时集合的请求。"""
            self.deleted.append(collection_name)

    repository = FakeRepository()
    indexer = object.__new__(KnowledgeIndexer)
    indexer.config = {
        "collection_prefix": "rag",
        "collection_alias": "rag_active",
        "batch_size": 1,
    }
    indexer.repository = repository
    indexer._load_source_documents = lambda: ([], {"fitkg.md": "source-sha"})
    indexer._build_chunks = lambda _documents, _checksums: [
        IndexedChunk("chunk-1", "深蹲保持脊柱中立", {"source_id": "fitkg.md"})
    ]
    indexer._build_revision = lambda _checksums: "a" * 64
    monkeypatch.setattr(knowledge_indexer, "get_embedding_model", FailingEmbeddingModel)

    with pytest.raises(RuntimeError, match="代理连接不可用"):
        indexer.build_and_activate()

    assert repository.created == ["rag_aaaaaaaaaaaa"]
    assert repository.deleted == ["rag_aaaaaaaaaaaa"]


def test_preflight_blocks_dataset_that_does_not_meet_chunk_gate():
    """验证预检在有效切片数低于质量门槛时阻止构建。"""
    indexer = object.__new__(KnowledgeIndexer)
    indexer.config = {"min_source_count": 1, "min_chunk_count": 2}
    indexer._last_build_stats = {"indexed_chunks": 1}
    indexer._load_source_documents = lambda: (
        [("动作.md", [Document(page_content="## 深蹲\n保持脊柱中立。")])],
        {"动作.md": "source-sha"},
    )
    indexer._build_chunks = lambda _documents, _checksums: [
        IndexedChunk("chunk-1", "保持脊柱中立。", {"source_id": "动作.md"})
    ]

    with pytest.raises(RuntimeError, match="有效切片数不足"):
        indexer.preflight()


def test_preflight_mode_does_not_initialize_qdrant_client(monkeypatch):
    """验证仅预检的索引器不初始化 Qdrant 客户端。"""
    def fail_if_created(*_args, **_kwargs):
        """若预检错误地创建 Qdrant 客户端则使测试失败。"""
        raise AssertionError("预检不应创建 Qdrant 客户端")

    monkeypatch.setattr(knowledge_indexer, "QdrantVectorRepository", fail_if_created)

    indexer = KnowledgeIndexer(initialize_repository=False)

    assert indexer.repository is None


def test_preflight_report_contains_source_level_counts_and_warnings():
    """验证预检报告包含各来源切片计数及重复数据警告。"""
    indexer = object.__new__(KnowledgeIndexer)
    indexer.config = {"min_source_count": 1, "min_chunk_count": 1}
    indexer._last_build_stats = {
        "indexed_chunks": 1,
        "dropped_empty_chunks": 0,
        "exact_duplicate_chunks": 0,
        "near_duplicate_chunks": 1,
    }
    indexer._load_source_documents = lambda: (
        [
            ("动作.md", [Document(page_content="## 深蹲")]),
            ("重复.md", [Document(page_content="## 重复")]),
        ],
        {"动作.md": "action-sha", "重复.md": "duplicate-sha"},
    )
    indexer._build_chunks = lambda _documents, _checksums: [
        IndexedChunk("chunk-1", "保持脊柱中立。", {"source_id": "动作.md"})
    ]
    indexer._build_revision = lambda _checksums: "a" * 64

    report = indexer.preflight().report()

    assert report["status"] == "passed_with_warnings"
    assert report["sources"]["动作.md"]["chunk_count"] == 1
    assert report["sources"]["重复.md"]["chunk_count"] == 0
    assert "重复.md" in report["warnings"][0]
