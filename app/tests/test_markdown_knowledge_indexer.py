import uuid

import pytest
from langchain_core.documents import Document
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

from app.services import knowledge_indexer
from app.services.fitkg_markdown_builder import render_markdown
from app.services.knowledge_indexer import KnowledgeIndexer
from app.services.vector_repository import IndexedChunk
from app.utils.file_handler import clean_text, pdf_loader, txt_loader


def test_clean_text_normalizes_unicode_and_whitespace():
    assert clean_text("\ufeffＡ\u200b\r\n\r\n  深蹲\t训练  ") == "A\n\n深蹲 训练"


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
        separators=["\n\n", "\n", "。", ""],
        length_function=len,
    )
    markdown = "# 动作知识\n\n## 深蹲\n深蹲保持脊柱中立。\n\n## 硬拉\n硬拉保持背部稳定。"

    chunks = indexer._split_source_documents("fitkg.md", [Document(page_content=markdown)])

    assert [chunk.metadata["章节标题"] for chunk in chunks] == ["深蹲", "硬拉"]
    assert all(chunk.metadata["文档标题"] == "动作知识" for chunk in chunks)


def test_indexer_uses_default_separators_with_canonical_config():
    indexer = KnowledgeIndexer(initialize_repository=False)

    assert indexer.repository is None


def test_loaders_read_utf8_text_and_pdf_documents(tmp_path):
    text_source = tmp_path / "fitkg.md"
    text_source.write_text("# 中文标题\n\n## 样本\n深蹲。", encoding="utf-8")

    text_documents = txt_loader(str(text_source))
    pdf_documents = pdf_loader("app/tests/fixtures/text_health_report.pdf")

    assert text_documents[0].page_content.startswith("# 中文标题")
    assert any(document.page_content.strip() for document in pdf_documents)


def test_build_chunks_deduplicates_exact_chunk_text_and_keeps_scalar_loader_metadata():
    indexer = object.__new__(KnowledgeIndexer)
    indexer.markdown_header_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=[("#", "文档标题"), ("##", "章节标题")],
        strip_headers=False,
    )
    indexer.recursive_splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,
        chunk_overlap=0,
        separators=["\n\n", "\n", ""],
        length_function=len,
    )
    documents = [
        Document(page_content="深蹲保持脊柱中立。", metadata={"page": 0, "nested": {"drop": True}}),
        Document(page_content="深蹲保持脊柱中立。", metadata={"page": 1}),
    ]

    chunks = indexer._build_chunks([("动作.txt", documents)])

    expected_id = str(uuid.uuid5(knowledge_indexer._INDEX_NAMESPACE, "动作.txt:0:0"))
    assert [(chunk.chunk_id, chunk.text) for chunk in chunks] == [(expected_id, "深蹲保持脊柱中立。")]
    assert chunks[0].metadata == {
        "chunk_id": expected_id,
        "source_id": "动作.txt",
        "source_type": "txt",
        "ordinal": 0,
        "page": 0,
    }


def test_preflight_blocks_empty_sources_and_empty_chunks():
    no_source_indexer = object.__new__(KnowledgeIndexer)
    no_source_indexer.config = {"min_source_count": 1, "min_chunk_count": 1}
    no_source_indexer._load_source_documents = lambda: []

    with pytest.raises(RuntimeError, match="有效来源数不足"):
        no_source_indexer.preflight()

    no_chunk_indexer = object.__new__(KnowledgeIndexer)
    no_chunk_indexer.config = {"min_source_count": 1, "min_chunk_count": 1}
    no_chunk_indexer._load_source_documents = lambda: [
        ("动作.txt", [Document(page_content="深蹲保持脊柱中立。")])
    ]
    no_chunk_indexer._build_chunks = lambda _documents: []

    with pytest.raises(RuntimeError, match="有效切片数不足"):
        no_chunk_indexer.preflight()


def test_build_embeds_all_chunks_before_one_repository_rebuild(monkeypatch):
    class RecordingEmbeddingModel:
        def embed_documents(self, texts):
            assert texts == ["深蹲", "硬拉"]
            return [[0.1], [0.2]]

    class RecordingRepository:
        def __init__(self):
            self.calls = []

        def rebuild(self, chunks, vectors):
            self.calls.append((chunks, vectors))

    chunks = [
        IndexedChunk("first", "深蹲", {"source_id": "动作.txt"}),
        IndexedChunk("second", "硬拉", {"source_id": "动作.txt"}),
    ]
    repository = RecordingRepository()
    indexer = object.__new__(KnowledgeIndexer)
    indexer.config = {"collection_name": "fitagent_knowledge"}
    indexer.repository = repository
    indexer.preflight = lambda: chunks
    monkeypatch.setattr(knowledge_indexer, "get_embedding_model", RecordingEmbeddingModel)

    result = indexer.build()

    assert repository.calls == [(chunks, [[0.1], [0.2]])]
    assert result.collection_name == "fitagent_knowledge"
    assert result.chunk_count == 2


def test_build_does_not_rebuild_when_embedding_fails(monkeypatch):
    class FailingEmbeddingModel:
        def embed_documents(self, _texts):
            raise RuntimeError("embedding unavailable")

    class RecordingRepository:
        def rebuild(self, _chunks, _vectors):
            raise AssertionError("embedding failure must not rebuild the repository")

    indexer = object.__new__(KnowledgeIndexer)
    indexer.repository = RecordingRepository()
    indexer.preflight = lambda: [IndexedChunk("first", "深蹲", {"source_id": "动作.txt"})]
    monkeypatch.setattr(knowledge_indexer, "get_embedding_model", FailingEmbeddingModel)

    with pytest.raises(RuntimeError, match="embedding unavailable"):
        indexer.build()
