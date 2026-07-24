"""在不访问 embedding 服务和 Qdrant 的情况下验证知识库发布输入。"""

from app.services.knowledge_indexer import KnowledgeIndexer


def main() -> None:
    """执行预检、写入忽略的报告，并输出适合终端查看的摘要。"""
    indexer = KnowledgeIndexer(initialize_repository=False)
    result = indexer.preflight()
    indexer.write_preflight_report(result)
    print(
        "知识库预检通过："
        f"revision={result.revision[:12]} "
        f"sources={len(result.source_checksums)} chunks={len(result.chunks)} "
        f"warnings={len(result.warnings)}"
    )
    for warning in result.warnings:
        print(f"警告：{warning}")


if __name__ == "__main__":
    main()
