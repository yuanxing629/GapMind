"""尽力提取 PDF 元数据。

使用 PyMuPDF (fitz) 读取 PDF 内嵌元数据字典，其中通常包含 title / author / creationDate。
学术 PDF 的元数据质量差异很大：有些作者字段格式良好，许多没有有用内容。
本模块尽力抽取可用字段，其余留给调用方填充或保持为空。

不调用 LLM，也不解析正文文本，这属于 Phase 3。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import fitz  # PyMuPDF


@dataclass
class PdfMetadata:
    """尽力提取的元数据，任意字段都可能为 None 或空值。"""

    title: str | None = None
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    page_count: int = 0


def extract_metadata(content: bytes) -> PdfMetadata:
    """从 PDF 字节中提取元数据。

    在内存中打开 PDF，读取其元数据字典，并尝试从 creationDate 解析年份。
    返回尽力抽取出的 PdfMetadata；格式错误的 PDF 不会抛出异常（返回空字段）。
    """
    result = PdfMetadata()
    try:
        doc = fitz.open(stream=content, filetype="pdf")
    except Exception:
        # PDF 无效或 PyMuPDF 无法打开时返回空结果。
        return result

    try:
        result.page_count = doc.page_count
        meta = doc.metadata or {}

        # Title：取 PDF metadata 的 'title' 字段并去除首尾空白。
        raw_title = (meta.get("title") or "").strip()
        if raw_title and _looks_like_title(raw_title):
            result.title = raw_title

        # Authors：PDF metadata 的 'author' 字段通常是单个字符串，例如
        # "Alice; Bob"、"Alice, Bob" 或 "Alice and Bob"。按常见分隔符拆分。
        raw_author = (meta.get("author") or "").strip()
        if raw_author:
            authors = _split_authors(raw_author)
            if authors:
                result.authors = authors

        # Year：优先从 creationDate（'D:YYYYMMDD...'）解析，然后 fallback 到 modDate。
        # 两者都不存在时保留为 None。
        result.year = _parse_year(meta.get("creationDate")) or _parse_year(
            meta.get("modDate")
        )
    finally:
        doc.close()

    return result


def _split_authors(raw: str) -> list[str]:
    """按常见分隔符拆分作者字符串。"""
    # 按 ';'、','、' and '、'&' 拆分，只保留看起来像姓名的部分。
    parts = re.split(r"[;,]|\band\b|&", raw)
    return [p.strip() for p in parts if _looks_like_author(p.strip())]


def _looks_like_title(s: str) -> bool:
    """拒绝明显无效的标题（PDF 生成器经常留下 'Untitled' 等值）。"""
    if not s:
        return False
    lower = s.lower()
    junk = {"untitled", "microsoft word -", "draft", "pdf"}
    return not any(lower.startswith(j) for j in junk)


def _looks_like_author(s: str) -> bool:
    """拒绝明显无效的作者字符串。"""
    if not s or len(s) > 200:
        return False
    # 必须至少包含一个字母，且不能是 URL 或文件名。
    if not re.search(r"[A-Za-z]", s):
        return False
    if "http" in s.lower() or ".pdf" in s.lower():
        return False
    return True


def _parse_year(raw: str | None) -> int | None:
    """从类似 'D:20240315...' 的 PDF 日期字符串中解析 4 位年份。"""
    if not raw:
        return None
    # PDF 日期格式为 D:YYYYMMDDHHmmSS+TZ，只取前 4 位数字。
    match = re.search(r"(?:D:)?(\d{4})", raw)
    if not match:
        return None
    year = int(match.group(1))
    # 合理性边界：拒绝明显的无效值。
    if 1900 <= year <= 2100:
        return year
    return None
