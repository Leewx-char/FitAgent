import json
from app.utils.file_handler import normalize_documents, clean_text, split_qa_documents
from datetime import datetime
import os.path
import hashlib
from app.utils.file_handler import listdir_with_allowed_type, get_file_md5_hex
from langchain_chroma import Chroma
from app.utils.config_handler import chroma_conf
from app.utils.file_handler import txt_loader, pdf_loader
from app.utils.logger_handler import logger
from app.utils.path_tool import get_abs_path
from app.services.factory import get_embedding_model
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document

class VectorStoreService:
    def __init__(self):
        self.persist_directory = get_abs_path(chroma_conf['persist_directory'])
        self.manifest_store = get_abs_path(
            chroma_conf.get('manifest_store',
            os.path.join(self.persist_directory, 'knowledge_manifest.json')),
        )

        manifest_dir = os.path.dirname(self.manifest_store)
        if manifest_dir:
            os.makedirs(manifest_dir, exist_ok=True)

        self.vector_store = self.create_chroma()

        self.default_splitter = self._build_splitter(
            chunk_size=chroma_conf['chunk_size'],
            chunk_overlap=chroma_conf['chunk_overlap'],
        )

        self.txt_splitter = self._build_splitter(
            chunk_size=chroma_conf.get('txt_chunk_size', chroma_conf['chunk_size']),
            chunk_overlap=chroma_conf.get('txt_chunk_overlap', chroma_conf['chunk_overlap']),
        )

        self.pdf_splitter = self._build_splitter(
            chunk_size=chroma_conf.get('pdf_chunk_size', chroma_conf['chunk_size']),
            chunk_overlap=chroma_conf.get('pdf_chunk_overlap', chroma_conf['chunk_overlap']),
        )

    def create_chroma(self):
        return Chroma(
            collection_name=chroma_conf["collection_name"],
            embedding_function=get_embedding_model(),
            persist_directory=self.persist_directory
        )

    def _build_splitter(self, chunk_size: int, chunk_overlap: int) -> RecursiveCharacterTextSplitter:
        return RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=chroma_conf['separators'],
            length_function=len,
        )

    def _get_splitter(self, read_path: str) -> RecursiveCharacterTextSplitter:
        if read_path.endswith(".txt"):
            return self.txt_splitter
        if read_path.endswith(".pdf"):
            return self.pdf_splitter
        return self.default_splitter

    @staticmethod
    def _build_chunk_id(source: str, chunk_index: int, content: str) -> str:
        digest = hashlib.md5(content.encode("utf-8")).hexdigest()[:12]
        return f"{source}:{chunk_index}:{digest}"


    def get_retriever(self):
        return self.vector_store.as_retriever(search_kwargs={"k": chroma_conf["k"]})

    def _load_manifest(self) -> dict:
        if not os.path.exists(self.manifest_store):
            return {}
        try:
            with open(self.manifest_store, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.warning(f"读取知识库 manifest 失败，将按空 manifest 处理：{str(e)}")
            return {}

    def _save_manifest(self, manifest: dict):
        with open(self.manifest_store, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2, sort_keys=True)

    @staticmethod
    def _manifest_item(md5_hex: str, chunk_count: int) -> dict:
        return {
            "md5": md5_hex,
            "chunk_count": chunk_count,
            "updated_at": datetime.now().isoformat(timespec="seconds")
        }

    def _delete_documents_by_source(self, source: str):
        try:
            self.vector_store.delete(where={"source": source})
        except Exception as e:
            logger.warning(f"按来源删除旧切片失败，source={source}，error={str(e)}")

    def _cleanup_stale_documents(self, allowed_files_path):
        existing_sources = {
            os.path.relpath(path, get_abs_path(chroma_conf["data_path"]))
            for path in allowed_files_path
        }

        manifest = self._load_manifest()

        # 从向量库元数据中找不在 data/ 目录的来源
        try:
            stored = self.vector_store.get(include=["metadatas"])
        except Exception as e:
            logger.warning(f"读取向量库元数据失败，跳过陈旧切片处理：{str(e)}")
            stored = {"metadatas": []}

        stale_sources = set()
        for metadata in stored.get("metadatas", []):
            if not metadata:
                continue
            source = metadata.get("source")
            if source and source not in existing_sources:
                stale_sources.add(source)

        # 也在 manifest里找
        for source in list(manifest.keys()):
            if source not in existing_sources:
                stale_sources.add(source)

        # 逐个清理
        for source in stale_sources:
            self._delete_documents_by_source(source)
            manifest.pop(source, None)
            logger.info(f"已清理已删除知识文件遗留的切片：{source}")

        self._save_manifest(manifest)

    def reset_store(self):
        try:
            self.vector_store.delete_collection()
        except Exception as e:
            logger.warning(f"删除旧向量集合失败：{str(e)}")

        if os.path.exists(self.manifest_store):
            os.remove(self.manifest_store)

        self.vector_store = self.create_chroma()
        logger.info("向量库重建完成")


    def load_document(self,):
        """
        从数据文件夹内读取数据文件，转为向量存入向量库
        要计算文件的md5做去重
        流程：扫描文件 → MD5去重 → 加载内容 → 文本切分 → 存入向量库 → 记录MD5
        特性：支持增量更新，只处理新增或修改的文件
        """

        def get_file_documents(read_path: str):
            """根据文件路径加载为Document对象列表"""
            if read_path.endswith("txt"):
                return txt_loader(read_path)
            if read_path.endswith("pdf"):
                return pdf_loader(read_path)

            return []

        allowed_files_path: list[str] = listdir_with_allowed_type(
            get_abs_path(chroma_conf["data_path"]),
            tuple(chroma_conf["allow_knowledge_file_type"]),
        )
        self._cleanup_stale_documents(allowed_files_path)
        manifest = self._load_manifest()

        for path in allowed_files_path:
            # 获取文件的MD5
            md5_hex = get_file_md5_hex(path)

            source = os.path.relpath(path, get_abs_path(chroma_conf["data_path"]))
            if manifest.get(source, {}).get("md5") == md5_hex:
                logger.info(f"[加载知识库]{path}内容已经存在知识库内，跳过")
                continue

            try:
                documents: list[Document] = get_file_documents(path)

                if not documents:
                    logger.warning(f"[加载知识库]{path}内没有有效文本内容，跳过")
                    continue

                documents = normalize_documents(documents)

                if source.endswith("100问.txt") or "常见问题" in clean_text(documents[0].page_content[:80]):
                    documents = split_qa_documents(documents)

                split_document: list[Document] = self._get_splitter(path).split_documents(documents)

                if not split_document:
                    logger.warning(f"[加载知识库]{path}分片后没有有效文本内容，跳过")
                    continue

                self._delete_documents_by_source(source)

                # 生成稳定ID
                ids = []
                for idx, doc in enumerate(split_document):
                    doc.metadata["source"] = source
                    doc.metadata["source_type"] = os.path.splitext(path)[1].lstrip(".").lower()
                    doc.metadata["chunk_index"] = idx
                    ids.append(self._build_chunk_id(source, idx, doc.page_content))

                # 分批写入，带上ids
                batch_size = 10
                for i in range(0, len(split_document), batch_size):
                    self.vector_store.add_documents(
                        split_document[i:i + batch_size],
                        ids=ids[i:i + batch_size],
                    )

                # 记录这个已经处理好的文件的md5值，避免下次重复加载
                manifest[source] = self._manifest_item(md5_hex, len(split_document))
                self._save_manifest(manifest)

                logger.info(f"[加载知识库]{path} 内容加载成功")
            except Exception as e:
                # exc_info为True会记录详细的错误堆栈，如果为False仅记录报错信息本身
                logger.error(f"[加载知识库]{path}加载失败：{str(e)}", exc_info=True)
                continue


if __name__ == '__main__':
    vs = VectorStoreService()

    vs.load_document()

    retriever = vs.get_retriever()

    res = retriever.invoke("迷路")
    for r in res:
        print(r.page_content)
        print("-"*20)