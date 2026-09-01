import os
from app.core.settings import get_settings
from app.utils.config_handler import get_models_config, get_prompts_config, get_vector_store_config
from app.utils.path_tool import get_abs_path


def validate_runtime() -> list[str]:
    """检查密钥、配置、必要路径和提示词编码，返回全部问题。"""
    issues = []

    # 检查 1: API KEY
    if not get_settings().dashscope_api_key.strip():
        issues.append("缺少 .env 配置 DASHSCOPE_API_KEY，请配置后再启动应用。")

    # 检查 2：关键文件路径
    required_paths = [
        ("主提示词", get_prompts_config().get("main_prompt_path")),
        ("报告提示词", get_prompts_config().get("report_prompt_path")),
        ("知识库目录", get_vector_store_config().get("data_path")),
    ]
    for label, relative_path in required_paths:
        if not relative_path:
            issues.append(f"{label}未在配置中声明。")
            continue
        abs_path = get_abs_path(relative_path)
        if not os.path.exists(abs_path):
            issues.append(f"{label}不存在：{abs_path}")

    # 检查 3: 模型配置
    for key in (
        "chat_model_name",
        "embedding_model_name",
        "vl_primary_model_name",
        "vl_fallback_model_name",
    ):
        if not get_models_config().get(key):
            issues.append(f"模型配置缺失：{key}")

    # 检查 4: 向量库配置
    for key in ("url", "grpc_port", "prefer_grpc", "collection_alias", "data_path"):
        if not get_vector_store_config().get(key):
            issues.append(f"向量库配置缺失：{key}")

    # 检查 5: 提示词编码
    prompts_cfg = get_prompts_config()
    prompts_key = (
        prompts_cfg.get("main_prompt_path"),
        prompts_cfg.get("report_prompt_path"),
    )
    for relative_path in prompts_key:
        if not relative_path:
            continue
        abs_path = get_abs_path(relative_path)
        if not os.path.exists(abs_path):
            continue
        try:
            with open(abs_path, "r", encoding="utf-8") as f:
                f.read()
        except UnicodeDecodeError:
            issues.append(f"提示词文件不是 UTF-8 编码：{abs_path}")

    return issues
