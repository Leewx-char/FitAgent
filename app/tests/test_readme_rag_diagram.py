"""README 中 RAG 流程图资源的回归校验。"""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_readme_embeds_existing_rag_flow_diagram() -> None:
    """确保 README 引用的 RAG 流程图文件存在且为 SVG。"""
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    diagram = PROJECT_ROOT / "docs/assets/rag-retrieval-flow.svg"

    assert 'src="docs/assets/rag-retrieval-flow.svg"' in readme
    assert diagram.is_file()
    assert "<svg " in diagram.read_text(encoding="utf-8")
