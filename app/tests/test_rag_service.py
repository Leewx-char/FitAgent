"""在线 RAG 检索契约与引用格式测试。"""

from langchain_core.documents import Document

from app.services.rag_service import RagSummarizeService
from app.services.query_planner import QueryPlan
from app.services.vector_repository import ScoredChunk


class FakeVectorStore:
    """不连接 Qdrant 的只读检索替身。"""

    def __init__(self) -> None:
        """初始化记录向量查询文本的列表。"""
        self.queries: list[str] = []

    def similarity_search(self, query, _limit, _source_filter):
        """记录查询并返回两条固定向量证据。"""
        self.queries.append(query)
        return [
            ScoredChunk(
                Document(
                    page_content="深蹲时膝盖应追踪脚尖方向。",
                    metadata={
                        "source_id": "动作指南大全.txt",
                        "chunk_id": "chunk-squat",
                        "parent_id": "parent-squat",
                        "child_text": "膝盖应追踪脚尖方向。",
                        "ordinal": 7,
                    },
                ),
                0.91,
            ),
            ScoredChunk(
                Document(
                    page_content="训练前建议进行动态热身。",
                    metadata={
                        "source_id": "运动损伤预防.txt",
                        "chunk_id": "chunk-warmup",
                        "parent_id": "parent-warmup",
                        "child_text": "动态热身。",
                        "ordinal": 8,
                    },
                ),
                0.72,
            ),
        ]

    @staticmethod
    def active_revision():
        """返回固定的活动索引版本。"""
        return "revision-1"

    @staticmethod
    def health():
        """返回可用状态的固定仓储健康信息。"""
        return {"status": "ready"}


class FakeBM25:
    """带固定证据的 BM25 替身。"""

    @staticmethod
    def load_artifact(_path):
        """模拟加载与向量索引版本匹配的 BM25 工件。"""
        return "revision-1"

    @staticmethod
    def search(_query, k, source_filter=None):
        """忽略筛选参数并返回一条固定 BM25 证据。"""
        del k, source_filter
        return [
            (
                Document(
                    page_content="深蹲时膝盖应追踪脚尖方向。",
                    metadata={
                        "source_id": "动作指南大全.txt",
                        "chunk_id": "chunk-squat",
                        "parent_id": "parent-squat",
                        "child_text": "膝盖应追踪脚尖方向。",
                        "ordinal": 7,
                    },
                ),
                3.2,
            )
        ]


class SplitQueryPlanner:
    """验证 RAG 服务会执行受控的两个子查询。"""

    @staticmethod
    def plan(query, _history):
        """将输入查询规划为两个固定的训练与饮食子查询。"""
        return QueryPlan(
            original_query=query,
            rewritten_query="新手减脂训练与饮食安排",
            search_queries=("新手减脂训练安排", "新手减脂饮食安排"),
            used_llm=True,
        )


class MetadataVectorStore:
    """构造分数接近的候选，验证标签仅作小幅、可观察的排序增强。"""

    @staticmethod
    def similarity_search(_query, _limit, _source_filter):
        """返回带动作和防护标签的两个接近分数候选。"""
        return [
            ScoredChunk(
                Document(
                    page_content="深蹲训练前需要进行动态热身。",
                    metadata={
                        "source_id": "动作指南.txt",
                        "chunk_id": "movement",
                        "parent_id": "parent-movement",
                        "child_text": "深蹲训练前需要进行动态热身。",
                        "tags": "动作,下肢",
                    },
                ),
                0.91,
            ),
            ScoredChunk(
                Document(
                    page_content="膝部疼痛时应停止诱发疼痛的动作并评估恢复。",
                    metadata={
                        "source_id": "运动防护.txt",
                        "chunk_id": "protection",
                        "parent_id": "parent-protection",
                        "child_text": "膝部疼痛时应停止诱发疼痛的动作并评估恢复。",
                        "tags": "防护,下肢",
                    },
                ),
                0.90,
            ),
        ]

    @staticmethod
    def active_revision():
        """返回固定的活动索引版本。"""
        return "revision-1"

    @staticmethod
    def health():
        """返回可用状态的固定仓储健康信息。"""
        return {"status": "ready"}


class EmptyBM25:
    @staticmethod
    def load_artifact(_path):
        """模拟无可用工件的空 BM25 检索器。"""
        return None

    @staticmethod
    def search(_query, _k, _source_filter=None):
        """返回空列表以关闭词法候选干扰。"""
        return []


class SourceQualityVectorStore:
    """构造来源相近、但外部原始三元组略占优势的候选集。"""

    @staticmethod
    def similarity_search(_query, _limit, _source_filter):
        """返回外部与精选来源的两个接近分数候选。"""
        return [
            ScoredChunk(
                Document(
                    page_content="高位下拉使用高位拉力训练机。",
                    metadata={
                        "source_id": "external/fitkg-cn/fitkg-cn-train.md",
                        "chunk_id": "external",
                        "parent_id": "parent-external",
                        "child_text": "高位下拉使用高位拉力训练机。",
                    },
                ),
                0.91,
            ),
            ScoredChunk(
                Document(
                    page_content="高位下拉先下沉肩胛，再用肘部带动。",
                    metadata={
                        "source_id": "动作指南大全.txt",
                        "chunk_id": "curated",
                        "parent_id": "parent-curated",
                        "child_text": "高位下拉先下沉肩胛，再用肘部带动。",
                    },
                ),
                0.90,
            ),
        ]

    @staticmethod
    def active_revision():
        """返回固定的活动索引版本。"""
        return "revision-1"

    @staticmethod
    def health():
        """返回可用状态的固定仓储健康信息。"""
        return {"status": "ready"}


class NearTieReranker:
    """模拟外部三元组因关键词命中而略高的原始精排分。"""

    @staticmethod
    def rerank(_query, candidates):
        """为候选返回受控的近似精排分数。"""
        scores = {"external/fitkg-cn/fitkg-cn-train.md\x1fexternal": 0.90}
        return [
            type(
                "Result",
                (),
                {"candidate_id": item.candidate_id, "score": scores.get(item.candidate_id, 0.87)},
            )
            for item in candidates
        ]


def test_retrieve_returns_stable_evidence_contract():
    """验证混合检索返回稳定的证据字段、排序信息和脱敏日志。"""
    vector_store = FakeVectorStore()
    service = RagSummarizeService(vector_store=vector_store, bm25=FakeBM25())

    result = service.retrieve("深蹲时膝盖怎么放？")

    assert result.index_revision == "revision-1"
    assert result.bm25_enabled is True
    assert result.vector_candidate_count == 2
    assert result.bm25_candidate_count == 1
    assert result.hits[0].evidence_id == "动作指南大全.txt#chunk-squat"
    assert result.hits[0].dense_rank == 1
    assert result.hits[0].bm25_rank == 1
    assert "深蹲" not in str(result.log_payload())


def test_build_context_includes_citable_evidence_markers():
    """验证构建上下文包含可引用的证据标记与来源标识。"""
    service = RagSummarizeService(vector_store=FakeVectorStore(), bm25=FakeBM25())

    context = service.build_context("深蹲时膝盖怎么放？").content

    assert "[证据:1]" in context
    assert "动作指南大全.txt#chunk-squat" in context
    assert "回答若采用以上资料" in context


def test_retrieve_runs_each_controlled_subquery_and_records_plan():
    """验证检索服务执行每个受控子查询并记录查询规划结果。"""
    vector_store = FakeVectorStore()
    service = RagSummarizeService(
        vector_store=vector_store,
        bm25=FakeBM25(),
        query_planner=SplitQueryPlanner(),
    )

    result = service.retrieve(
        "新手减脂怎么练和怎么吃？",
        history=[{"role": "user", "content": "我刚开始"}],
    )

    assert len(vector_store.queries) == 2
    assert result.search_queries == ("新手减脂训练安排", "新手减脂饮食安排")
    assert result.query_planner_used_llm is True
    assert result.hits[0].rerank_score is not None


def test_metadata_tags_boost_matching_evidence_without_hard_filtering():
    """验证匹配标签仅提升候选排序，不作为硬性筛选条件。"""
    service = RagSummarizeService(vector_store=MetadataVectorStore(), bm25=EmptyBM25())
    service.reranker_enabled = False

    result = service.retrieve("膝盖疼痛怎么处理？")

    assert result.query_tags == ("防护", "下肢")
    assert result.hits[0].chunk_id == "protection"
    assert result.hits[0].metadata_tag_score == 1.0
    assert result.log_payload()["query_tag_count"] == 2


def test_source_quality_penalty_softly_demotes_low_information_external_source():
    """验证来源质量惩罚会温和降低外部低信息证据的排序。"""
    service = RagSummarizeService(
        vector_store=SourceQualityVectorStore(),
        bm25=EmptyBM25(),
        reranker=NearTieReranker(),
    )
    service.source_quality_penalties = (("external/fitkg-cn/", 0.08),)

    result = service.retrieve("高位下拉怎样更多用背发力？")

    assert result.hits[0].chunk_id == "curated"
    external_hit = next(hit for hit in result.hits if hit.chunk_id == "external")
    assert external_hit.source_quality_penalty == 0.08
    assert external_hit.rerank_score == 0.828


def test_query_planner_can_be_disabled_without_calling_the_model():
    """验证关闭查询规划后不会调用规划器且使用原始查询。"""
    class UnexpectedPlanner:
        @staticmethod
        def plan(_query, _history):
            """若禁用规划后仍被调用则使测试失败。"""
            raise AssertionError("关闭查询规划后不应调用规划器")

    service = RagSummarizeService(
        vector_store=FakeVectorStore(),
        bm25=FakeBM25(),
        query_planner=UnexpectedPlanner(),
    )
    service.query_planner_enabled = False

    result = service.retrieve("那深蹲呢？", history=[{"role": "user", "content": "先聊硬拉"}])

    assert result.search_queries == ("那深蹲呢？",)
    assert result.query_planner_used_llm is False
