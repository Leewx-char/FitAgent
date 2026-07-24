"""解析健康文档；内部状态码只用于控制识别流程，不暴露给 HTTP 接口。"""

import base64
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

try:
    import magic
except ImportError as exc:  # pragma: no cover - 取决于运行所在操作系统
    raise ImportError(
        "缺少 python-magic 依赖。\n"
        '请重新执行：python -m pip install -e ".[dev]"\n'
        "macOS 用户请执行：brew install libmagic\n"
        "Linux 用户请执行：apt install libmagic1"
    ) from exc

from langchain_core.messages import HumanMessage
from pdf2image import convert_from_path
from pydantic import ValidationError
from pypdf import PdfReader

from app.core.settings import get_settings
from app.schemas import HealthDataSchema, HealthMetric
from app.services.factory import get_chat_model, get_vl_model
from app.utils.logger_handler import logger
from app.utils.prompt_loader import load_health_extract_prompts

UPLOAD_DIR = Path("storage/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

HEALTH_CODE_OK = 0
HEALTH_CODE_UNRELATED = 1001
HEALTH_CODE_PARSE_FAILED = 1002
HEALTH_CODE_ENCRYPTED = 1003
HEALTH_CODE_INVALID_INPUT = 1004

MAX_FILE_SIZE = 10 * 1024 * 1024
MAX_PDF_TEXT_CHARACTERS = 20_000
ALLOWED_MIMES = {"image/jpeg", "image/png", "image/webp", "application/pdf"}
PDF_TEXT_THRESHOLD = 200
METRIC_FIELDS = tuple(HealthDataSchema.model_fields)


def _result(
    code: int,
    messages: list[str] | None = None,
    metrics: HealthDataSchema | None = None,
    conflicts: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """构造唯一的健康文档结果格式。"""

    unique_messages = list(dict.fromkeys(message for message in messages or [] if message))
    data = None
    if metrics is not None:
        data = {
            "metrics": metrics.model_dump(exclude_none=True),
            "conflicts": conflicts or {},
        }
    return {"code": code, "messages": unique_messages, "data": data}


def _validate_upload(file_bytes: bytes, filename: str) -> tuple[str, str]:
    """在使用随机文件名暂存前校验文件类型和大小。"""

    mime = magic.from_buffer(file_bytes, mime=True)
    if mime not in ALLOWED_MIMES:
        raise ValueError(f"不支持的文件类型：{mime}，仅支持图片（JPG/PNG/WebP）和 PDF")
    if len(file_bytes) > MAX_FILE_SIZE:
        size_mb = len(file_bytes) / 1024 / 1024
        raise ValueError(f"文件大小超过限制（最大 10MB，当前 {size_mb:.1f}MB）")

    extension = Path(filename).suffix or (".jpg" if mime.startswith("image") else ".pdf")
    return mime, f"{uuid.uuid4().hex}{extension}"


def _save_temp(file_bytes: bytes, safe_name: str) -> Path:
    """以生成后的安全文件名将上传文件写入临时目录。"""

    path = UPLOAD_DIR / safe_name
    path.write_bytes(file_bytes)
    return path


def _parse_llm_json(content: str) -> dict[str, Any]:
    """从模型响应中提取一个 JSON 对象，绝不执行任意内容。"""

    code_block = re.search(r"```(?:json)?\s*([\s\S]*?)```", content)
    if code_block:
        content = code_block.group(1).strip()
    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    object_match = re.search(r"\{[\s\S]*\}", content)
    if object_match:
        try:
            parsed = json.loads(object_match.group())
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return _result(HEALTH_CODE_PARSE_FAILED, ["AI 返回结果无法解析为 JSON"])


def _response_content(response: Any) -> str:
    """将 LangChain 响应内容统一转换为纯文本。"""

    content = response.content
    if isinstance(content, list):
        return "".join(item if isinstance(item, str) else item.get("text", "") for item in content)
    return str(content)


def _extract_with_llm(text: str) -> dict[str, Any]:
    """使用常规聊天模型从可选中的 PDF 文字中提取结构化字段。"""

    messages = [
        {"role": "system", "content": load_health_extract_prompts()},
        {"role": "user", "content": f"请从以下文档内容中提取健康数据：\n\n{text}"},
    ]
    return _parse_llm_json(_response_content(get_chat_model().invoke(messages)))


def _extract_with_vl(image_path: str, tier: str) -> dict[str, Any]:
    """使用指定层级的视觉模型提取单张页面。"""

    with open(image_path, "rb") as image_file:
        encoded_image = base64.b64encode(image_file.read()).decode()

    image_mime_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }
    mime_type = image_mime_types.get(Path(image_path).suffix.lower(), "image/jpeg")
    messages = [
        HumanMessage(
            content=[
                {
                    "type": "text",
                    "text": (
                        f"{load_health_extract_prompts()}\n\n"
                        "请从这张健康文档图片中提取数据。仅做指标提取，"
                        "不要给出诊断或治疗建议。"
                    ),
                },
                {"type": "image", "image": f"data:{mime_type};base64,{encoded_image}"},
            ]
        )
    ]
    return _parse_llm_json(_response_content(get_vl_model(tier).invoke(messages)))


def _normalise_messages(value: Any) -> list[str]:
    """将模型消息字段规范为字符串列表。"""

    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(message) for message in value if isinstance(message, (str, int, float))]
    return []


def _has_measurement(data: HealthDataSchema) -> bool:
    """判断经校验的提取结果是否包含至少一个可用指标。"""

    return any(
        (metric := getattr(data, field)) is not None and metric.value is not None
        for field in METRIC_FIELDS
    )


def _parse_model_result(result: dict[str, Any]) -> tuple[int, HealthDataSchema | None, list[str]]:
    """校验统一模型结果并返回状态码、指标和消息。"""

    try:
        code = int(result.get("code", HEALTH_CODE_PARSE_FAILED))
    except (TypeError, ValueError):
        code = HEALTH_CODE_PARSE_FAILED
    messages = _normalise_messages(result.get("messages"))
    if code != HEALTH_CODE_OK:
        return code, None, messages or ["模型未返回可用结果"]

    try:
        data = HealthDataSchema.model_validate(result.get("data"))
    except ValidationError:
        return HEALTH_CODE_PARSE_FAILED, None, ["模型返回数据不符合健康数据契约"]
    if not _has_measurement(data):
        return HEALTH_CODE_PARSE_FAILED, None, ["未识别到可确认的健康指标"]
    return HEALTH_CODE_OK, data, messages


def _extract_visual_page(
    image_path: str,
    tier: str,
) -> tuple[int, HealthDataSchema | None, list[str]]:
    """调用指定层级视觉模型并校验返回的统一结果。"""

    try:
        result = _extract_with_vl(image_path, tier)
    except Exception:
        logger.exception("健康文档%s视觉模型调用失败", "主" if tier == "primary" else "兜底")
        return HEALTH_CODE_PARSE_FAILED, None, ["视觉模型调用失败"]
    return _parse_model_result(result)


def _extract_pdf_text_and_page_count(pdf_path: str) -> tuple[str, int]:
    """不渲染 PDF，直接提取可选文本和页数。"""

    reader = PdfReader(pdf_path)
    if reader.is_encrypted:
        raise ValueError("PDF 文件已加密，请截图后以图片形式上传")

    text_parts = []
    for page in reader.pages:
        page_text = page.extract_text()
        if page_text:
            text_parts.append(page_text.strip())
    return "\n".join(text_parts), len(reader.pages)


def _render_pdf_page(pdf_path: str, page: int, dpi: int) -> str:
    """按指定分辨率将单页 PDF 渲染为临时 PNG。"""

    images = convert_from_path(pdf_path, dpi=dpi, first_page=page, last_page=page)
    if not images:
        raise ValueError(f"PDF 第 {page} 页无法渲染")
    image_path = UPLOAD_DIR / f"{uuid.uuid4().hex}.png"
    images[0].save(image_path, "PNG")
    return str(image_path)


def _cleanup_files(paths: list[str | Path]) -> None:
    """尽力删除上传文件和渲染页面等临时文件。"""

    for path in paths:
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except OSError:
            logger.warning("未能清理健康文档临时文件：%s", path)


def _merge_page_data(
    extracted_pages: list[tuple[int, HealthDataSchema]],
) -> tuple[HealthDataSchema, dict[str, list[dict[str, Any]]]]:
    """合并无冲突的分页指标，并把冲突候选交给用户选择。"""

    merged: dict[str, Any] = {}
    selected: dict[str, tuple[int, HealthMetric]] = {}
    conflicts: dict[str, list[dict[str, Any]]] = {}

    for page, data in extracted_pages:
        for field in METRIC_FIELDS:
            metric = getattr(data, field)
            if metric is None or metric.value is None:
                continue
            if field not in selected:
                selected[field] = (page, metric)
                merged[field] = metric.model_dump(exclude_none=True)
                continue

            existing_page, existing_metric = selected[field]
            existing_payload = existing_metric.model_dump(exclude_none=True)
            metric_payload = metric.model_dump(exclude_none=True)
            if existing_payload == metric_payload:
                continue
            if field not in conflicts:
                conflicts[field] = [{"page": existing_page, "metric": existing_payload}]
                merged.pop(field, None)
            conflicts[field].append({"page": page, "metric": metric_payload})

    return HealthDataSchema.model_validate(merged), conflicts


def _build_result(
    page_codes: list[int],
    extracted_pages: list[tuple[int, HealthDataSchema]],
    messages: list[str],
) -> dict[str, Any]:
    """汇总分页提取结果并构造统一响应。"""

    if extracted_pages:
        data, conflicts = _merge_page_data(extracted_pages)
        return _result(HEALTH_CODE_OK, messages, data, conflicts)
    if page_codes and all(code == HEALTH_CODE_UNRELATED for code in page_codes):
        return _result(HEALTH_CODE_UNRELATED, messages or ["文档与健康指标无关"])
    return _result(HEALTH_CODE_PARSE_FAILED, messages or ["未能从文档中提取健康指标"])


def _prefix_page_messages(page: int, messages: list[str]) -> list[str]:
    """为分页处理消息补充页码，便于用户知道需要复核的位置。"""

    return [f"第 {page} 页：{message}" for message in messages]


def parse_image(image_path: str) -> dict[str, Any]:
    """通过主模型识别图片；失败时使用兜底模型重试。"""

    code, data, messages = _extract_visual_page(image_path, "primary")
    if code == HEALTH_CODE_OK:
        return _result(code, messages, data)
    if code == HEALTH_CODE_UNRELATED:
        return _result(code, messages)

    retry_messages = messages + ["主模型识别失败，已使用兜底模型重试"]
    code, data, fallback_messages = _extract_visual_page(image_path, "fallback")
    return _result(code, retry_messages + fallback_messages, data)


def parse_pdf(pdf_path: str) -> dict[str, Any]:
    """解析 PDF；可选文本走聊天模型，扫描件按页走视觉模型。"""

    try:
        text, page_count = _extract_pdf_text_and_page_count(pdf_path)
    except ValueError as exc:
        return _result(HEALTH_CODE_ENCRYPTED, [str(exc)])
    except Exception:
        logger.exception("PDF 文字提取失败")
        return _result(HEALTH_CODE_PARSE_FAILED, ["PDF 文件无法解析"])

    settings = get_settings()
    if page_count > settings.health_document_max_pages:
        return _result(
            HEALTH_CODE_INVALID_INPUT,
            [f"PDF 共 {page_count} 页，超过当前允许的 {settings.health_document_max_pages} 页上限"],
        )

    if len(text.strip()) >= PDF_TEXT_THRESHOLD:
        if len(text) > MAX_PDF_TEXT_CHARACTERS:
            return _result(
                HEALTH_CODE_INVALID_INPUT,
                [f"PDF 文字超过 {MAX_PDF_TEXT_CHARACTERS} 字符，请拆分文件后重新上传"],
            )
        try:
            result = _extract_with_llm(text)
        except Exception:
            logger.exception("PDF 文本模型调用失败")
            return _result(HEALTH_CODE_PARSE_FAILED, ["文本模型调用失败"])
        code, data, messages = _parse_model_result(result)
        return _result(code, messages, data)

    page_codes: list[int] = []
    extracted_pages: list[tuple[int, HealthDataSchema]] = []
    messages: list[str] = []
    for page in range(1, page_count + 1):
        primary_path: str | None = None
        fallback_path: str | None = None
        try:
            primary_path = _render_pdf_page(pdf_path, page, settings.health_document_render_dpi)
            code, data, page_messages = _extract_visual_page(primary_path, "primary")
            messages.extend(_prefix_page_messages(page, page_messages))

            if code not in {HEALTH_CODE_OK, HEALTH_CODE_UNRELATED}:
                messages.append(f"第 {page} 页主模型识别失败，已使用高精度模型重试")
                fallback_path = _render_pdf_page(
                    pdf_path,
                    page,
                    settings.health_document_fallback_render_dpi,
                )
                code, data, fallback_messages = _extract_visual_page(fallback_path, "fallback")
                messages.extend(_prefix_page_messages(page, fallback_messages))

            page_codes.append(code)
            if data is not None:
                extracted_pages.append((page, data))
        except Exception:
            logger.exception("PDF 第 %s 页视觉解析失败", page)
            page_codes.append(HEALTH_CODE_PARSE_FAILED)
            messages.append(f"第 {page} 页渲染或识别失败")
        finally:
            _cleanup_files([path for path in (primary_path, fallback_path) if path])

    return _build_result(page_codes, extracted_pages, messages)


def parse_health_doc(file_path: str, mime_type: str) -> dict[str, Any]:
    """将已校验的临时文件分派给对应解析器，并始终删除该文件。"""

    try:
        if mime_type.startswith("image"):
            return parse_image(file_path)
        if mime_type == "application/pdf":
            return parse_pdf(file_path)
        return _result(HEALTH_CODE_INVALID_INPUT, [f"不支持的文件类型：{mime_type}"])
    finally:
        _cleanup_files([file_path])


def handle_upload(file_bytes: bytes, filename: str) -> dict[str, Any]:
    """校验、解析用户上传的健康文档，并始终清理临时文件。"""

    try:
        mime_type, safe_name = _validate_upload(file_bytes, filename)
    except ValueError as exc:
        return _result(HEALTH_CODE_INVALID_INPUT, [str(exc)])

    temp_path = _save_temp(file_bytes, safe_name)
    try:
        return parse_health_doc(str(temp_path), mime_type)
    except Exception:
        logger.exception("健康文档解析过程失败")
        return _result(HEALTH_CODE_PARSE_FAILED, ["解析过程出错"])
