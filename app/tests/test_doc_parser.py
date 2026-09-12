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


def _health_output(height_cm: float) -> doc_parser.HealthExtractionOutput:
    """构造模型结构化输出使用的健康指标结果。"""
    return doc_parser.HealthExtractionOutput.model_validate(_health_data(height_cm))


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
            _health_output(175),
            doc_parser.HealthExtractionOutput(code=1002, messages=["识别失败"]),
            _health_output(175),
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


def test_rejects_success_output_without_health_data():
    """成功状态但缺少 data 时应被判定为结构化契约失败。"""
    code, data, messages = doc_parser._parse_model_result(doc_parser.HealthExtractionOutput(code=0))

    assert code == doc_parser.HEALTH_CODE_PARSE_FAILED
    assert data is None
    assert messages == ["模型返回数据不符合健康数据契约"]


def test_pdf_schema_failure_uses_existing_parse_failure_result(monkeypatch):
    """文本 PDF 的结构化输出缺失指标时必须走既有失败路径。"""
    monkeypatch.setattr(
        doc_parser, "get_settings", lambda: SimpleNamespace(health_document_max_pages=20)
    )
    monkeypatch.setattr(
        doc_parser,
        "_extract_pdf_text_and_page_count",
        lambda _path: ("身高检查结果" * 40, 1),
    )
    monkeypatch.setattr(
        doc_parser,
        "_extract_with_llm",
        lambda _text: doc_parser.HealthExtractionOutput(code=0),
    )

    result = doc_parser.parse_pdf("report.pdf")

    assert result == {
        "code": doc_parser.HEALTH_CODE_PARSE_FAILED,
        "messages": ["模型返回数据不符合健康数据契约"],
        "data": None,
    }


def test_image_schema_failure_retries_fallback_then_returns_parse_failure(monkeypatch):
    """主视觉和兜底视觉都失配时应维持统一解析失败结果。"""
    monkeypatch.setattr(
        doc_parser,
        "_extract_with_vl",
        lambda *_args: doc_parser.HealthExtractionOutput(code=0),
    )

    result = doc_parser.parse_image("health.png")

    assert result["code"] == doc_parser.HEALTH_CODE_PARSE_FAILED
    assert result["data"] is None
    assert any("主模型识别失败" in message for message in result["messages"])


def test_text_extractor_uses_structured_output(monkeypatch):
    """文本提取必须请求并消费 HealthExtractionOutput，而非解析模型文本。"""
    captured = {}

    class FakeStructuredModel:
        @staticmethod
        def invoke(messages):
            captured["messages"] = messages
            return _health_output(175)

    class FakeModel:
        @staticmethod
        def with_structured_output(schema):
            captured["schema"] = schema
            return FakeStructuredModel()

    monkeypatch.setattr(doc_parser, "get_chat_model", lambda: FakeModel())

    assert doc_parser._extract_with_llm("身高 175 cm") == _health_output(175)
    assert captured["schema"] is doc_parser.HealthExtractionOutput
    assert captured["messages"][1]["content"].endswith("身高 175 cm")


def test_visual_extractor_uses_structured_output(monkeypatch, tmp_path):
    """主视觉与兜底视觉模型都必须使用同一输出契约。"""
    captured = {}
    image_path = tmp_path / "health.png"
    image_path.write_bytes(b"image data")

    class FakeStructuredModel:
        @staticmethod
        def invoke(messages):
            captured["messages"] = messages
            return {"parsed": _health_output(175), "raw": SimpleNamespace(tool_calls=[])}

    class FakeModel:
        @staticmethod
        def with_structured_output(schema, **kwargs):
            captured["schema"] = schema
            captured["kwargs"] = kwargs
            return FakeStructuredModel()

    def fake_vl_model(tier):
        """记录模型层级并返回支持结构化输出的测试模型。"""
        captured["tier"] = tier
        return FakeModel()

    monkeypatch.setattr(doc_parser, "get_vl_model", fake_vl_model)

    assert doc_parser._extract_with_vl(str(image_path), "fallback") == _health_output(175)
    assert captured["tier"] == "fallback"
    assert captured["schema"] is doc_parser.HealthExtractionOutput
    assert captured["kwargs"] == {"include_raw": True}
    assert captured["messages"][0].content[1]["type"] == "image"


def test_visual_extractor_uses_valid_later_tool_call_when_tongyi_first_call_is_empty(
    monkeypatch,
    tmp_path,
):
    """Tongyi 空首 tool-call 后的有效 schema 参数仍须被 Pydantic 校验后使用。"""
    image_path = tmp_path / "health.png"
    image_path.write_bytes(b"image data")
    raw = SimpleNamespace(
        tool_calls=[
            {"name": "HealthExtractionOutput", "args": {}},
            {"name": "", "args": _health_data(175)},
        ]
    )

    class FakeStructuredModel:
        @staticmethod
        def invoke(_messages):
            return {"parsed": None, "raw": raw}

    class FakeModel:
        @staticmethod
        def with_structured_output(schema, **kwargs):
            assert schema is doc_parser.HealthExtractionOutput
            assert kwargs == {"include_raw": True}
            return FakeStructuredModel()

    monkeypatch.setattr(doc_parser, "get_vl_model", lambda _tier: FakeModel())

    assert doc_parser._extract_with_vl(str(image_path), "primary") == _health_output(175)


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
