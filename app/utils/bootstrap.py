import os
from app.utils.config_handler import prompts_conf, chroma_conf, rag_conf
from app.utils.path_tool import get_abs_path

def validate_runtime() -> list[str]:
    issues = []

    # 检查 1: API KEY
    if not os.getenv("DASHSCOPE_API_KEY"):
        issues.append("缺少环境变量 DASHSCOPE_API_KEY，请现在运行环境中配置后再启动应用。")

    # 检查 2：关键文件路径
    required_paths = [
        ("主提示词", prompts_conf.get("main_prompt_path")),
        ("报告提示词", prompts_conf.get("report_prompt_path")),
        ("知识库目录", chroma_conf.get("data_path")),
    ]
    for label, relative_path in required_paths:
        if not relative_path:
            issues.append(f"{label}未在配置中声明。")
            continue
        abs_path = get_abs_path(relative_path)
        if not os.path.exists(abs_path):
            issues.append(f"{label}不存在：{abs_path}")

    # 检查 3: 模型配置
    for key in ("chat_model_name", "embedding_model_name"):
        if not rag_conf.get(key):
            issues.append(f"模型配置缺失：{key}")

    # 检查 4: 向量库配置
    for key in ("collection_name", "persist_directory", "data_path"):
        if not chroma_conf.get(key):
            issues.append(f"向量库配置缺失：{key}")

    # 检查 5: 提示词编码
    prompts_key = (
        prompts_conf.get("main_prompt_path"),
        prompts_conf.get("report_prompt_path"),
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