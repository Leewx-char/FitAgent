"""为当前 Qdrant 演示环境提供可重复执行的离线知识索引构建器。"""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from collections import Counter

from langchain_core.documents import Document
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

from app.core.settings import get_settings
from app.services.factory import get_embedding_model
from app.services.knowledge_enrichment import ContentDeduplicator, DeepTextCleaner, MetadataEnricher
from app.services.vector_repository import IndexedChunk, QdrantVectorRepository
from app.utils.config_handler import get_models_config, get_vector_store_config
from app.utils.file_handler import (
    clean_text,
    get_file_sha256_hex,
    listdir_with_allowed_type,
    normalize_documents,
    pdf_loader,
    split_qa_documents,
    txt_loader,
)
from app.utils.path_tool import get_abs_path
from app.utils.logger_handler import logger

_INDEX_NAMESPACE = uuid.UUID("25f3c970-1a3d-49b0-99e4-f8e7d24ca0d5")


@dataclass(frozen=True)
class IndexBuildResult:
    """一次不可变知识索引版本发布后的产物信息。"""

    revision: str
    collection_name: str
    chunk_count: int


@dataclass(frozen=True)
class IndexPreflightResult:
    """不依赖 embedding 或 Qdrant 的索引发布前质量检查结果。"""

    revision: str
    chunks: list[IndexedChunk]
    source_checksums: dict[str, str]
    source_document_counts: dict[str, int]
    source_chunk_counts: dict[str, int]
    content_processing: dict[str, int]
    rules: dict[str, int]
    warnings: tuple[str, ...]

    def report(self) -> dict:
        """返回可写入 JSON、可与 index manifest 一起审计的安全摘要。"""
        return {
            "status": "passed_with_warnings" if self.warnings else "passed",
            "rules": self.rules,
            "source_count": len(self.source_document_counts),
            "chunk_count": len(self.chunks),
            "sources": {
                source: {
                    "sha256": self.source_checksums[source],
                    "document_count": self.source_document_counts[source],
                    "chunk_count": self.source_chunk_counts.get(source, 0),
                }
                for source in sorted(self.source_checksums)
            },
            "content_processing": self.content_processing,
            "warnings": list(self.warnings),
        }


class KnowledgeIndexer:
    """完全离线地构建新版本，校验通过后再将其激活。"""

    def __init__(
        self,
        repository: QdrantVectorRepository | None = None,
        *,
        initialize_repository: bool = True,
    ) -> None:
        self.config = get_vector_store_config()
        self._settings = get_settings()
        self.repository = repository
        if initialize_repository and self.repository is None:
            self.repository = self._create_repository()
        self.recursive_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.config["chunk_size"],
            chunk_overlap=self.config["chunk_overlap"],
            separators=self.config["separators"],
            length_function=len,
        )
        self.parent_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.config["parent_chunk_size"],
            chunk_overlap=self.config["parent_chunk_overlap"],
            separators=self.config["separators"],
            length_function=len,
        )
        self.markdown_header_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=[("#", "文档标题"), ("##", "章节标题")],
            strip_headers=False,
        )
        self.cleaner = DeepTextCleaner()
        self.enricher = MetadataEnricher(max_tags=self.config["max_tags"])
        self._last_build_stats: dict[str, int] = {}

    def _create_repository(self) -> QdrantVectorRepository:
        return QdrantVectorRepository(
            collection_name=self.config["collection_alias"],
            url=self._settings.qdrant_url or self.config["url"],
            api_key=self._settings.qdrant_api_key or None,
            grpc_port=self.config["grpc_port"],
            prefer_grpc=self.config["prefer_grpc"],
            timeout_seconds=self.config["qdrant_timeout_seconds"],
        )

    def build_and_activate(self) -> IndexBuildResult:
        """从源文件创建、校验并激活一个完整的索引版本。"""
        if self.repository is None:
            self.repository = self._create_repository()
        preflight = self.preflight()
        revision = preflight.revision
        source_checksums = preflight.source_checksums
        chunks = self._attach_index_revision(preflight.chunks, revision)
        collection_name = f"{self.config['collection_prefix']}_{revision[:12]}"
        embedding_model = get_embedding_model()
        collection_created = False
        index_activated = False
        build_started = time.monotonic()

        try:
            first_vector = embedding_model.embed_documents([chunks[0].text])[0]
            self.repository.create_collection(collection_name, len(first_vector))
            collection_created = True

            batch_size = self.config["batch_size"]
            total_batches = math.ceil(len(chunks) / batch_size)
            progress_interval = max(1, total_batches // 20)
            logger.info(
                "开始构建知识库索引：revision=%s，切片=%s，批次=%s",
                revision[:12],
                len(chunks),
                total_batches,
            )
            for batch_number, start in enumerate(range(0, len(chunks), batch_size), start=1):
                batch = chunks[start : start + batch_size]
                vectors = embedding_model.embed_documents([chunk.text for chunk in batch])
                self.repository.upsert(collection_name, batch, vectors)
                if batch_number % progress_interval == 0 or batch_number == total_batches:
                    logger.info(
                        "知识库索引构建进度：%s/%s 批，%s/%s 切片，已用 %.1f 秒",
                        batch_number,
                        total_batches,
                        min(start + len(batch), len(chunks)),
                        len(chunks),
                        time.monotonic() - build_started,
                    )

            indexed_count = self.repository.client.count(
                collection_name=collection_name, exact=True
            ).count
            if indexed_count != len(chunks):
                raise RuntimeError(
                    f"Qdrant 写入校验失败：期望 {len(chunks)}，实际 {indexed_count}，未激活新索引。"
                )

            self.repository.activate_alias(self.config["collection_alias"], collection_name)
            index_activated = True
            self._write_artifacts(revision, collection_name, chunks, source_checksums, preflight)
            logger.info(
                "知识库索引已发布：revision=%s，collection=%s，切片=%s，用时 %.1f 秒",
                revision[:12],
                collection_name,
                len(chunks),
                time.monotonic() - build_started,
            )
            return IndexBuildResult(revision, collection_name, len(chunks))
        except Exception as error:
            if collection_created and not index_activated:
                try:
                    self.repository.delete_collection(collection_name)
                    logger.warning("知识库索引构建失败，已清理未发布集合：%s", collection_name)
                except Exception:
                    logger.exception("知识库索引构建失败，且未能清理集合：%s", collection_name)
            elif index_activated:
                logger.error(
                    "索引别名已切换到 %s，后续步骤失败；为避免删除正在服务的集合，未自动清理。",
                    collection_name,
                )

            message = self._build_failure_message(error)
            logger.error(message, exc_info=True)
            raise RuntimeError(message) from error

    def preflight(self) -> IndexPreflightResult:
        """读取、清洗和切片知识源，阻止明显异常的数据集进入昂贵的构建阶段。"""
        source_documents, source_checksums = self._load_source_documents()
        source_document_counts = {source: len(documents) for source, documents in source_documents}
        chunks = self._build_chunks(source_documents, source_checksums)
        source_chunk_counts = Counter(str(chunk.metadata.get("source_id", "")) for chunk in chunks)
        warnings = self._validate_preflight(
            source_checksums=source_checksums,
            chunks=chunks,
            source_chunk_counts=source_chunk_counts,
        )
        rules = {
            "min_source_count": int(self.config.get("min_source_count", 1)),
            "min_chunk_count": int(self.config.get("min_chunk_count", 1)),
        }
        return IndexPreflightResult(
            revision=self._build_revision(source_checksums),
            chunks=chunks,
            source_checksums=source_checksums,
            source_document_counts=source_document_counts,
            source_chunk_counts=dict(source_chunk_counts),
            content_processing=dict(getattr(self, "_last_build_stats", {})),
            rules=rules,
            warnings=tuple(warnings),
        )

    def _validate_preflight(
        self,
        *,
        source_checksums: dict[str, str],
        chunks: list[IndexedChunk],
        source_chunk_counts: Counter[str],
    ) -> list[str]:
        """校验发布必须满足的不变量，并标记可由人工确认的来源告警。"""
        min_source_count = int(self.config.get("min_source_count", 1))
        min_chunk_count = int(self.config.get("min_chunk_count", 1))
        if len(source_checksums) < min_source_count:
            raise RuntimeError(
                "知识库预检失败：有效来源数不足，"
                f"要求至少 {min_source_count} 个，实际 {len(source_checksums)} 个。"
            )
        if len(chunks) < min_chunk_count:
            raise RuntimeError(
                "知识库预检失败：有效切片数不足，"
                f"要求至少 {min_chunk_count} 个，实际 {len(chunks)} 个。"
            )

        chunk_ids = [chunk.chunk_id for chunk in chunks]
        if len(chunk_ids) != len(set(chunk_ids)):
            raise RuntimeError("知识库预检失败：检测到重复 chunk_id，未创建 Qdrant collection。")
        unknown_sources = sorted(set(source_chunk_counts).difference(source_checksums))
        if unknown_sources:
            raise RuntimeError(
                "知识库预检失败：切片引用了未登记来源：" + ", ".join(unknown_sources)
            )

        return [
            f"来源 {source} 已加载但在清洗/去重后没有保留切片，请确认是否符合预期。"
            for source in sorted(source_checksums)
            if source_chunk_counts.get(source, 0) == 0
        ]

    @staticmethod
    def _build_failure_message(error: Exception) -> str:
        """将常见外部依赖错误转换为可直接处理的中文提示。"""
        detail = str(error)
        normalized = detail.lower()
        if "proxyerror" in normalized or "unable to connect to proxy" in normalized:
            return (
                "DashScope 向量化失败：当前终端的代理连接不可用。"
                "请检查或清除 HTTP_PROXY、HTTPS_PROXY、ALL_PROXY 后重试。"
            )
        if "dashscope.aliyuncs.com" in normalized:
            return "DashScope 向量化失败：无法连接服务。请检查网络、API Key 和代理设置后重试。"
        return f"知识库索引构建失败：{detail}"

    @staticmethod
    def _attach_index_revision(chunks: list[IndexedChunk], revision: str) -> list[IndexedChunk]:
        return [
            IndexedChunk(chunk.chunk_id, chunk.text, {**chunk.metadata, "index_revision": revision})
            for chunk in chunks
        ]

    def _load_source_documents(self) -> tuple[list[tuple[str, list[Document]]], dict[str, str]]:
        data_path = get_abs_path(self.config["data_path"])
        allowed_types = tuple(self.config["allow_knowledge_file_type"])
        paths = listdir_with_allowed_type(data_path, allowed_types)
        documents_by_source: list[tuple[str, list[Document]]] = []
        checksums: dict[str, str] = {}

        for path in paths:
            source = os.path.relpath(path, data_path).replace("\\", "/")
            checksum = get_file_sha256_hex(path)
            if not checksum:
                continue
            documents = self._load_file(path)
            documents = normalize_documents(documents)
            if source.endswith("100问.txt") or (
                documents and "常见问题" in clean_text(documents[0].page_content[:80])
            ):
                documents = split_qa_documents(documents)
            if documents:
                documents_by_source.append((source, documents))
                checksums[source] = checksum
        return documents_by_source, checksums

    @staticmethod
    def _load_file(path: str) -> list[Document]:
        suffix = Path(path).suffix.lower()
        if suffix == ".txt":
            return txt_loader(path)
        if suffix == ".md":
            return txt_loader(path)
        if suffix == ".pdf":
            return pdf_loader(path)
        return []

    def _split_source_documents(self, source: str, documents: list[Document]) -> list[Document]:
        """按文件类型切分；Markdown 先按标题分段，再递归控制每段长度。"""
        if not source.endswith(".md"):
            return self.recursive_splitter.split_documents(documents)

        split_documents: list[Document] = []
        for document in documents:
            header_documents = self.markdown_header_splitter.split_text(document.page_content)
            for header_document in header_documents:
                if "章节标题" not in header_document.metadata:
                    continue
                header_document.metadata = {**document.metadata, **header_document.metadata}
                split_documents.extend(self.recursive_splitter.split_documents([header_document]))
        return split_documents

    def _split_parent_documents(self, source: str, documents: list[Document]) -> list[Document]:
        """按标题形成语义父段，再以较大窗口限制父段长度。"""
        if not source.endswith(".md"):
            return self.parent_splitter.split_documents(documents)

        parent_documents: list[Document] = []
        for document in documents:
            header_documents = self.markdown_header_splitter.split_text(document.page_content)
            for header_document in header_documents:
                if "章节标题" not in header_document.metadata:
                    continue
                header_document.metadata = {**document.metadata, **header_document.metadata}
                parent_documents.extend(self.parent_splitter.split_documents([header_document]))
        return parent_documents

    def _build_chunks(
        self,
        documents_by_source: list[tuple[str, list[Document]]],
        source_checksums: dict[str, str],
    ) -> list[IndexedChunk]:
        chunks: list[IndexedChunk] = []
        deduplicator = ContentDeduplicator(self.config["near_duplicate_hamming_distance"])
        dropped_empty = 0
        for source, documents in documents_by_source:
            parent_documents = self._split_parent_documents(source, documents)
            source_revision = source_checksums[source]
            for parent_ordinal, parent_document in enumerate(parent_documents):
                parent_text = self.cleaner.clean(parent_document.page_content)
                if not parent_text:
                    dropped_empty += 1
                    continue
                parent_identity = f"{source}:{source_revision}:parent:{parent_ordinal}"
                parent_id = str(uuid.uuid5(_INDEX_NAMESPACE, parent_identity))
                child_documents = self.recursive_splitter.split_documents(
                    [Document(page_content=parent_text, metadata=parent_document.metadata)]
                )
                for child_ordinal, child_document in enumerate(child_documents):
                    child_text = self.cleaner.clean(child_document.page_content)
                    if not child_text:
                        dropped_empty += 1
                        continue
                    if deduplicator.is_duplicate(child_text):
                        continue

                    identity = f"{source}:{source_revision}:{parent_ordinal}:{child_ordinal}"
                    chunk_id = str(uuid.uuid5(_INDEX_NAMESPACE, identity))
                    title = str(child_document.metadata.get("章节标题", ""))
                    enriched = self.enricher.enrich(child_text, title)
                    metadata: dict[str, str | int | float | bool] = {
                        "chunk_id": chunk_id,
                        "source_id": source,
                        "source_revision": source_revision,
                        "source_type": Path(source).suffix.lstrip(".").lower(),
                        "ordinal": len(chunks),
                        "parent_id": parent_id,
                        "parent_text": parent_text,
                        "tags": ",".join(enriched.tags),
                    }
                    for key, value in child_document.metadata.items():
                        if key == "source":
                            continue
                        if isinstance(value, (str, int, float, bool)):
                            metadata[key] = value
                    chunks.append(IndexedChunk(chunk_id, enriched.text, metadata))
        self._last_build_stats = {
            "indexed_chunks": len(chunks),
            "dropped_empty_chunks": dropped_empty,
            "exact_duplicate_chunks": deduplicator.exact_duplicates,
            "near_duplicate_chunks": deduplicator.near_duplicates,
        }
        logger.info("知识内容清洗与去重完成：%s", self._last_build_stats)
        return chunks

    def _build_revision(self, source_checksums: dict[str, str]) -> str:
        revision_input = {
            "schema": 3,
            "sources": source_checksums,
            "chunk_size": self.config["chunk_size"],
            "chunk_overlap": self.config["chunk_overlap"],
            "parent_chunk_size": self.config["parent_chunk_size"],
            "parent_chunk_overlap": self.config["parent_chunk_overlap"],
            "near_duplicate_hamming_distance": self.config["near_duplicate_hamming_distance"],
            "max_tags": self.config["max_tags"],
            "embedding_model": get_models_config()["embedding_model_name"],
        }
        serialized = json.dumps(revision_input, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(serialized).hexdigest()

    def _write_artifacts(
        self,
        revision: str,
        collection_name: str,
        chunks: list[IndexedChunk],
        source_checksums: dict[str, str],
        preflight: IndexPreflightResult,
    ) -> None:
        manifest = {
            "index_revision": revision,
            "collection_name": collection_name,
            "chunk_count": len(chunks),
            "source_checksums": source_checksums,
            "content_processing": self._last_build_stats,
            "preflight": preflight.report(),
            "built_at": datetime.now(timezone.utc).isoformat(),
        }
        self._write_json(self.config["manifest_path"], manifest)
        self._write_json(
            self.config["bm25_artifact_path"],
            {
                "index_revision": revision,
                "documents": [
                    {"page_content": chunk.text, "metadata": chunk.metadata} for chunk in chunks
                ],
            },
        )

    def write_preflight_report(self, result: IndexPreflightResult) -> None:
        """保存只读预检报告，供构建前人工复核或 CI 门禁使用。"""
        report = {
            "index_revision": result.revision,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            **result.report(),
        }
        self._write_json(self.config["preflight_report_path"], report)

    @staticmethod
    def _write_json(relative_path: str, content: dict) -> None:
        path = Path(get_abs_path(relative_path))
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(path)


def main() -> None:
    """命令行入口：``python -m app.services.knowledge_indexer``。"""
    result = KnowledgeIndexer().build_and_activate()
    print(
        f"知识库索引已激活：revision={result.revision[:12]} "
        f"collection={result.collection_name} chunks={result.chunk_count}"
    )


if __name__ == "__main__":
    main()
