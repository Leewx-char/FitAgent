import pytest
from langchain_core.documents import Document
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

from app.services import knowledge_indexer
from app.services.fitkg_markdown_builder import render_markdown
from app.services.knowledge_indexer import KnowledgeIndexer
from app.services.vector_repository import IndexedChunk
from app.utils.file_handler import txt_loader


def test_fitkg_markdown_uses_explicit_title_boundaries():
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
    source = tmp_path / "fitkg.md"
    source.write_text("# 中文标题\n\n## 样本\n深蹲。", encoding="utf-8")

    documents = txt_loader(str(source))

    assert documents[0].page_content.startswith("# 中文标题")


def test_indexer_cleans_unpublished_collection_when_batch_embedding_fails(monkeypatch):
    class FailingEmbeddingModel:
        def __init__(self):
            self.calls = 0

        def embed_documents(self, _texts):
            self.calls += 1
            if self.calls == 1:
                return [[0.1, 0.2]]
            raise RuntimeError("ProxyError: unable to connect to proxy")

    class FakeRepository:
        def __init__(self):
            self.created: list[str] = []
            self.deleted: list[str] = []

        def create_collection(self, collection_name, _vector_size):
            self.created.append(collection_name)

        def upsert(self, _collection_name, _chunks, _vectors):
            raise AssertionError("向量化失败后不应继续写入")

        def delete_collection(self, collection_name):
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
