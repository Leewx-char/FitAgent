"""将已许可的 FitKG-CN 标注数据转换为带标题层级的 Markdown 文档。"""

from __future__ import annotations

import json
from pathlib import Path

from app.utils.path_tool import get_abs_path

_RAW_DATA_PATH = Path(
    "storage/rag/sources/fitkg-cn/extracted/NYN921-FitKG-CN-41b1142/data/fitkg-cn"
)
_OUTPUT_PATH = Path("data/external/fitkg-cn")
_SPLITS = ("train", "dev")


def _normalize_text(tokens: list[str]) -> str:
    """将标注数据的字符级 token 还原为适合阅读和检索的句子。"""
    return "".join(tokens).replace("\t", " ").replace("\n", " ").strip()


def _entity_text(tokens: list[str], entity: dict) -> str:
    """根据实体边界还原实体文本。"""
    return _normalize_text(tokens[entity["start"] : entity["end"]])


def _render_record(record: dict, ordinal: int) -> str:
    """将一个关系抽取样本渲染成独立的二级 Markdown 标题区块。"""
    tokens = record["tokens"]
    entities = record.get("entities", [])
    entity_names = [_entity_text(tokens, entity) for entity in entities]
    lines = [f"## 样本 {ordinal:05d}", f"原始文本：{_normalize_text(tokens)}", "", "### 已标注实体"]
    lines.extend(
        f"- {name}（类型：{entity['type']}）" for name, entity in zip(entity_names, entities)
    )
    if not entities:
        lines.append("- 无")

    lines.extend(["", "### 已标注关系"])
    relations = record.get("relations", [])
    for relation in relations:
        head = entity_names[relation["head"]]
        tail = entity_names[relation["tail"]]
        lines.append(f"- {head} ——{relation['type']}→ {tail}")
    if not relations:
        lines.append("- 无")
    return "\n".join(lines)


def render_markdown(records: list[dict], split_name: str) -> str:
    """将一个数据划分渲染为标题清晰、可供 Markdown 分割器处理的文档。"""
    title = "训练集" if split_name == "train" else "验证集"
    header = [
        f"# FitKG-CN 中文科学健身知识图谱（{title}）",
        "",
        "> 来源：Du, S., Liu, Z. & Pan, B. FitKG-CN v1.1，Zenodo，DOI: 10.5281/zenodo.14355004",
        "> 许可证：CC BY 4.0。数据为知识图谱标注样本，不应被表述为医疗或个性化运动处方。",
        "",
    ]
    return (
        "\n\n".join(
            [
                "\n".join(header),
                *(
                    _render_record(record, ordinal)
                    for ordinal, record in enumerate(records, start=1)
                ),
            ]
        )
        + "\n"
    )


def build_fitkg_markdown(
    raw_data_path: Path | None = None, output_path: Path | None = None
) -> list[Path]:
    """从审核暂存区生成 FitKG-CN 的训练集和验证集 Markdown 文档。"""
    source_path = raw_data_path or Path(get_abs_path(str(_RAW_DATA_PATH)))
    destination = output_path or Path(get_abs_path(str(_OUTPUT_PATH)))
    destination.mkdir(parents=True, exist_ok=True)

    outputs = []
    for split_name in _SPLITS:
        records = json.loads((source_path / f"{split_name}.json").read_text(encoding="utf-8"))
        target = destination / f"fitkg-cn-{split_name}.md"
        target.write_text(render_markdown(records, split_name), encoding="utf-8")
        outputs.append(target)
    return outputs


def main() -> None:
    """命令行入口：``python -m app.services.fitkg_markdown_builder``。"""
    outputs = build_fitkg_markdown()
    print("已生成 FitKG-CN Markdown 文档：")
    for output in outputs:
        print(output)


if __name__ == "__main__":
    main()
