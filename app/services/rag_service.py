"""基于活动 Qdrant revision 的在线 RAG V1 编排服务。"""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace

from langchain_core.documents import Document

from app.core.request_context import request_id_var
from app.services.bm25_retriever import BM25Retriever
from app.services.context_builder import ContextBuilder
from app.services.knowledge_enrichment import MetadataEnricher
from app.services.query_planner import QueryPlan, QueryPlanner
from app.services.reranker import LexicalReranker, RerankCandidate, Reranker
from app.services.retrieval_contracts import RetrievalHit, RetrievalRequest, RetrievalResult
from app.services.vector_store import VectorStoreService
from app.utils.config_handler import get_synonyms_config, get_vector_store_config
from app.utils.logger_handler import logger
from app.utils.path_tool import get_abs_path


@dataclass(frozen=True)
class _FusedDocument:
    """RRF 与重排序阶段的内部候选对象。"""

    document: Document
    score: float
    dense_rank: int | None
    bm25_rank: int | None
    rerank_score: float | None = None
    metadata_tag_score: float | None = None
    source_quality_penalty: float = 0.0


@dataclass(frozen=True)
class RagContext:
    """供 Agent 使用的文本上下文及其可展示证据。

    ``content`` 保持既有工具接口兼容；``result`` 让 SSE 层无需解析模型提示文本，
    直接把真实命中的证据安全地交给前端展示。
    """

    content: str
    result: RetrievalResult | None


class RagSummarizeService:
    """查询理解、混合召回、重排序、上下文预算与证据输出的在线 RAG 服务。"""

    def __init__(
        self,
        vector_store: VectorStoreService | None = None,
        bm25: BM25Retriever | None = None,
        query_planner: QueryPlanner | None = None,
        reranker: Reranker | None = None,
        context_builder: ContextBuilder | None = None,
    ) -> None:
        """按配置组装检索、规划、精排和上下文构建组件。"""
        config = get_vector_store_config()
        self.vector_store = vector_store or VectorStoreService()
        self.top_k = config["k"]
        self.candidate_k = config.get("candidate_k", max(self.top_k * 2, self.top_k))
        self.min_relevance_score = config.get("min_relevance_score", 0.0)
        self.query_planner_enabled = config.get("query_planner_enabled", True)
        self.reranker_enabled = config.get("reranker_enabled", True)
        self.reranker_candidate_k = config.get("reranker_candidate_k", self.candidate_k)
        self.source_quality_penalties = self._load_source_quality_penalties(config)
        self.metadata_tag_boost_enabled = config.get("metadata_tag_boost_enabled", True)
        self.metadata_tag_boost_weight = config.get("metadata_tag_boost_weight", 0.15)
        self.synonym_map = get_synonyms_config().get("expand", {})
        self.normalize_map = get_synonyms_config().get("normalize", {})
        self.bm25 = bm25 or BM25Retriever()
        self.bm25_revision = self.bm25.load_artifact(get_abs_path(config["bm25_artifact_path"]))
        self.query_planner = query_planner or QueryPlanner(
            history_turns=config.get("query_history_turns", 3),
            max_subqueries=config.get("max_subqueries", 2),
        )
        self.reranker = reranker or LexicalReranker(
            base_score_weight=config.get("reranker_base_score_weight", 0.7)
        )
        self.metadata_enricher = MetadataEnricher(max_tags=config.get("max_tags", 4))
        self.context_builder = context_builder or ContextBuilder(
            max_context_chars=config.get("max_context_chars", 6000),
            max_chars_per_evidence=config.get("max_chars_per_evidence", 1200),
        )

    @staticmethod
    def _load_source_quality_penalties(config: dict) -> tuple[tuple[str, float], ...]:
        """读取来源前缀软惩罚；仅调候选排序，不影响召回或执行硬过滤。"""
        raw_penalties = config.get("source_quality_penalties", {})
        if not isinstance(raw_penalties, dict):
            return ()
        parsed = []
        for prefix, value in raw_penalties.items():
            try:
                penalty = float(value)
            except (TypeError, ValueError):
                logger.warning("忽略无效的来源质量惩罚：%s=%r", prefix, value)
                continue
            if not str(prefix) or not 0 <= penalty < 1:
                logger.warning("忽略超出范围的来源质量惩罚：%s=%r", prefix, value)
                continue
            parsed.append((str(prefix), penalty))
        return tuple(sorted(parsed, key=lambda item: len(item[0]), reverse=True))

    def readiness(self) -> dict[str, int | str]:
        """返回 Qdrant 就绪状态；该方法绝不导入或重建文档。"""

        return self.vector_store.health()

    def _normalize_query(self, query: str) -> str:
        """统一查询空白、大小写和配置的规范词替换。"""
        normalized = re.sub(r"\s+", " ", query.strip().lower())
        for source, target in self.normalize_map.items():
            normalized = normalized.replace(source, target)
        return normalized

    def _expand_query(self, query: str) -> str:
        """为命中同义词规则的规范查询补充扩展词。"""
        normalized = self._normalize_query(query)
        expansions = [
            candidate
            for phrase, candidates in self.synonym_map.items()
            if phrase in normalized
            for candidate in candidates
        ]
        return f"{normalized} {' '.join(expansions)}" if expansions else normalized

    @staticmethod
    def _document_key(document: Document) -> tuple[str, str]:
        """用离线构建的稳定切片标识合并不同检索路径的结果。"""

        return (
            str(document.metadata.get("source_id", document.metadata.get("source", ""))),
            str(document.metadata.get("chunk_id", document.metadata.get("ordinal", ""))),
        )

    @staticmethod
    def _candidate_id(candidate: _FusedDocument) -> str:
        """根据融合文档的稳定键生成候选标识。"""
        return "\x1f".join(RagSummarizeService._document_key(candidate.document))

    @staticmethod
    def _document_terms(content: str) -> set[str]:
        """提取文档正文和标签中的规范词，用于元数据匹配。"""
        return set(re.findall(r"[一-鿿]{2,}|[a-z0-9]+", content.lower()))

    def _deduplicate_docs(
        self, scored_docs: list[_FusedDocument], threshold: float = 0.8
    ) -> list[_FusedDocument]:
        """避免同一父段或高重叠上下文多次占用最终上下文预算。"""

        kept: list[_FusedDocument] = []
        kept_terms: list[set[str]] = []
        for candidate in scored_docs:
            terms = self._document_terms(candidate.document.page_content)
            if any(
                (union := len(terms | previous)) and len(terms & previous) / union > threshold
                for previous in kept_terms
            ):
                continue
            kept.append(candidate)
            kept_terms.append(terms)
        return kept

    @staticmethod
    def _document_tags(document: Document) -> tuple[str, ...]:
        """读取离线写入的逗号分隔标签，并忽略旧索引中缺失该字段的切片。"""
        raw_tags = str(document.metadata.get("tags", ""))
        return tuple(tag.strip() for tag in raw_tags.split(",") if tag.strip())

    def _apply_metadata_tag_boost(
        self, candidates: list[_FusedDocument], query_tags: tuple[str, ...]
    ) -> list[_FusedDocument]:
        """以有限标签匹配微调 RRF 候选排序；标签不硬过滤且不应压倒语义召回。"""
        if not self.metadata_tag_boost_enabled or not query_tags:
            return candidates

        query_tag_set = set(query_tags)
        boosted = []
        for candidate in candidates:
            document_tags = set(self._document_tags(candidate.document))
            overlap = query_tag_set & document_tags
            tag_score = len(overlap) / len(query_tag_set)
            boosted.append(
                replace(
                    candidate,
                    score=candidate.score * (1 + self.metadata_tag_boost_weight * tag_score),
                    metadata_tag_score=round(tag_score, 4) if overlap else 0.0,
                )
            )
        return sorted(boosted, key=lambda item: item.score, reverse=True)

    @staticmethod
    def _rrf_fusion(
        vector_results: list[tuple[Document, float]],
        bm25_results: list[tuple[Document, float]],
        k: int = 60,
    ) -> list[_FusedDocument]:
        """按稳定切片 ID 融合多查询的 dense 与 BM25 粗召回结果。"""

        records: dict[tuple[str, str], dict] = {}
        for route_name, results in (("dense", vector_results), ("bm25", bm25_results)):
            for rank, (document, _) in enumerate(results, start=1):
                key = RagSummarizeService._document_key(document)
                record = records.setdefault(
                    key,
                    {
                        "document": document,
                        "score": 0.0,
                        "dense_rank": None,
                        "bm25_rank": None,
                    },
                )
                record["score"] += 1.0 / (k + rank)
                previous_rank = record[f"{route_name}_rank"]
                record[f"{route_name}_rank"] = (
                    rank if previous_rank is None else min(previous_rank, rank)
                )
        fused = [
            _FusedDocument(
                document=record["document"],
                score=record["score"],
                dense_rank=record["dense_rank"],
                bm25_rank=record["bm25_rank"],
            )
            for record in records.values()
        ]
        return sorted(fused, key=lambda item: item.score, reverse=True)

    def _collect_candidates(
        self,
        plan: QueryPlan,
        source_filter: list[str] | None,
        bm25_enabled: bool,
    ) -> tuple[list[tuple[Document, float]], list[tuple[Document, float]]]:
        """并行执行每个子查询的 dense/BM25 召回；dense 失败视为检索不可用。"""

        vector_results: list[tuple[Document, float]] = []
        bm25_results: list[tuple[Document, float]] = []
        # 按查询计划顺序聚合结果，不以完成顺序排序，避免不同延迟导致相同请求得到不同 RRF 排名。
        tasks: list[tuple[str, str, Future]] = []
        with ThreadPoolExecutor(max_workers=max(2, len(plan.search_queries) * 2)) as executor:
            for search_query in plan.search_queries:
                expanded_query = self._expand_query(search_query)
                tasks.append(
                    (
                        "dense",
                        expanded_query,
                        executor.submit(
                            self.vector_store.similarity_search,
                            expanded_query,
                            self.candidate_k,
                            source_filter,
                        ),
                    )
                )
            if bm25_enabled:
                for search_query in plan.search_queries:
                    expanded_query = self._expand_query(search_query)
                    tasks.append(
                        (
                            "bm25",
                            expanded_query,
                            executor.submit(
                                self.bm25.search,
                                expanded_query,
                                self.candidate_k,
                                source_filter,
                            ),
                        )
                    )

            for route_name, expanded_query, task in tasks:
                try:
                    value = task.result()
                except Exception as error:
                    if route_name == "dense":
                        logger.error("Qdrant 向量检索失败：%s", error, exc_info=True)
                        raise RuntimeError("知识库检索暂时不可用，请稍后重试。") from error
                    logger.warning("BM25 检索失败，已降级为 dense：%s", error, exc_info=True)
                    continue
                if route_name == "dense":
                    vector_results.extend(
                        (item.document, item.score)
                        for item in value
                        if item.score >= self.min_relevance_score
                    )
                else:
                    bm25_results.extend(value)
                logger.debug(
                    "RAG 子查询完成：route=%s query_length=%s", route_name, len(expanded_query)
                )
        return vector_results, bm25_results

    def _rerank(self, query: str, candidates: list[_FusedDocument]) -> list[_FusedDocument]:
        """在有限候选集上进行可关闭的轻量重排序，保留 RRF 作为基础分。"""

        if not self.reranker_enabled or len(candidates) <= 1:
            return candidates[: self.top_k]
        candidate_map = {self._candidate_id(candidate): candidate for candidate in candidates}
        rerank_inputs = [
            RerankCandidate(
                candidate_id=candidate_id,
                text=str(
                    candidate.document.metadata.get("child_text", candidate.document.page_content)
                ),
                base_score=candidate.score,
            )
            for candidate_id, candidate in candidate_map.items()
        ]
        reranked = self.reranker.rerank(query, rerank_inputs)
        adjusted = []
        for item in reranked:
            candidate = candidate_map[item.candidate_id]
            penalty = self._source_quality_penalty(candidate.document)
            adjusted.append(
                replace(
                    candidate,
                    rerank_score=round(item.score * (1 - penalty), 8),
                    source_quality_penalty=penalty,
                )
            )
        return sorted(
            adjusted,
            key=lambda candidate: candidate.rerank_score or 0.0,
            reverse=True,
        )[: self.top_k]

    def _source_quality_penalty(self, document: Document) -> float:
        """返回最具体来源前缀对应的软惩罚，未匹配来源保持原始排序分。"""
        source_id = str(document.metadata.get("source_id", document.metadata.get("source", "")))
        return next(
            (
                penalty
                for prefix, penalty in self.source_quality_penalties
                if source_id.startswith(prefix)
            ),
            0.0,
        )

    @staticmethod
    def _to_hit(candidate: _FusedDocument, rank: int) -> RetrievalHit:
        """将融合候选及其排序信息转换为公开检索证据。"""
        metadata = {
            str(key): value
            for key, value in candidate.document.metadata.items()
            if isinstance(value, (str, int, float, bool))
        }
        source_id = str(metadata.get("source_id", metadata.get("source", "未知来源")))
        chunk_id = str(metadata.get("chunk_id", metadata.get("ordinal", "unknown")))
        parent_id = str(metadata.get("parent_id", chunk_id))
        return RetrievalHit(
            evidence_id=f"{source_id}#{chunk_id}",
            source_id=source_id,
            chunk_id=chunk_id,
            parent_id=parent_id,
            text=candidate.document.page_content,
            child_text=str(metadata.get("child_text", candidate.document.page_content)),
            rank=rank,
            score=round(candidate.score, 8),
            dense_rank=candidate.dense_rank,
            bm25_rank=candidate.bm25_rank,
            rerank_score=candidate.rerank_score,
            metadata=metadata,
            metadata_tag_score=candidate.metadata_tag_score,
            source_quality_penalty=candidate.source_quality_penalty,
        )

    def retrieve(
        self,
        query: str,
        source_filter: list[str] | None = None,
        history: list[dict] | None = None,
    ) -> RetrievalResult:
        """执行查询理解、混合召回、重排序；全过程只读活动索引。"""

        request = RetrievalRequest(
            query=query,
            source_filter=tuple(source_filter or ()),
            request_id=request_id_var.get(),
        )
        started_at = time.perf_counter()
        plan = (
            self.query_planner.plan(query, history)
            if self.query_planner_enabled
            else QueryPlan(query, (query,), query, False)
        )
        query_tags = self.metadata_enricher.extract_tags(plan.rewritten_query)
        active_revision = self.vector_store.active_revision()
        bm25_enabled = bool(self.bm25_revision and self.bm25_revision == active_revision)
        if self.bm25_revision and not bm25_enabled:
            logger.warning(
                "BM25 工件 revision 与 Qdrant 不一致，降级为 dense：%s != %s",
                self.bm25_revision,
                active_revision,
            )
        vector_results, bm25_results = self._collect_candidates(plan, source_filter, bm25_enabled)
        fused = self._deduplicate_docs(self._rrf_fusion(vector_results, bm25_results))
        fused = self._apply_metadata_tag_boost(fused, query_tags)
        selected = self._rerank(plan.rewritten_query, fused[: self.reranker_candidate_k])
        result = RetrievalResult(
            request=request,
            expanded_query=" | ".join(self._expand_query(item) for item in plan.search_queries),
            search_queries=plan.search_queries,
            index_revision=active_revision,
            hits=tuple(self._to_hit(candidate, rank) for rank, candidate in enumerate(selected, 1)),
            vector_candidate_count=len(vector_results),
            bm25_candidate_count=len(bm25_results),
            elapsed_ms=round((time.perf_counter() - started_at) * 1000),
            bm25_enabled=bm25_enabled,
            query_planner_used_llm=plan.used_llm,
            query_tags=query_tags,
            query_planner_fallback_reason=plan.fallback_reason,
        )
        logger.info("RAG_RETRIEVAL %s", json.dumps(result.log_payload(), ensure_ascii=False))
        return result

    @staticmethod
    def _format_references(hits: tuple[RetrievalHit, ...]) -> str:
        """给 Agent 提供稳定证据目录，而不是仅列出模糊文件名。"""

        entries = [f"[证据:{hit.rank}] {hit.evidence_id} | 来源={hit.source_id}" for hit in hits]
        return "\n证据目录：\n" + "\n".join(entries) if entries else ""

    def build_context(
        self,
        query: str,
        source_filter: list[str] | None = None,
        history: list[dict] | None = None,
    ) -> RagContext:
        """构建预算内上下文，并保留结构化证据供 API 层展示。"""

        try:
            result = self.retrieve(query, source_filter, history)
        except RuntimeError as error:
            return RagContext(str(error), None)
        if not result.hits:
            return RagContext("未检索到相关参考资料。", result)

        snippets = {
            snippet.evidence_id: snippet for snippet in self.context_builder.build(result.hits)
        }
        context_parts = []
        for hit in result.hits:
            snippet = snippets.get(hit.evidence_id)
            if snippet is None:
                continue
            location = hit.metadata.get("ordinal")
            location_text = f"来源={hit.source_id} | 证据ID={hit.evidence_id}"
            if location is not None:
                location_text += f" | 切片={location}"
            truncation = " | 已按上下文预算截取" if snippet.truncated else ""
            context_parts.append(
                f"[证据:{hit.rank}] {location_text}{truncation}\n{snippet.text.strip()}"
            )
        return RagContext(
            "\n\n".join(context_parts)
            + self._format_references(result.hits)
            + "\n回答若采用以上资料，请在相应结论后保留 [证据:N] 标记。",
            result,
        )
