"""离线知识索引的内容清洗、去重和轻量元数据增强。"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass


@dataclass(frozen=True)
class EnrichedText:
    """清洗后的文本及可直接写入向量库的低成本、可消费元数据。"""

    text: str
    tags: tuple[str, ...]


class DeepTextCleaner:
    """去除常见网页/PDF 噪声，且保留 Markdown 标题和原始语义内容。"""

    _NOISE_LINE = re.compile(
        r"^(?:\d{1,4}|第\s*\d+\s*页|https?://\S+|www\.\S+|[-_=*]{4,}|"
        r"(?:版权所有|免责声明|阅读原文|点击关注|关注我们|返回顶部|上一篇|下一篇).{0,24})$",
        re.IGNORECASE,
    )

    def clean(self, text: str) -> str:
        """规范 Unicode、清理导航性内容，并折叠重复的相邻行。"""
        normalized = unicodedata.normalize("NFKC", text)
        normalized = normalized.replace("\ufeff", "").replace("\u200b", "")
        normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")

        kept_lines: list[str] = []
        previous_key = ""
        for raw_line in normalized.split("\n"):
            line = re.sub(r"[ \t]+", " ", raw_line).strip()
            key = re.sub(r"\s+", "", line).lower()
            if not line or self._NOISE_LINE.fullmatch(line):
                continue
            if key == previous_key:
                continue
            kept_lines.append(line)
            previous_key = key

        return "\n".join(kept_lines).strip()


class ContentDeduplicator:
    """按规范化正文执行精确去重和 SimHash 近重复去重。"""

    def __init__(self, near_duplicate_hamming_distance: int = 3) -> None:
        self.near_duplicate_hamming_distance = near_duplicate_hamming_distance
        self._exact_hashes: set[str] = set()
        self._bands: dict[tuple[int, int], list[int]] = defaultdict(list)
        self.exact_duplicates = 0
        self.near_duplicates = 0

    @staticmethod
    def _canonical_text(text: str) -> str:
        return re.sub(r"\W+", "", text).lower()

    @classmethod
    def _simhash(cls, text: str) -> int:
        canonical = cls._canonical_text(text)
        features = [canonical[index : index + 3] for index in range(max(1, len(canonical) - 2))]
        weights = [0] * 64
        for feature in features:
            value = int.from_bytes(
                hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest(), "big"
            )
            for bit in range(64):
                weights[bit] += 1 if value & (1 << bit) else -1
        return sum(1 << bit for bit, weight in enumerate(weights) if weight >= 0)

    def is_duplicate(self, text: str) -> bool:
        """记录新内容；若与已记录内容相同或近似，则返回 ``True``。"""
        canonical = self._canonical_text(text)
        exact_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if exact_hash in self._exact_hashes:
            self.exact_duplicates += 1
            return True

        fingerprint = self._simhash(canonical)
        candidates: set[int] = set()
        for band in range(4):
            value = (fingerprint >> (band * 16)) & 0xFFFF
            candidates.update(self._bands[(band, value)])
        if any(
            (fingerprint ^ candidate).bit_count() <= self.near_duplicate_hamming_distance
            for candidate in candidates
        ):
            self.near_duplicates += 1
            return True

        self._exact_hashes.add(exact_hash)
        for band in range(4):
            value = (fingerprint >> (band * 16)) & 0xFFFF
            self._bands[(band, value)].append(fingerprint)
        return False


class MetadataEnricher:
    """不调用大模型，为切片生成可用于在线排序的主题标签。"""

    _TAG_RULES = {
        "动作": ("深蹲", "硬拉", "卧推", "引体", "俯卧撑", "训练", "动作"),
        "营养": ("蛋白", "碳水", "脂肪", "热量", "饮食", "补剂", "营养"),
        "防护": ("疼痛", "损伤", "受伤", "热身", "康复", "风险", "防护"),
        "下肢": ("腿", "髋", "膝", "踝", "臀"),
        "上肢": ("肩", "肘", "腕", "胸", "背"),
        "核心": ("核心", "腰", "脊柱", "腹"),
    }

    def __init__(self, max_tags: int = 4) -> None:
        self.max_tags = max_tags

    def extract_tags(self, text: str, title: str = "") -> tuple[str, ...]:
        """从标题和正文提取稳定标签；同一规则也用于查询侧标签识别。"""
        target = f"{title}\n{text}".lower()
        return tuple(
            tag
            for tag, keywords in self._TAG_RULES.items()
            if any(word in target for word in keywords)
        )[: self.max_tags]

    def enrich(self, text: str, title: str = "") -> EnrichedText:
        """生成写入索引的文本和标签，不生成没有消费者的摘要字段。"""
        return EnrichedText(text=text, tags=self.extract_tags(text, title))
