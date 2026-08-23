from app.utils.config_handler import get_prompts_config
from app.utils.logger_handler import logger
from app.utils.path_tool import get_abs_path


def load_system_prompts():
    try:
        system_prompt_path = get_abs_path(get_prompts_config()["main_prompt_path"])
    except KeyError as e:
        logger.error("[load_system_prompts]在yaml配置项中没有load_system_prompts配置项")
        raise e

    try:
        return open(system_prompt_path, "r", encoding="utf-8").read()
    except Exception as e:
        logger.error(f"[load_system_prompts]解析系统提示词出错，{str(e)}")
        raise e


def load_report_prompts():
    try:
        report_prompt_path = get_abs_path(get_prompts_config()["report_prompt_path"])
    except KeyError as e:
        logger.error("[load_report_prompts]在yaml配置项中没有report_prompts_path配置项")
        raise e

    try:
        return open(report_prompt_path, "r", encoding="utf-8").read()
    except Exception as e:
        logger.error(f"[load_report_prompts]解析报告生成提示词出错，{str(e)}")
        raise e


def load_health_extract_prompts():
    try:
        health_prompt_path = get_abs_path(get_prompts_config()["health_extract_prompt_path"])
    except KeyError as e:
        logger.error("[load_health_extract_prompts]在yaml配置项中没有health_extract_path配置项")
        raise e

    try:
        return open(health_prompt_path, "r", encoding="utf-8").read()
    except Exception as e:
        logger.error(f"[load_health_extract_prompt]解析健康提取提示词出错，{str(e)}")
        raise e


def load_training_plan_prompt() -> str:
    """Load the schema-bound prompt used solely for weekly-plan JSON generation."""

    try:
        prompt_path = get_abs_path(get_prompts_config()["training_plan_prompt_path"])
        return open(prompt_path, "r", encoding="utf-8").read()
    except KeyError as error:
        logger.error("[load_training_plan_prompt] prompts.yml 缺少训练计划提示词路径")
        raise error
    except Exception as error:
        logger.error("[load_training_plan_prompt]读取训练计划提示词失败：%s", error)
        raise


if __name__ == "__main__":
    print(load_system_prompts())
