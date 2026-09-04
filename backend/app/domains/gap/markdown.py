"""构建面向模型的论文视图，排除实验密集型章节。"""

from __future__ import annotations

import re

HASH_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$")
BOLD_HEADING = re.compile(r"^\s*\*\*(.+?)\*\*\s*$")
PLAIN_HEADING = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*|[A-Z])[.)]?\s+([A-Z][A-Za-z0-9 &:/-]{2,100}?)[.:]?\s*$"
)
ALL_CAPS_HEADING = re.compile(r"^\s*([A-Z][A-Z0-9 &:/-]{2,100}?)[.:]?\s*$")
DECORATION = re.compile(r"[*_`#]+")
SPACE = re.compile(r"\s+")

DROP = (
    re.compile(r"^(?:experiments?|experimental\b)"),
    re.compile(r"^(?:evaluation|empirical evaluation|empirical study)\b"),
    re.compile(r"^(?:results?|results and discussion)\b"),
    re.compile(r"^(?:ablation|ablation study|ablation analysis)\b"),
    re.compile(r"^(?:implementation details?|experimental settings?)\b"),
    re.compile(r"^(?:case study|case studies)\b"),
)
TERMINAL_DROP = (
    re.compile(r"^(?:appendix|appendices)\b"),
    re.compile(r"^(?:supplementary|supplemental)(?: material| information)?\b"),
    re.compile(r"^(?:references|bibliography)\b"),
    re.compile(r"^(?:acknowledg(?:e)?ments?)\b"),
)
KEEP_AFTER_DROP = (
    re.compile(r"^(?:conclusions?|concluding remarks?)\b"),
    re.compile(r"^(?:limitations?|limitations and future work)\b"),
    re.compile(r"^(?:future work|future directions?)\b"),
    re.compile(r"^(?:discussion|discussion and conclusion)\b"),
)


def _heading(line: str) -> str | None:
    for pattern in (HASH_HEADING, BOLD_HEADING, PLAIN_HEADING, ALL_CAPS_HEADING):
        match = pattern.match(line)
        if match:
            value = DECORATION.sub("", match.group(1))
            value = SPACE.sub(" ", value).strip(" .:：-").lower()
            return re.sub(r"^(?:\d+(?:\.\d+)*|[ivxlcdm]+|[a-z])[.)]?\s+", "", value)
    return None


def _matches(value: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(pattern.search(value) for pattern in patterns)


def compact_markdown(markdown: str) -> str:
    kept: list[str] = []
    dropping = False
    terminal = False
    for line in markdown.splitlines():
        heading = _heading(line)
        if heading is not None:
            if terminal:
                pass
            elif _matches(heading, KEEP_AFTER_DROP):
                dropping = False
            elif _matches(heading, TERMINAL_DROP):
                dropping = True
                terminal = True
            elif _matches(heading, DROP):
                dropping = True
        if not dropping:
            kept.append(line)
    return "\n".join(kept).strip()

