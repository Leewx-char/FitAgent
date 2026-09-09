"""为 Qdrant 提供可重复执行的离线知识索引构建器。"""

from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

from app.core.settings import get_settings
from app.services.factory import get_embedding_model
from app.services.vector_repository import IndexedChunk, QdrantVectorRepository
from app.utils.config_handler import get_vector_store_config
from app.utils.file_handler import (
    listdir_with_allowed_type,
    normalize_documents,
    pdf_loader,
    txt_loader,
)
from app.utils.logger_handler import logger
from app.utils.path_tool import get_abs_path

_INDEX_NAMESPACE = uuid.UUID("25f3c970-1a3d-49b0-99e4-f8e7d24ca0d5")


@dataclass(frozen=True)
class IndexBuildResult:
    """一次知识索引重建的结果。"""

    collection_name: str
    chunk_count: int


class KnowledgeIndexer:
    """加载、分块并一次性重建离线知识索引。"""

    def __init__(
        self,
        repository: QdrantVectorRepository | None = None,
        *,
        initialize_repository: bool = True,
    ) -> None:
        """加载分块配置及可选的 Qdrant 仓储。"""
        self.config = get_vector_store_config()
        self._settings = get_settings()
        self.repository = repository
        if initialize_repository and self.repository is None:
            self.repository = self._create_repository()
        self.recursive_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.config["chunk_size"],
            chunk_overlap=self.config["chunk_overlap"],
            separators=self.config.get("separators", ["\n\n", "\n", "。", "？", "！", " ", ""]),
            length_function=len,
        )
        self.markdown_header_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=[("#", "文档标题"), ("##", "章节标题")],
            strip_headers=False,
        )

    def _create_repository(self) -> QdrantVectorRepository:
        """按当前配置创建 Qdrant 向量仓储。"""
        return QdrantVectorRepository(
            collection_name=self.config["collection_name"],
            url=self._settings.qdrant_url or self.config["url"],
            api_key=self._settings.qdrant_api_key or None,
            grpc_port=self.config["grpc_port"],
            prefer_grpc=self.config["prefer_grpc"],
            timeout_seconds=self.config["qdrant_timeout_seconds"],
        )

    def build(self) -> IndexBuildResult:
        """预检和向量化完成后，一次性破坏性重建 Qdrant collection。"""
        if self.repository is None:
            self.repository = self._create_repository()
        chunks = self.preflight()
        vectors = get_embedding_model().embed_documents([chunk.text for chunk in chunks])
        self.repository.rebuild(chunks, vectors)
        logger.info(
            "知识库索引已重建：collection=%s，切片=%s", self.config["collection_name"], len(chunks)
        )
        return IndexBuildResult(
            collection_name=self.config["collection_name"], chunk_count=len(chunks)
        )

    def preflight(self) -> list[IndexedChunk]:
        """读取和分块知识源，阻止空数据集进入 embedding 阶段。"""
        source_documents = self._load_source_documents()
        self._validate_source_count(source_documents)
        chunks = self._build_chunks(source_documents)
        self._validate_chunk_count(chunks)
        return chunks

    def _validate_source_count(self, source_documents: list[tuple[str, list[Document]]]) -> None:
        """校验最少有效来源数。"""
        min_source_count = int(self.config.get("min_source_count", 1))
        if len(source_documents) < min_source_count:
            raise RuntimeError(
                "知识库预检失败：有效来源数不足，"
                f"要求至少 {min_source_count} 个，实际 {len(source_documents)} 个。"
            )

    def _validate_chunk_count(self, chunks: list[IndexedChunk]) -> None:
        """校验最少有效切片数。"""
        min_chunk_count = int(self.config.get("min_chunk_count", 1))
        if len(chunks) < min_chunk_count:
            raise RuntimeError(
                "知识库预检失败：有效切片数不足，"
                f"要求至少 {min_chunk_count} 个，实际 {len(chunks)} 个。"
            )

    def _load_source_documents(self) -> list[tuple[str, list[Document]]]:
        """加载来源文件，并在 loader 后立即规范化文档。"""
        data_path = get_abs_path(self.config["data_path"])
        allowed_types = tuple(self.config["allow_knowledge_file_type"])
        documents_by_source: list[tuple[str, list[Document]]] = []

        for path in listdir_with_allowed_type(data_path, allowed_types):
            documents = normalize_documents(self._load_file(path))
            if documents:
                source = os.path.relpath(path, data_path).replace("\\", "/")
                documents_by_source.append((source, documents))
        return documents_by_source

    @staticmethod
    def _load_file(path: str) -> list[Document]:
        """按文件后缀加载 TXT、Markdown 或 PDF 文档。"""
        suffix = Path(path).suffix.lower()
        if suffix in {".txt", ".md"}:
            return txt_loader(path)
        if suffix == ".pdf":
            return pdf_loader(path)
        return []

    def _split_source_documents(self, source: str, documents: list[Document]) -> list[Document]:
        """以 Markdown 标题分段后，或直接对其它来源递归分块。"""
        if not source.endswith(".md"):
            return self.recursive_splitter.split_documents(documents)

        chunks: list[Document] = []
        for document in documents:
            for section in self.markdown_header_splitter.split_text(document.page_content):
                section.metadata = {**document.metadata, **section.metadata}
                chunks.extend(self.recursive_splitter.split_documents([section]))
        return chunks

    def _build_chunks(
        self, documents_by_source: list[tuple[str, list[Document]]]
    ) -> list[IndexedChunk]:
        """以最终分块的精确 UTF-8 SHA-256 去重并保留标量 loader 元数据。"""
        chunks: list[IndexedChunk] = []
        seen_hashes: set[str] = set()

        for source, documents in documents_by_source:
            for document_ordinal, document in enumerate(documents):
                split_documents = self._split_source_documents(source, [document])
                for chunk_ordinal, split_document in enumerate(split_documents):
                    text = split_document.page_content
                    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
                    if text_hash in seen_hashes:
                        continue
                    seen_hashes.add(text_hash)
                    chunk_id = str(
                        uuid.uuid5(_INDEX_NAMESPACE, f"{source}:{document_ordinal}:{chunk_ordinal}")
                    )
                    scalar_loader_metadata = {
                        key: value
                        for key, value in split_document.metadata.items()
                        if isinstance(value, (str, int, float, bool))
                    }
                    metadata: dict[str, str | int | float | bool] = {
                        "chunk_id": chunk_id,
                        "source_id": source,
                        "source_type": Path(source).suffix.lstrip(".").lower(),
                        "ordinal": len(chunks),
                        **scalar_loader_metadata,
                    }
                    chunks.append(IndexedChunk(chunk_id, text, metadata))
        return chunks


def main() -> None:
    """命令行入口：``python -m app.services.knowledge_indexer``。"""
    result = KnowledgeIndexer().build()
    print(f"知识库索引已重建：collection={result.collection_name} chunks={result.chunk_count}")


if __name__ == "__main__":
    main()
