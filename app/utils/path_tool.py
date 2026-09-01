"""
为整个工程提供统一的绝对路径
"""

import os


def get_project_root() -> str:
    """返回当前工程的根目录绝对路径。"""
    # 当前文件的绝对路径
    current_file = os.path.abspath(__file__)
    # 获取工程的根目录，先获取文件所在的文件夹绝对路径
    current_dir = os.path.dirname(current_file)
    # 获取工程根目录
    project_root = os.path.dirname(os.path.dirname(current_dir))

    return project_root


def get_abs_path(relative_path: str) -> str:
    """将工程内相对路径拼接为绝对路径。"""
    project_root = get_project_root()
    return os.path.join(project_root, relative_path)


if __name__ == "__main__":
    print(get_abs_path("config/config.txt"))
