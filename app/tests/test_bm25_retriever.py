import json

from app.services.bm25_retriever import BM25Retriever
from app.utils.file_handler import get_file_sha256_hex


def test_bm25_source_filter_and_offline_artifact(tmp_path):
    artifact = tmp_path / "bm25_documents.json"
    artifact.write_text(
        json.dumps(
            {
                "index_revision": "revision-1",
                "documents": [
                    {
                        "page_content": "深蹲要保持背部稳定",
                        "metadata": {"source_id": "动作指南大全.txt"},
                    },
                    {
                        "page_content": "深蹲后的营养恢复建议",
                        "metadata": {"source_id": "营养学知识.txt"},
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    retriever = BM25Retriever()

    revision = retriever.load_artifact(str(artifact))
    results = retriever.search("深蹲", source_filter=["动作指南大全.txt"])

    assert revision == "revision-1"
    assert len(results) == 1
    assert results[0][0].metadata["source_id"] == "动作指南大全.txt"


def test_file_checksum_is_stable_for_index_revisions(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("健身知识", encoding="utf-8")

    checksum = get_file_sha256_hex(str(source))

    assert checksum is not None
    assert len(checksum) == 64
    assert checksum == get_file_sha256_hex(str(source))


def test_bm25_returns_parent_context_but_keeps_child_evidence(tmp_path):
    artifact = tmp_path / "bm25_documents.json"
    artifact.write_text(
        json.dumps(
            {
                "index_revision": "revision-1",
                "documents": [
                    {
                        "page_content": "深蹲时保持脊柱中立。",
                        "metadata": {
                            "source_id": "guide.md",
                            "parent_text": "深蹲动作说明。深蹲时保持脊柱中立。出现疼痛应停止训练。",
                        },
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    retriever = BM25Retriever()
    retriever.load_artifact(str(artifact))

    document, _score = retriever.search("深蹲", k=1)[0]

    assert "出现疼痛" in document.page_content
    assert document.metadata["child_text"] == "深蹲时保持脊柱中立。"
