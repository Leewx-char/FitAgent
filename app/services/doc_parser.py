"""
用户上传文件
  ↓
handle_upload(file_bytes, filename)
  ├── _validate_upload  →  验证MIME、大小、uuid重命名
  ├── _save_temp        →  保存到 storage/uploads/
  └── parse_health_doc  →  路由分发
        ├── 图片 → parse_image → _extract_with_vl (千问VL)
        └── PDF  → parse_pdf
              ├── 加密 → 返回 {"status": "encrypted", ...}
              ├── 文字≥200字 → _extract_with_llm (qwen3-max)
              └── 文字<200字 → _pdf_to_images → _extract_with_vl (千问VL)
"""
import json
import os
import re
import uuid
import base64
from pathlib import Path
try:
    import magic
except ImportError:
    raise ImportError(
        "缺少 python-magic 依赖。\n"
        "Windows 用户请执行：pip install python-magic-bin\n"
        "macOS 用户请执行：brew install libmagic\n"
        "Linux 用户请执行：apt install libmagic1"
    )
from pypdf import PdfReader
from pdf2image import convert_from_path
from langchain_core.messages import HumanMessage
from app.services.factory import get_chat_model, get_vl_model
from app.utils.prompt_loader import load_health_extract_prompts
from app.utils.logger_handler import logger

UPLOAD_DIR = Path("storage/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

MAX_FILE_SIZE = 10 * 1024 * 1024 # 10MB
ALLOWED_MIMES = {"image/jpeg", "image/png", "image/webp", "application/pdf"}
PDF_TEXT_THRESHOLD = 200

def _validate_upload(file_bytes: bytes, filename: str) -> tuple[str, str]:
    # mime 文件的类型标识
    mime = magic.from_buffer(file_bytes, mime=True) # 读文件头前几个字节判断真实类型
    if mime not in ALLOWED_MIMES: # 不在白名单就直接拒绝
        raise ValueError(f"不支持的文件类型：{mime}，仅支持图片（JPG/PNG/Webp）和PDF")
    if len(file_bytes) > MAX_FILE_SIZE: # 大小超10MB拒绝
        raise ValueError(f"文件大小超过限制（最大10MB），当前{len(file_bytes) / 1024 / 1024:.1f}MB")
    # 取原始扩展名
    ext = Path(filename).suffix or (".jpg" if mime.startswith("image") else ".pdf")
    # 32位随机十六进制字符串做文件名
    safe_name = f"{uuid.uuid4().hex}{ext}"
    return mime, safe_name

def _save_temp(file_bytes: bytes, safe_name: str) -> Path:
    """
    解释：
    - 把验证通过的文件内容写到 storage/uploads/ 目录
    - 返回 Path 对象，后续解析需要读这个文件
    - 解析完会在 finally 里删掉这个临时文件（安全措施，防止磁盘堆积）
    """
    path = UPLOAD_DIR / safe_name
    path.write_bytes(file_bytes)
    return path

# 从LLM返回中提取JSON
def _parse_llm_json(content: str) -> dict:
    # 第1层：提取 ```json...``` 代码块
    json_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", content)
    if json_match:
        content = json_match.group(1).strip()
    # 第2层：直接尝试解析
    try:
        result = json.loads(content)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass
    # 第3层：宽松匹配，找第一个 {到最后一个}
    brace_match = re.search(r"\{[\s\S]*\}", content)
    if brace_match:
        try:
            return json.loads(brace_match.group())
        except json.JSONDecodeError:
            pass
    return {"status": "parse_failed", "message": "AI返回结果无法解析为JSON"}

# 调用大模型（文字型用普通LLM）
def _extract_with_llm(text: str) -> dict:
    prompt = load_health_extract_prompts()
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": f"请从以下文档内容中提取健康数据：\n\n{text}"},
    ]
    response = get_chat_model().invoke(messages)
    content = response.content
    if isinstance(content, list):
        content = "".join(
            item if isinstance(item, str) else item.get("text", "")
            for item in content
        )
    return _parse_llm_json(content)

def _extract_with_vl(image_path: str) -> dict:
    with open(image_path, "rb") as f: # 以二进制模式读图片文件
        b64 = base64.b64encode(f.read()).decode() # 图片二进制 → base64 编码字符串。VL只接受 base64 编码的图片数据

    ext = Path(image_path).suffix.lower()
    # 根据扩展名确定图片 MIME 类型
    mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
    mime_type = mime_map.get(ext, "image/jpeg")

    prompt = load_health_extract_prompts()
    # 千问 VL 使用 DashScope 格式，type 用 "image"，字段名用 "image"
    messages = [
        HumanMessage(content=[
            {"type": "text", "text": f"{prompt}\n\n请从以下健康文档图片中提取健康数据："},
            {"type": "image", "image": f"data:{mime_type};base64,{b64}"},
        ])
    ]
    response = get_vl_model().invoke(messages)
    content = response.content
    if isinstance(content, list):
        content = "".join(
            item if isinstance(item, str) else item.get("text", "")
            for item in content
        )
    return _parse_llm_json(content)

def _extract_text_from_pdf(pdf_path: str) -> str:
    try:
        # 打开 PDF 文件
        reader = PdfReader(pdf_path)
        text_parts = []
        # 遍历每一页
        for page in reader.pages:
            t = page.extract_text()
            if t:
                text_parts.append(t.strip())
        # 最终拼接所有页的文字，用换行符分隔
        return "\n".join(text_parts)
    except Exception as e:
        error_msg = str(e)
        if any(kw in error_msg.lower() for kw in ("encrypted", "password", "not been decrypted")):
            raise ValueError("PDF文件已加密，请截图后以图片形式上传")
        raise

# PDF转图片
def _pdf_to_images(pdf_path: str) -> list[str]:
    # pdf2image 的核心函数，底层调用系统安装的 poppler 把 PDF 每页渲染成 PIL Image 对象。dpi=200 是清晰度和文件大小的平衡
    images = convert_from_path(pdf_path, dpi=200)
    paths = []
    for img in images[:3]: # 只取前3页,体检报告通常前几页就有核心数据,每页都要走 VL 调用，页数太多会很慢很贵
        img_path = str(UPLOAD_DIR / f"{uuid.uuid4().hex}.png")
        # PIL Image 保存为 PNG 到临时目录
        img.save(img_path, "PNG")
        paths.append(img_path)
    return paths

# 清理临时文件
def _cleanup_files(paths: list[str]):
    for p in paths:
        try:
            if p and os.path.exists(p):
                os.remove(p)
        except Exception:
            pass

# 解析图片入口
def parse_image(image_path: str) -> dict:
    try:
        return _extract_with_vl(image_path)
    except Exception as e:
        logger.error(f"图片解析失败：{str(e)}", exc_info=True)
        return {"status": "parse_failed", "message": f"图片解析失败：{str(e)}"}

# 解析PDF入口
def parse_pdf(pdf_path: str) -> dict:
    # finally 里需要清理 image_paths，如果放在 try 里面，
    # 赋值之前的异常会导致 image_paths 未定义，finally 里的
    # _cleanup_files 就会报错。
    image_paths = []
    try:
        text = _extract_text_from_pdf(pdf_path)
    except ValueError as e:
        # 加密PDF
        return {"status": "encrypted", "message": str(e)}
    except Exception as e:
        logger.error(f"PDF文字提取异常：{str(e)}", exc_info=True)
        return {"status": "parse_failed", "message": "PDF文件无法解析"}

    if len(text.strip()) >= PDF_TEXT_THRESHOLD:
        # 文字充足，走普通LLM
        return _extract_with_llm(text)

    # 文字不足，视为扫描件，走VL
    try:
        image_paths = _pdf_to_images(pdf_path)
        return _extract_with_vl(image_paths[0])
    except Exception as e:
        logger.error(f"PDF转图片失败：{str(e)}", exc_info=True)
        return {"status": "parse_failed", "message": "PDF扫描件解析失败"}
    finally:
        # 不论 _extract_with_vl 成功还是失败，PDF 转出的临时图片都要删掉。
        _cleanup_files(image_paths)

# 统一入口
def parse_health_doc(file_path: str, mime_type: str) -> dict:
    try:
        if mime_type.startswith("image"):
            return parse_image(file_path)
        elif mime_type == "application/pdf":
            return parse_pdf(file_path)
        else:
            return {"status": "parse_failed", "message": f"不支持的文件类型：{mime_type}"}
    finally:
        # 无论解析成功还是失败，都删除上传的原始临时文件
        _cleanup_files([file_path])

def handle_upload(file_bytes: bytes, filename: str) -> dict:
    try:
        # 验证 MIME + 大小
        mime_type, safe_name = _validate_upload(file_bytes, filename)
    except ValueError as e:
        return {"status": "error", "message": str(e)}

    # 保存到临时文件
    temp_path = _save_temp(file_bytes, safe_name)
    try:
        # 解析提取
        result = parse_health_doc(str(temp_path), mime_type)
    except Exception as e:
        logger.error(f"文档解析失败：{str(e)}", exc_info=True)
        result = {"status": "parse_failed", "message": "解析过程出错"}
    return result


