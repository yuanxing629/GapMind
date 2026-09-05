"""将较长的 parsed-markdown 文档切分为适合 LLM 的批次。

抽取 prompt 面向整篇论文，但配置的远程模型 context window 要求我们分批处理。策略如下：

* 切分时保持段落/标题（``\n## ``, ``\n\n``）边界完整，避免 LLM 丢失语义结构；
* 尾部批次始终结束在真实文档字符偏移处，绝不静默丢弃最后一段；
* 按 ``overlap_chars`` 重叠批次，使跨批次实体解析仍然有效（批次 N 尾部提及的方法可以
  在批次 N+1 中再次出现）。

该函数有意不产生副作用，只执行纯字符串计算，因此无需启动 DB 或 LLM client 即可进行
单元测试。
"""

from __future__ import annotations

# 默认值说明：
#   * ``max_chars``（24 000）：为配置的远程模型提供更小的有界文本切片，降低
#     ``items + relations`` JSON 响应达到输出上限而被截断的概率。
#   * ``overlap_chars``（1 000）：足以重新呈现段尾方法，又不会明显增加 token 消耗。
DEFAULT_MAX_CHARS = 24_000
DEFAULT_OVERLAP_CHARS = 1_000


def split_extraction_batches(
    text: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> list[tuple[int, str]]:
    """返回覆盖 ``text`` 的 ``[(start_offset, batch_text), ...]``。

    每个 tuple 的 ``start_offset`` 是 ``batch_text[0]`` 在原始文档中的绝对字符位置，
    因此调用方可以将 evidence span 回链到主文档。
    """
    if len(text) <= max_chars:
        return [(0, text)]

    batches: list[tuple[int, str]] = []
    start = 0
    while start < len(text):
        hard_end = min(start + max_chars, len(text))
        end = hard_end
        if hard_end < len(text):
            min_split = start + max_chars // 2
            heading = text.rfind("\n## ", min_split, hard_end)
            paragraph = text.rfind("\n\n", min_split, hard_end)
            split_at = heading if heading >= min_split else paragraph
            if split_at >= min_split:
                end = split_at
        if end <= start:
            end = hard_end
        batches.append((start, text[start:end]))
        if end >= len(text):
            break
        start = max(end - overlap_chars, start + 1)
    return batches


__all__ = ["split_extraction_batches", "DEFAULT_MAX_CHARS", "DEFAULT_OVERLAP_CHARS"]
