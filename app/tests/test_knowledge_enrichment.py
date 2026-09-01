from langchain_core.documents import Document
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

from app.services.knowledge_enrichment import ContentDeduplicator, DeepTextCleaner, MetadataEnricher
from app.services.knowledge_indexer import KnowledgeIndexer


def test_deep_cleaner_removes_navigation_and_repeated_lines():
    """验证深度清洗器移除导航、链接和重复文本行。"""
    cleaner = DeepTextCleaner()

    cleaned = cleaner.clean(
        "\ufeff深蹲时保持脊柱中立\n深蹲时保持脊柱中立\n"
        "第 2 页\n阅读原文\nhttps://example.com/article\n硬拉前先热身"
    )

    assert cleaned == "深蹲时保持脊柱中立\n硬拉前先热身"


def test_content_deduplicator_removes_exact_content_duplicates():
    """验证内容去重器将规范化后相同的文本识别为重复。"""
    deduplicator = ContentDeduplicator()

    assert deduplicator.is_duplicate("深蹲时保持脊柱中立。") is False
    assert deduplicator.is_duplicate("深蹲时保持脊柱中立") is True
    assert deduplicator.exact_duplicates == 1


def test_metadata_enricher_creates_reusable_tags_without_unused_summary():
    """验证元数据增强器产生可复用标签而不生成未使用摘要。"""
    enricher = MetadataEnricher()
    enriched = enricher.enrich("深蹲训练前应热身；膝部疼痛时停止训练。", title="下肢动作防护")

    assert {"动作", "防护", "下肢"}.issubset(set(enriched.tags))
    assert enricher.extract_tags("膝部疼痛") == ("防护", "下肢")
    assert not hasattr(enriched, "summary")


def test_indexer_keeps_child_retrieval_text_and_parent_context():
    """验证索引切片保留子级检索文本及共享的父级上下文。"""
    indexer = object.__new__(KnowledgeIndexer)
    indexer.config = {"near_duplicate_hamming_distance": 3}
    indexer.parent_splitter = RecursiveCharacterTextSplitter(
        chunk_size=120, chunk_overlap=20, separators=["\n", "。", ""], length_function=len
    )
    indexer.recursive_splitter = RecursiveCharacterTextSplitter(
        chunk_size=35, chunk_overlap=5, separators=["。", ""], length_function=len
    )
    indexer.markdown_header_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=[("#", "文档标题"), ("##", "章节标题")], strip_headers=False
    )
    indexer.cleaner = DeepTextCleaner()
    indexer.enricher = MetadataEnricher()

    chunks = indexer._build_chunks(
        [
            (
                "guide.md",
                [
                    Document(
                        page_content=(
                            "# 训练指南\n\n## 深蹲\n深蹲时保持脊柱中立。"
                            "下蹲前先热身，出现膝部疼痛应立即停止训练。"
                        )
                    )
                ],
            )
        ],
        {"guide.md": "source-sha"},
    )

    assert len(chunks) >= 2
    assert len({chunk.metadata["parent_id"] for chunk in chunks}) == 1
    assert all("膝部疼痛" in chunk.metadata["parent_text"] for chunk in chunks)
    assert any("防护" in chunk.metadata["tags"] for chunk in chunks)
