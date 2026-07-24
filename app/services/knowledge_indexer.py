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


class KnowledgeIndexer:
    """完全离线地构建新版本，校验通过后再将其激活。"""

    def __init__(self, repository: QdrantVectorRepository | None = None) -> None:
        self.config = get_vector_store_config()
        settings = get_settings()
        self.repository = repository or QdrantVectorRepository(
            collection_name=self.config["collection_alias"],
            url=settings.qdrant_url or self.config["url"],
            api_key=settings.qdrant_api_key or None,
            grpc_port=self.config["grpc_port"],
            prefer_grpc=self.config["prefer_grpc"],
            timeout_seconds=self.config["qdrant_timeout_seconds"],
        )
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
        self.enricher = MetadataEnricher(
            summary_max_chars=self.config["summary_max_chars"],
            max_tags=self.config["max_tags"],
        )
        self._last_build_stats: dict[str, int] = {}

    def build_and_activate(self) -> IndexBuildResult:
        """从源文件创建、校验并激活一个完整的索引版本。"""
        source_documents, source_checksums = self._load_source_documents()
        chunks = self._build_chunks(source_documents, source_checksums)
        if not chunks:
            raise RuntimeError("知识库没有可索引的有效文本，未创建 Qdrant collection。")

        revision = self._build_revision(source_checksums)
        chunks = self._attach_index_revision(chunks, revision)
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
            self._write_artifacts(revision, collection_name, chunks, source_checksums)
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
                        "summary": enriched.summary,
                        "tags": enriched.tags,
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
            "schema": 2,
            "sources": source_checksums,
            "chunk_size": self.config["chunk_size"],
            "chunk_overlap": self.config["chunk_overlap"],
            "parent_chunk_size": self.config["parent_chunk_size"],
            "parent_chunk_overlap": self.config["parent_chunk_overlap"],
            "near_duplicate_hamming_distance": self.config["near_duplicate_hamming_distance"],
            "summary_max_chars": self.config["summary_max_chars"],
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
    ) -> None:
        manifest = {
            "index_revision": revision,
            "collection_name": collection_name,
            "chunk_count": len(chunks),
            "source_checksums": source_checksums,
            "content_processing": self._last_build_stats,
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
