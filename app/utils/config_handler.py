"""
YAML 配置文件加载模块。

所有配置通过 @lru_cache 惰性加载，首次调用时读取 YAML 文件并缓存。
测试中可通过 get_xxx_config.cache_clear() 重置。
"""
import yaml
from functools import lru_cache
from app.utils.path_tool import get_abs_path

def _load_yaml(relative_path: str) -> dict:
    abs_path = get_abs_path(relative_path)
    with open(abs_path, "r", encoding="utf-8") as f:
        return yaml.load(f, Loader=yaml.SafeLoader)

@lru_cache(maxsize=1)
def get_models_config() -> dict:
    return _load_yaml("config/models.yml")

@lru_cache(maxsize=1)
def get_chroma_config() -> dict:
    return _load_yaml("config/chroma.yml")

@lru_cache(maxsize=1)
def get_synonyms_config() -> dict:
    return _load_yaml("config/synonyms.yml")

@lru_cache(maxsize=1)
def get_prompts_config() -> dict:
    return _load_yaml("config/prompts.yml")

if __name__ == '__main__':
    print(get_models_config()["chat_model_name"])
