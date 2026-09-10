"""DashScope 二阶段重排的边界测试。"""

from types import SimpleNamespace

import pytest
from langchain_core.documents import Document

from app.services import reranker
from app.services.vector_repository import ScoredChunk


def make_candidate(chunk_id: str, score: float) -> ScoredChunk:
    return ScoredChunk(
        document=Document(
            page_content=f"{chunk_id} 的知识文本",
            metadata={"source_id": "知识.md", "chunk_id": chunk_id},
        ),
        score=score,
    )


def test_dashscope_reranker_maps_provider_order_and_scores_without_returning_text(monkeypatch):
    calls = []

    def call(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            status_code=200,
            code="",
            output=SimpleNamespace(
                results=[
                    SimpleNamespace(index=1, relevance_score=0.92),
                    SimpleNamespace(index=0, relevance_score=0.61),
                ]
            ),
        )

    monkeypatch.setattr(reranker.dashscope.TextReRank, "call", call)
    candidates = [make_candidate("first", 0.2), make_candidate("second", 0.1)]

    result = reranker.DashScopeReranker("gte-rerank-v2", "test-key").rerank(
        "深蹲时膝盖内扣怎么纠正？", candidates, limit=6
    )

    assert [item.document.metadata["chunk_id"] for item in result] == ["second", "first"]
    assert [item.score for item in result] == [0.92, 0.61]
    assert calls == [
        {
            "model": "gte-rerank-v2",
            "query": "深蹲时膝盖内扣怎么纠正？",
            "documents": ["first 的知识文本", "second 的知识文本"],
            "top_n": 2,
            "return_documents": False,
            "api_key": "test-key",
        }
    ]


def test_dashscope_reranker_rejects_provider_failure_and_invalid_indexes(monkeypatch):
    candidates = [make_candidate("only", 0.2)]

    monkeypatch.setattr(
        reranker.dashscope.TextReRank,
        "call",
        lambda **kwargs: SimpleNamespace(status_code=429, code="Throttling", output=None),
    )
    service = reranker.DashScopeReranker("gte-rerank-v2", "test-key")
    with pytest.raises(RuntimeError, match="DashScope 重排暂时不可用"):
        service.rerank("深蹲", candidates, limit=1)

    monkeypatch.setattr(
        reranker.dashscope.TextReRank,
        "call",
        lambda **kwargs: SimpleNamespace(
            status_code=200,
            code="",
            output=SimpleNamespace(results=[SimpleNamespace(index=9, relevance_score=0.9)]),
        ),
    )
    with pytest.raises(RuntimeError, match="无效候选索引"):
        service.rerank("深蹲", candidates, limit=1)

    monkeypatch.setattr(
        reranker.dashscope.TextReRank,
        "call",
        lambda **kwargs: SimpleNamespace(
            status_code=200,
            code="",
            output=SimpleNamespace(
                results=[
                    SimpleNamespace(index=0, relevance_score=0.9),
                    SimpleNamespace(index=0, relevance_score=0.8),
                ]
            ),
        ),
    )
    with pytest.raises(RuntimeError, match="超出请求上限"):
        service.rerank("深蹲", candidates, limit=1)

    candidates = [make_candidate("first", 0.2), make_candidate("second", 0.1)]
    monkeypatch.setattr(
        reranker.dashscope.TextReRank,
        "call",
        lambda **kwargs: SimpleNamespace(
            status_code=200,
            code="",
            output=SimpleNamespace(
                results=[
                    SimpleNamespace(index=0, relevance_score=0.9),
                    SimpleNamespace(index=0, relevance_score=0.8),
                ]
            ),
        ),
    )
    with pytest.raises(RuntimeError, match="重复候选索引"):
        service.rerank("深蹲", candidates, limit=2)


def test_dashscope_reranker_rejects_incomplete_results_and_boolean_indexes(monkeypatch):
    service = reranker.DashScopeReranker("gte-rerank-v2", "test-key")
    candidates = [make_candidate("first", 0.2), make_candidate("second", 0.1)]

    monkeypatch.setattr(
        reranker.dashscope.TextReRank,
        "call",
        lambda **kwargs: SimpleNamespace(
            status_code=200,
            code="",
            output=SimpleNamespace(results=[]),
        ),
    )
    with pytest.raises(RuntimeError, match="结果数量与请求上限不符"):
        service.rerank("深蹲", candidates, limit=2)

    monkeypatch.setattr(
        reranker.dashscope.TextReRank,
        "call",
        lambda **kwargs: SimpleNamespace(
            status_code=200,
            code="",
            output=SimpleNamespace(results=[SimpleNamespace(index=True, relevance_score=0.9)]),
        ),
    )
    with pytest.raises(RuntimeError, match="无效候选索引"):
        service.rerank("深蹲", candidates, limit=1)
