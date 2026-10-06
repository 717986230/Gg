"""读取整本小说并自动切分章节。

支持 .txt（自动识别 UTF-8 / GB18030 / Big5）、.epub（需 pip install .[epub]）。
章节识别规则覆盖常见网文格式：
  第一章 / 第1章 / 第一百零三回 / 第十卷 第三章 / 序章 / 楔子 / 番外 / Chapter 12
识别不到章节标题时按字数均分，保证任何文本都能出片。
"""
from __future__ import annotations

import re
from pathlib import Path

from .models import Chapter

_CN_NUM = "零〇一二两三四五六七八九十百千万"
CHAPTER_RE = re.compile(
    rf"^\s*(?:"
    rf"(?:第[{_CN_NUM}\d]+[卷部集]\s*)?第[{_CN_NUM}\d]+[章回节话](?:[\s:：、.·\-—]+.*)?"
    rf"|(?:序章|序言|楔子|引子|尾声|后记|番外)(?:[\s:：、.·\-—]+.*)?"
    rf"|chapter\s+\d+(?:[\s:：.\-—]+.*)?"
    rf")\s*$",
    re.IGNORECASE,
)
MAX_TITLE_LEN = 40


def read_text(path: str | Path) -> str:
    path = Path(path)
    if path.suffix.lower() == ".epub":
        return _read_epub(path)
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "gb18030", "big5"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _read_epub(path: Path) -> str:
    try:
        from bs4 import BeautifulSoup
        from ebooklib import ITEM_DOCUMENT, epub
    except ImportError as e:  # pragma: no cover - optional dependency
        raise RuntimeError("读取 epub 需要: pip install 'novel2drama[epub]'") from e
    book = epub.read_epub(str(path))
    parts = []
    for item in book.get_items_of_type(ITEM_DOCUMENT):
        soup = BeautifulSoup(item.get_content(), "html.parser")
        for tag in soup.find_all(["h1", "h2", "h3", "p"]):
            text = tag.get_text(strip=True)
            if text:
                parts.append(text)
    return "\n".join(parts)


def _clean_lines(text: str) -> list[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("　", " ")
    return [ln.strip() for ln in text.split("\n") if ln.strip()]


def is_chapter_title(line: str) -> bool:
    return len(line) <= MAX_TITLE_LEN and bool(CHAPTER_RE.match(line))


def split_chapters(text: str, fallback_chars: int = 3000) -> list[Chapter]:
    lines = _clean_lines(text)
    chapters: list[Chapter] = []
    title: str | None = None
    buf: list[str] = []
    preface: list[str] = []

    def flush() -> None:
        if title is not None and buf:
            chapters.append(Chapter(index=len(chapters) + 1, title=title, paragraphs=list(buf)))

    for line in lines:
        if is_chapter_title(line):
            flush()
            title, buf = line, []
        elif title is None:
            preface.append(line)
        else:
            buf.append(line)
    flush()

    if not chapters:
        return _split_by_length(lines, fallback_chars)
    # 书名/简介等前言并入第一章开头，避免丢信息
    if preface and len("".join(preface)) > 200:
        chapters.insert(0, Chapter(index=0, title="序", paragraphs=preface))
        for i, ch in enumerate(chapters, start=1):
            ch.index = i
    return chapters


def _split_by_length(lines: list[str], size: int) -> list[Chapter]:
    chapters: list[Chapter] = []
    buf: list[str] = []
    count = 0
    for line in lines:
        buf.append(line)
        count += len(line)
        if count >= size:
            chapters.append(Chapter(index=len(chapters) + 1, title=f"第{len(chapters) + 1}集", paragraphs=buf))
            buf, count = [], 0
    if buf:
        chapters.append(Chapter(index=len(chapters) + 1, title=f"第{len(chapters) + 1}集", paragraphs=buf))
    return chapters


def guess_title(path: str | Path, text: str) -> str:
    first = _clean_lines(text[:500])
    if first:
        m = re.match(r"^《(.+?)》", first[0])
        if m:
            return m.group(1)
    return Path(path).stem
