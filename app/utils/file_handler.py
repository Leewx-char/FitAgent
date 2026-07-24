import re
import os
import hashlib
from app.utils.logger_handler import logger
from langchain_core.documents import Document
from langchain_community.document_loaders import PyPDFLoader, TextLoader


def get_file_md5_hex(filepath: str) -> str | None:
    """返回现有调用方仍在使用的兼容性 MD5 校验和。"""
    if not os.path.exists(filepath):
        logger.error(f"[md5计算]文件{filepath}不存在")
        return None
    if not os.path.isfile(filepath):
        logger.error(f"[md5计算]路径{filepath}不是文件")
        return None
    digest = hashlib.md5()
    try:
        with open(filepath, "rb") as file:
            while chunk := file.read(4096):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError as error:
        logger.error(f"计算文件{filepath}md5失败，{error}")
        return None


def get_file_sha256_hex(filepath: str) -> str | None:
    """返回适用于不可变索引版本清单的源文件校验和。"""
    if not os.path.isfile(filepath):
        logger.error(f"[sha256计算]路径{filepath}不是文件")
        return None
    digest = hashlib.sha256()
    try:
        with open(filepath, "rb") as file:
            while chunk := file.read(4096):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError as error:
        logger.error(f"计算文件{filepath}sha256失败，{error}")
        return None


def listdir_with_allowed_type(
    path: str, allowed_types: tuple[str]
):  # 返回文件夹内的列表（允许的文件后缀）
    files = []

    if not os.path.isdir(path):
        logger.error(f"[listdir_with_allowed_type]{path}不是文件夹")
        return ()

    """递归遍历目录树，root是当前遍历到的目录路径，
    dir是当前目录下的子目录列表，filenames是当前目录的文件列表"""
    for root, dir, filenames in os.walk(path):
        for filename in filenames:
            if filename.endswith(allowed_types):
                files.append(os.path.join(root, filename))

    # os.walk遍历顺序不固定
    return tuple(sorted(files))


def pdf_loader(filepath: str, passwd=None) -> list[Document]:
    return PyPDFLoader(filepath, passwd).load()


def txt_loader(filepath: str) -> list[Document]:
    """优先按 UTF-8 加载文本；兼容历史资料的其他编码。"""
    return TextLoader(filepath, encoding="utf-8", autodetect_encoding=True).load()


def clean_text(text: str) -> str:
    """轻量文本清洗，统一空白、换行和BOM"""
    """空值检查"""
    if not text:
        return ""

    # 去除特殊字符
    cleaned = text.replace("\ufeff", "").replace("\u3000", " ")
    # 统一换行符
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    # 压缩空白字符
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    # 清理行首尾空格
    cleaned = re.sub(r" *\n *", "\n", cleaned)
    # 限制连续空行
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

    # 去除首尾空白
    return cleaned.strip()


def normalize_documents(documents: list[Document]) -> list[Document]:
    """对一组文档做统一清洗，并过滤空内容。"""
    normalized = []
    for doc in documents:
        cleaned = clean_text(doc.page_content)
        if not cleaned:
            continue
        doc.page_content = cleaned
        normalized.append(doc)

    """虽然原文档的内容也换成清洗过后的，
    但是空白页没有处理，所以不返回原文档,返回清洗过后的文档"""
    return normalized


def split_qa_documents(documents: list[Document]) -> list[Document]:
    """
    把 FAQ/问答类长文本拆成独立的“问题-答案”文档。

    提高知识库命中率，避免一整个 FAQ文件被当成长文切碎后难以命中
    """
    qa_documents = []
    """命名捕获组，在编译时定义组名，匹配时自动分组，一个可重复使用的正则对象（提高性能）"""
    pattern = re.compile(
        r"(?ms)(?:^|\n)(?:\d+\.\s*)?(?:\*\*)?(?P<question>[^\n？?]{3,}[？?])(?:\*\*)?\s*\n-\s*(?P<answer>.*?)(?=(?:\n(?:\d+\.\s*)?(?:\*\*)?[^\n？?]{3,}[？?](?:\*\*)?\s*\n-\s)|\Z)"
    )

    """取出每一个文档的内容再用正则表达式查找符合条件的，如果条数少（可能不是FAQ文档）就直接加入分割后的问答列表，如果多再拆分"""
    for doc in documents:
        # 查找匹配项并返回迭代器
        matches = list(pattern.finditer(doc.page_content))

        if len(matches) < 3:
            qa_documents.append(doc)
            continue

        for index, match in enumerate(matches):
            question = clean_text(match.group("question"))
            answer = clean_text(match.group("answer"))
            if not question or not answer:
                continue
            qa_documents.append(
                Document(
                    page_content=f"问题：{question}\n答案：{answer}",
                    metadata={**doc.metadata, "qa_index": index},
                )
            )

    return qa_documents
