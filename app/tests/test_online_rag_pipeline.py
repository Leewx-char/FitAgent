"""在线 RAG V1 的查询理解、重排序和上下文预算测试。"""

import time
from types import SimpleNamespace

from app.services.context_builder import ContextBuilder
from app.services.query_planner import QueryPlanner
from app.services.reranker import LexicalReranker, RerankCandidate
from app.services.retrieval_contracts import RetrievalHit


class FakeQueryModel:
    """返回受控 JSON 的查询模型替身。"""

    def __init__(self, content: str) -> None:
        """保存待返回的受控模型内容并初始化提示词记录。"""
        self.content = content
        self.prompts: list[str] = []

    def invoke(self, prompt: str):
        """记录查询规划提示词并返回预设内容。"""
        self.prompts.append(prompt)
        return SimpleNamespace(content=self.content)


class OrderedQueryPlanner:
    """提供两个受控查询，用于验证聚合结果不受完成顺序影响。"""

    @staticmethod
    def plan(query, _history):
        """返回两个固定子查询及其原始查询顺序。"""
        return SimpleNamespace(
            original_query=query,
            rewritten_query=query,
            search_queries=("slow query", "fast query"),
            used_llm=False,
            fallback_reason="",
        )


class OutOfOrderVectorStore:
    """让第二个计划查询先完成，同时保持各自的原始排序位置。"""

    @staticmethod
    def similarity_search(query, _limit, _source_filter):
        """让慢查询延迟完成并返回对应的单条向量命中。"""
        if query == "slow query":
            time.sleep(0.02)
            chunk_id = "slow"
        else:
            chunk_id = "fast"
        return [
            SimpleNamespace(
                document=SimpleNamespace(
                    page_content=query,
                    metadata={"source_id": "guide.md", "chunk_id": chunk_id},
                ),
                score=0.9,
            )
        ]

    @staticmethod
    def active_revision():
        """返回供检索结果使用的固定索引版本。"""
        return "revision-1"


def test_query_planner_keeps_normal_question_on_fast_path():
    """验证普通问题不调用模型，直接走查询规划快速路径。"""
    def unexpected_model_factory():
        """若普通问题错误触发模型创建则使测试失败。"""
        raise AssertionError("不应调用模型")

    planner = QueryPlanner(model_factory=unexpected_model_factory)

    plan = planner.plan("深蹲时膝盖怎么放？")

    assert plan.search_queries == ("深蹲时膝盖怎么放？",)
    assert plan.used_llm is False


def test_query_planner_resolves_reference_with_history():
    """验证指代性问题结合历史后由模型改写为多个子查询。"""
    model = FakeQueryModel(
        '{"rewritten_query":"杠铃深蹲的标准动作和常见错误","subqueries":["杠铃深蹲标准动作","杠铃深蹲常见错误"]}'
    )
    planner = QueryPlanner(model_factory=lambda: model)

    plan = planner.plan(
        "那深蹲呢？",
        history=[{"role": "user", "content": "硬拉和深蹲哪个更适合新手？"}],
    )

    assert plan.used_llm is True
    assert plan.rewritten_query.startswith("杠铃深蹲")
    assert plan.search_queries == ("杠铃深蹲的标准动作和常见错误", "杠铃深蹲标准动作")
    assert "硬拉和深蹲" in model.prompts[0]


def test_query_planner_falls_back_when_model_returns_invalid_payload():
    """验证模型返回无效载荷时规划器回退到原始查询。"""
    planner = QueryPlanner(model_factory=lambda: FakeQueryModel("不是 JSON"))

    plan = planner.plan(
        "那深蹲呢？",
        history=[{"role": "user", "content": "先聊硬拉"}],
    )

    assert plan.search_queries == ("那深蹲呢？",)
    assert plan.used_llm is False
    assert plan.fallback_reason == "ValueError"


def test_lexical_reranker_promotes_candidate_matching_query_terms():
    """验证词法重排序提升包含查询关键词的候选证据。"""
    reranker = LexicalReranker(base_score_weight=0.7)

    results = reranker.rerank(
        "深蹲膝盖内扣",
        [
            RerankCandidate("generic", "训练前动态热身", 0.02),
            RerankCandidate("squat", "深蹲时膝盖内扣应关注臀中肌", 0.019),
        ],
    )

    assert results[0].candidate_id == "squat"


def test_context_builder_keeps_child_evidence_inside_budget():
    """验证上下文预算截断时仍保留子级证据附近的关键文本。"""
    parent = "前言" * 180 + "关键证据：深蹲时膝盖追踪脚尖方向。" + "补充" * 180
    hit = RetrievalHit(
        evidence_id="动作指南大全.txt#chunk-1",
        source_id="动作指南大全.txt",
        chunk_id="chunk-1",
        parent_id="parent-1",
        text=parent,
        child_text="关键证据：深蹲时膝盖追踪脚尖方向。",
        rank=1,
        score=0.03,
        dense_rank=1,
        bm25_rank=1,
        rerank_score=0.9,
        metadata={},
    )

    snippet = ContextBuilder(max_context_chars=240, max_chars_per_evidence=240).build((hit,))[0]

    assert snippet.truncated is True
    assert len(snippet.text) <= 240
    assert "膝盖追踪脚尖" in snippet.text


def test_parallel_retrieval_keeps_query_plan_order_when_requests_finish_out_of_order():
    """验证并行检索即使乱序完成，结果仍按查询计划排列。"""
    from app.services.rag_service import RagSummarizeService

    class EmptyBM25:
        @staticmethod
        def load_artifact(_path):
            """模拟空 BM25 工件加载，不提供词法检索结果。"""
            return None

    service = RagSummarizeService(
        vector_store=OutOfOrderVectorStore(),
        bm25=EmptyBM25(),
        query_planner=OrderedQueryPlanner(),
    )
    service.reranker_enabled = False

    result = service.retrieve("test query")

    assert [hit.chunk_id for hit in result.hits] == ["slow", "fast"]
