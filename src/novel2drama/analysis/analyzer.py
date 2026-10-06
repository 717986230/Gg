"""全书分析：建立故事圣经 → 逐章分析并维护跨章节关联。

逐章顺序处理（而不是并发），因为每一章都要看到"截至上一章"的故事圣经和
前情摘要，才能识别伏笔回收、人物重逢、关系转折等跨章节联系。
"""
from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel, Field

from ..llm import LLM
from ..models import AnalysisDelta, Chapter, ChapterAnalysis, StoryBible
from .bible import apply_delta, bible_brief

SYSTEM = """你是资深的动漫短剧改编编剧兼设定统筹。你的任务是把网络小说忠实地改编成抖音竖屏动漫短剧。
改编原则（按优先级）：
1. 小说还原度：情节、台词、人物性格以原文为准，不杜撰原文没有的关键情节。
2. 人物关系：准确识别人物之间的关系及其演变，关系是剧情张力的核心。
3. 世界观一致：所有设定（修炼体系、科技、势力、地理）必须与故事圣经保持一致；发现新设定时补充而不是改写。
4. 章节关联：每章都要识别与前文的因果、伏笔回收、人物重逢、关系转折等联系。
角色 id 一律使用小写拼音或英文下划线形式（如 lin_xiao），一旦确定绝不更改；引用已有角色时必须使用故事圣经里的 id。
visual_prompt 一律用英文，描述稳定的外观特征（发型发色、瞳色、年龄感、服饰、标志物），不要写动作和表情。"""


class BibleSeed(BaseModel):
    bible: StoryBible
    reasoning: str = Field(default="", description="对题材与画风选择的简短说明")


def _toc(chapters: list[Chapter]) -> str:
    return "\n".join(f"{c.index}. {c.title}" for c in chapters)


def seed_bible(llm: LLM, title: str, chapters: list[Chapter], sample_chapters: int = 2) -> StoryBible:
    sample = "\n\n".join(f"## {c.title}\n{c.text}" for c in chapters[:sample_chapters])
    prompt = (
        f"小说《{title}》共 {len(chapters)} 章。目录：\n{_toc(chapters)}\n\n开篇正文：\n{sample}\n\n"
        "请据此建立初始故事圣经：判断题材、时代、基调，确定统一的动漫画风(art_style)与 3-5 个主色调，"
        "列出开篇已出场的角色、关系、地点与世界观规则。characters/relationships/locations 只写开篇确实出现的。"
    )
    seed = llm.structured(system=SYSTEM, context="", prompt=prompt, schema=BibleSeed)
    seed.bible.title = title
    return seed.bible


def _recap(analyses: list[ChapterAnalysis], detailed_last: int = 5) -> str:
    if not analyses:
        return "（这是第一章，无前情）"
    lines = []
    for a in analyses[:-detailed_last]:
        lines.append(f"第{a.chapter}章：{a.summary[:60]}")
    for a in analyses[-detailed_last:]:
        lines.append(
            f"第{a.chapter}章：{a.summary}\n  关键事件：{'；'.join(a.key_events)}\n  结尾钩子：{a.hook}"
        )
    return "【前情摘要】\n" + "\n".join(lines)


def analyze_chapter(llm: LLM, bible: StoryBible, chapter: Chapter, previous: list[ChapterAnalysis]) -> AnalysisDelta:
    context = bible_brief(bible) + "\n\n" + _recap(previous)
    prompt = (
        f"请分析第{chapter.index}章《{chapter.title}》。正文（[n] 为段落编号）：\n{chapter.numbered_text()}\n\n"
        "要求：\n"
        "- analysis.links：列出本章与前文章节的具体关联（因果/伏笔回收/人物重逢/关系转折/地点重访/主题呼应/冲突升级），"
        "target_chapter 必须是小于本章的章节号；第一章可为空。\n"
        "- 本章新出场角色放 new_characters；已有角色若外貌/身份/弧光有变化，把完整新版本放 character_updates。\n"
        "- 关系有新建或变化（类型、亲密度）分别放 new_relationships / relationship_updates，history 写明本章发生了什么。\n"
        "- 新出现的地点放 new_locations；新揭示的世界观设定放 world_rules_added。\n"
        "- 新埋下的伏笔放 plot_threads_opened；本章回收的伏笔（照抄故事圣经 plot_threads 原文）放 plot_threads_closed。\n"
        "- hook 写本集结尾最抓人的悬念；recap 写下一集开头用的一句话前情提要。"
    )
    delta = llm.structured(system=SYSTEM, context=context, prompt=prompt, schema=AnalysisDelta)
    delta.analysis.chapter = chapter.index
    delta.analysis.links = [l for l in delta.analysis.links if 0 < l.target_chapter < chapter.index]
    return delta


def analyze_novel(
    llm: LLM,
    title: str,
    chapters: list[Chapter],
    bible: StoryBible | None = None,
    done: list[ChapterAnalysis] | None = None,
    on_chapter: Callable[[StoryBible, ChapterAnalysis], None] | None = None,
) -> tuple[StoryBible, list[ChapterAnalysis]]:
    """可断点续跑：传入已完成的 bible/analyses 会从下一章继续。"""
    bible = bible or seed_bible(llm, title, chapters)
    analyses = list(done or [])
    finished = {a.chapter for a in analyses}
    for chapter in chapters:
        if chapter.index in finished:
            continue
        delta = analyze_chapter(llm, bible, chapter, analyses)
        bible = apply_delta(bible, delta)
        analyses.append(delta.analysis)
        if on_chapter:
            on_chapter(bible, delta.analysis)
    return bible, analyses
