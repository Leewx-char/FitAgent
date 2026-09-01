"""多页视觉健康文档提取测试，不调用外部模型。"""

from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from app.schemas import HealthDataSchema
from app.services import doc_parser


def _health_data(height_cm: float) -> dict:
    """构造仅含身高指标的标准健康解析成功响应。"""
    return {
        "code": 0,
        "messages": [],
        "data": {"height_cm": {"value": height_cm, "unit": "cm"}},
    }


def test_scanned_pdf_processes_every_page_and_retries_only_failed_page(monkeypatch):
    """验证扫描 PDF 逐页识别，且仅对失败页使用备用 DPI 重试。"""
    settings = SimpleNamespace(
        health_document_max_pages=20,
        health_document_render_dpi=200,
        health_document_fallback_render_dpi=300,
    )
    monkeypatch.setattr(doc_parser, "get_settings", lambda: settings)
    monkeypatch.setattr(doc_parser, "_extract_pdf_text_and_page_count", lambda _: ("", 2))
    render_page = MagicMock(side_effect=lambda _path, page, dpi: f"page-{page}-{dpi}.png")
    monkeypatch.setattr(doc_parser, "_render_pdf_page", render_page)

    with patch.object(
        doc_parser,
        "_extract_with_vl",
        side_effect=[
            _health_data(175),
            {"code": 1002, "messages": ["识别失败"], "data": None},
            _health_data(175),
        ],
    ) as extractor:
        result = doc_parser.parse_pdf("report.pdf")

    assert result["code"] == 0
    assert extractor.call_count == 3
    assert render_page.call_args_list == [
        call("report.pdf", 1, 200),
        call("report.pdf", 2, 200),
        call("report.pdf", 2, 300),
    ]
    assert result["data"]["metrics"]["height_cm"]["value"] == 175


def test_merge_conflicts_requires_user_choice():
    """验证不同页的同一指标冲突时保留候选而不擅自合并。"""
    first = HealthDataSchema.model_validate(_health_data(170)["data"])
    second = HealthDataSchema.model_validate(_health_data(180)["data"])

    merged, conflicts = doc_parser._merge_page_data([(1, first), (2, second)])

    assert merged.height_cm is None
    assert [candidate["page"] for candidate in conflicts["height_cm"]] == [1, 2]


def test_rejects_model_result_without_unified_envelope():
    """验证缺少统一响应信封的模型结果会被判定为解析失败。"""
    code, data, messages = doc_parser._parse_model_result(
        {"height_cm": {"value": 175, "unit": "cm"}}
    )

    assert code == doc_parser.HEALTH_CODE_PARSE_FAILED
    assert data is None
    assert messages == ["模型未返回可用结果"]


def test_result_always_uses_the_unified_envelope():
    """验证解析失败结果仍包含统一的 code、messages 与 data 字段。"""
    result = doc_parser._result(doc_parser.HEALTH_CODE_PARSE_FAILED, ["无法识别"])

    assert set(result) == {"code", "messages", "data"}
    assert result["data"] is None


def test_rejects_pdf_that_exceeds_page_limit(monkeypatch):
    """验证页数超过配置上限的 PDF 被拒绝解析。"""
    monkeypatch.setattr(doc_parser, "_extract_pdf_text_and_page_count", lambda _: ("", 21))
    monkeypatch.setattr(
        doc_parser,
        "get_settings",
        lambda: SimpleNamespace(health_document_max_pages=20),
    )

    result = doc_parser.parse_pdf("report.pdf")

    assert result["code"] == 1004
    assert "20 页上限" in result["messages"][0]


def test_cleanup_removes_temporary_file(tmp_path):
    """处理结束后不应残留上传文件和渲染页面等临时文件。"""

    temporary_file = tmp_path / "health-page.png"
    temporary_file.write_bytes(b"temporary data")

    doc_parser._cleanup_files([temporary_file])

    assert not temporary_file.exists()
