"""自动分镜 + 质量评审闭环。

1. 以故事圣经、本章分析、关联章节摘要为上下文，生成本集分镜（Episode）。
2. 规则校验（确定性）：角色/地点 id 合法、原文段落覆盖率、关系对同框率等。
3. LLM 评审四项指标：人物关系紧密度 / 世界观贴合度 / 小说还原度 / 章节贴合度。
4. 任一指标低于阈值则带着问题清单重写分镜，最多 N 轮。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..llm import LLM
from ..models import Chapter, ChapterAnalysis, Episode, QualityScores, StoryBible
from ..styles import StylePreset, get_style
from .analyzer import system_for
from .bible import bible_brief, resolve_character_id

STORYBOARD_RULES = """分镜通用规范：
- 单镜 1.5-8 秒，台词镜头按每秒 4-5 个汉字估算时长。
- 开头 3 秒必须是强钩子（冲突/悬念/高光画面），若有前情则先用 recap。
- 结尾停在 cliffhanger 上，引导看下一集。
- 每个镜头的 source_paragraphs 必须指向它改编自的原文段落编号；全集应覆盖本章主要情节段落。
- 台词优先使用原文对白；原文没有对白的关键心理活动用 narrator 旁白或角色内心独白。
- characters 只能使用故事圣经中存在的角色 id；location 只能使用故事圣经中的地点 id。
- 世界观设定（招式、法宝、科技、服饰、建筑）在 image_prompt 中要具体且符合 world.rules 与 art_style。
- layout 选择：single 单人；two_shot 双人同框；split_vertical 上下分屏对峙/对话；split_horizontal 左右分屏；
  manga_3 三格漫画式快速蒙太奇；inset_closeup 大场景+角落特写；full_bleed 全幅大场面。
- image_prompt 用英文，只写构图、动作、表情、光线、镜头，不必重复角色外貌和画风（系统自动拼接）。"""


def rules_for(style: StylePreset) -> str:
    return f"{STORYBOARD_RULES}\n\n本剧风格（{style.description}）专属要求：\n{style.storyboard_rules}"

JUDGE_SYSTEM = """你是严格的改编质量评审。对照原文章节、故事圣经和分镜，按 0-100 打分：
- relationship_tightness 人物关系紧密度：人物关系是否被准确呈现并推动剧情，关系演变是否可见，互动镜头是否充分。
- world_consistency 世界观贴合度：画面与设定是否和故事圣经一致，有无违背设定或时代错乱。
- novel_fidelity 小说还原度：关键情节、台词、人物性格是否忠于原文，有无杜撰或遗漏重要情节。
- chapter_alignment 章节贴合度：是否紧扣本章内容、承接前文关联、留好下一集钩子，节奏是否适合短剧。
problems 中每条都要指出镜头 id 和具体问题；没有问题就留空。"""


@dataclass
class LintReport:
    errors: list[str] = field(default_factory=list)
    coverage: float = 0.0
    pair_coverage: float = 1.0

    def as_text(self) -> str:
        lines = [f"原文段落覆盖率 {self.coverage:.0%}，紧密关系同框率 {self.pair_coverage:.0%}"]
        lines += [f"- {e}" for e in self.errors]
        return "\n".join(lines)


def lint_episode(ep: Episode, chapter: Chapter, bible: StoryBible, analysis: ChapterAnalysis,
                 style: StylePreset | None = None) -> LintReport:
    """确定性校验并就地修正可自动修复的问题（如中文名→id）。"""
    report = LintReport()
    loc_ids = {loc.id for loc in bible.locations}
    covered: set[int] = set()
    for shot in ep.shots:
        fixed = []
        for ref in shot.characters:
            cid = resolve_character_id(bible, ref)
            if cid is None:
                report.errors.append(f"{shot.id}: 未知角色 {ref}")
            else:
                fixed.append(cid)
        shot.characters = fixed
        for line in shot.dialogue:
            if line.speaker != "narrator":
                line.speaker = resolve_character_id(bible, line.speaker) or line.speaker
        if loc_ids and shot.location not in loc_ids:
            report.errors.append(f"{shot.id}: 未知地点 {shot.location}")
        bad = [p for p in shot.source_paragraphs if not 0 <= p < len(chapter.paragraphs)]
        if bad:
            report.errors.append(f"{shot.id}: 段落编号越界 {bad}")
        covered.update(p for p in shot.source_paragraphs if 0 <= p < len(chapter.paragraphs))

    # 只统计有实质内容的段落（过滤 "嗯。" 之类）
    meaningful = {i for i, p in enumerate(chapter.paragraphs) if len(p) >= 15}
    report.coverage = len(covered & meaningful) / max(1, len(meaningful))
    if report.coverage < 0.6:
        report.errors.append(f"原文覆盖率仅 {report.coverage:.0%}，有主要情节未改编")

    present = set(analysis.characters_present)
    close_pairs = [
        (r.source, r.target) for r in bible.relationships
        if r.intensity >= 4 and r.source in present and r.target in present
    ]
    if close_pairs:
        together = sum(1 for a, b in close_pairs if any({a, b} <= set(s.characters) for s in ep.shots))
        report.pair_coverage = together / len(close_pairs)
        for a, b in close_pairs:
            if not any({a, b} <= set(s.characters) for s in ep.shots):
                report.errors.append(f"紧密关系 {a}↔{b} 本章同场却没有同框镜头")

    if style and style.wide_first and ep.shots:
        wide = sum(1 for s in ep.shots if s.shot_type in ("大远景", "远景", "全景"))
        if wide / len(ep.shots) < 0.7:
            report.errors.append(f"大场景风格要求 ≥70% 远景镜头，当前 {wide}/{len(ep.shots)}")
        pov = [s.id for s in ep.shots if s.shot_type in ("主观", "特写")]
        if pov:
            report.errors.append(f"第三视角风格不应使用主观/特写镜头：{', '.join(pov)}")
    if not 30 <= ep.duration <= 240:
        report.errors.append(f"总时长 {ep.duration:.0f}s 不在 30-240s 范围")
    return report


def _linked_context(analysis: ChapterAnalysis, history: dict[int, ChapterAnalysis]) -> str:
    parts = []
    for link in analysis.links:
        prev = history.get(link.target_chapter)
        if prev:
            parts.append(f"- 关联第{prev.chapter}章（{link.kind}：{link.description}）：{prev.summary}")
    return "【本章与前文的关联】\n" + ("\n".join(parts) if parts else "（无）")


def generate_episode(
    llm: LLM,
    bible: StoryBible,
    chapter: Chapter,
    analysis: ChapterAnalysis,
    history: dict[int, ChapterAnalysis],
    feedback: str = "",
    style: StylePreset | None = None,
) -> Episode:
    style = style or get_style(None)
    context = bible_brief(bible) + "\n\n" + rules_for(style)
    prev = history.get(chapter.index - 1)
    prompt = (
        f"{_linked_context(analysis, history)}\n\n"
        f"【本章分析】\n{analysis.model_dump_json(indent=1)}\n\n"
        f"【上一集结尾钩子】{prev.hook if prev else '（无，第一集）'}\n\n"
        f"【第{chapter.index}章原文】\n{chapter.numbered_text()}\n\n"
        f"请为第{chapter.index}章生成完整分镜，镜头 id 格式 E{chapter.index:02d}-S01。"
        "hashtags 给 4-6 个抖音话题（不带 #）。"
    )
    if feedback:
        prompt += f"\n\n上一版分镜的评审意见如下，请逐条修正后重新输出完整分镜：\n{feedback}"
    ep = llm.structured(system=system_for(style), context=context, prompt=prompt, schema=Episode)
    ep.chapter = chapter.index
    if prev and not ep.recap:
        ep.recap = prev.recap
    return ep


def judge_episode(llm: LLM, bible: StoryBible, chapter: Chapter, analysis: ChapterAnalysis, ep: Episode) -> QualityScores:
    prompt = (
        f"【本章分析】\n{analysis.model_dump_json(indent=1)}\n\n"
        f"【原文】\n{chapter.numbered_text()}\n\n"
        f"【分镜】\n{ep.model_dump_json(indent=1)}"
    )
    return llm.structured(system=JUDGE_SYSTEM, context=bible_brief(bible), prompt=prompt, schema=QualityScores)


def storyboard_chapter(
    llm: LLM,
    bible: StoryBible,
    chapter: Chapter,
    analysis: ChapterAnalysis,
    history: dict[int, ChapterAnalysis],
    threshold: int = 80,
    max_rounds: int = 3,
    style: StylePreset | None = None,
) -> tuple[Episode, QualityScores, LintReport]:
    feedback = ""
    best: tuple[Episode, QualityScores, LintReport] | None = None
    for _ in range(max_rounds):
        ep = generate_episode(llm, bible, chapter, analysis, history, feedback, style=style)
        lint = lint_episode(ep, chapter, bible, analysis, style)
        scores = judge_episode(llm, bible, chapter, analysis, ep)
        if best is None or scores.min_score > best[1].min_score:
            best = (ep, scores, lint)
        if scores.min_score >= threshold and not lint.errors:
            break
        feedback = "规则校验：\n" + lint.as_text() + "\n评审问题：\n" + "\n".join(
            f"- {p}" for p in [*scores.problems, *scores.suggestions]
        )
    assert best is not None
    return best
