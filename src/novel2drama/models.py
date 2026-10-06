"""全流程共享的数据结构。

所有 LLM 输出都用这些 Pydantic 模型做结构化约束，每个阶段的产物都以 JSON
落盘到工作区，因此任意阶段都可以单独重跑 / 人工修改后继续。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------- 原文 / 章节
class Chapter(BaseModel):
    index: int = Field(description="章节序号，从 1 开始")
    title: str
    paragraphs: list[str] = Field(description="按段落切分的正文，分镜通过段落下标回指原文")

    @property
    def text(self) -> str:
        return "\n".join(self.paragraphs)

    def numbered_text(self) -> str:
        """带段落编号的正文，供 LLM 引用 source_paragraphs。"""
        return "\n".join(f"[{i}] {p}" for i, p in enumerate(self.paragraphs))


# ---------------------------------------------------------------- 故事圣经（全书级）
class Character(BaseModel):
    id: str = Field(description="稳定的英文/拼音 id，如 lin_xiao")
    name: str
    aliases: list[str] = Field(default_factory=list, description="别名、称呼、绰号")
    role: str = Field(description="主角/反派/配角/群像等")
    personality: str
    appearance: str = Field(description="外貌、服饰、标志物；用于画面一致性")
    visual_prompt: str = Field(description="英文绘图锚点提示词，所有出场镜头都会拼接它，保证角色形象一致")
    voice: str = Field(description="声线描述，如 少年清亮 / 低沉沙哑")
    first_chapter: int
    arc: str = Field(default="", description="截至目前的人物弧光")


class Relationship(BaseModel):
    source: str = Field(description="角色 id")
    target: str = Field(description="角色 id")
    type: str = Field(description="师徒/恋人/宿敌/兄弟/主仆/同门 等")
    description: str
    intensity: int = Field(ge=1, le=5, description="关系紧密程度 1-5，影响构图中两人距离与同框频率")
    since_chapter: int
    history: list[str] = Field(default_factory=list, description="关系演变：'第3章: 由敌转友' 这样的记录")


class Location(BaseModel):
    id: str
    name: str
    description: str
    visual_prompt: str = Field(description="英文场景锚点提示词")


class Faction(BaseModel):
    name: str
    description: str
    members: list[str] = Field(default_factory=list, description="角色 id 列表")


class WorldSetting(BaseModel):
    genre: str = Field(description="题材：玄幻/都市/仙侠/悬疑/科幻 …")
    era: str
    tone: str = Field(description="整体基调")
    rules: list[str] = Field(default_factory=list, description="世界观硬设定：修炼体系、科技水平、禁忌等")
    factions: list[Faction] = Field(default_factory=list)
    art_style: str = Field(description="英文画风提示词，全剧统一，如 'chinese xianxia anime, cel shading'")
    palette: list[str] = Field(default_factory=list, description="主色调 hex，用于画布 UI 与字幕")


class StoryBible(BaseModel):
    title: str
    world: WorldSetting
    characters: list[Character] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    locations: list[Location] = Field(default_factory=list)
    plot_threads: list[str] = Field(default_factory=list, description="尚未回收的伏笔/主线/支线")

    def character(self, cid: str) -> Character | None:
        return next((c for c in self.characters if c.id == cid), None)

    def location(self, lid: str) -> Location | None:
        return next((loc for loc in self.locations if loc.id == lid), None)

    def relation(self, a: str, b: str) -> Relationship | None:
        for r in self.relationships:
            if {r.source, r.target} == {a, b}:
                return r
        return None


# ---------------------------------------------------------------- 章节分析
class ChapterLink(BaseModel):
    target_chapter: int = Field(description="关联的前序章节序号")
    kind: Literal["因果", "伏笔回收", "人物重逢", "关系转折", "地点重访", "主题呼应", "冲突升级"]
    description: str


class ChapterAnalysis(BaseModel):
    chapter: int
    summary: str
    key_events: list[str]
    characters_present: list[str] = Field(description="角色 id")
    locations: list[str] = Field(description="地点 id")
    emotional_arc: str
    links: list[ChapterLink] = Field(default_factory=list, description="与前文章节的关联")
    hook: str = Field(description="本集结尾钩子（短剧留人点）")
    recap: str = Field(description="下一集开头 3 秒的'前情提要'一句话")


class AnalysisDelta(BaseModel):
    """单章分析的 LLM 返回：本章分析 + 对故事圣经的增量更新。"""

    analysis: ChapterAnalysis
    new_characters: list[Character] = Field(default_factory=list)
    character_updates: list[Character] = Field(default_factory=list, description="已有角色的完整新版本（外貌变化、弧光推进）")
    new_relationships: list[Relationship] = Field(default_factory=list)
    relationship_updates: list[Relationship] = Field(default_factory=list)
    new_locations: list[Location] = Field(default_factory=list)
    world_rules_added: list[str] = Field(default_factory=list)
    plot_threads_opened: list[str] = Field(default_factory=list)
    plot_threads_closed: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- 分镜
ShotType = Literal["大远景", "远景", "全景", "中景", "近景", "特写", "过肩", "主观"]
Camera = Literal["固定", "推", "拉", "摇", "移", "跟", "环绕", "手持晃动"]
Layout = Literal["single", "two_shot", "split_vertical", "split_horizontal", "manga_3", "inset_closeup", "full_bleed"]


class DialogueLine(BaseModel):
    speaker: str = Field(description="角色 id；旁白用 narrator")
    text: str = Field(description="台词，尽量保留原文措辞，必要时口语化压缩")
    emotion: str = ""


class Shot(BaseModel):
    id: str = Field(description="如 E03-S07")
    duration: float = Field(ge=1.0, le=12.0, description="秒")
    shot_type: ShotType
    camera: Camera
    layout: Layout = Field(description="画布布局，由画布重组引擎渲染")
    location: str = Field(description="地点 id")
    characters: list[str] = Field(description="入镜角色 id，按画面重要性排序")
    action: str = Field(description="画面内容/动作描述（中文）")
    image_prompt: str = Field(description="英文画面提示词（不需要重复角色和画风锚点，系统会自动拼接）")
    dialogue: list[DialogueLine] = Field(default_factory=list)
    sfx: str = ""
    transition: Literal["切", "叠化", "闪白", "黑场", "滑动"] = "切"
    source_paragraphs: list[int] = Field(description="对应原文段落下标，用于还原度校验")


class Episode(BaseModel):
    chapter: int
    title: str = Field(description="短剧标题（抖音标题），≤30 字")
    hashtags: list[str]
    recap: str = ""
    shots: list[Shot]
    cliffhanger: str

    @property
    def duration(self) -> float:
        return sum(s.duration for s in self.shots)


# ---------------------------------------------------------------- 质量评审
class QualityScores(BaseModel):
    relationship_tightness: int = Field(ge=0, le=100, description="人物关系紧密度")
    world_consistency: int = Field(ge=0, le=100, description="世界观贴合度")
    novel_fidelity: int = Field(ge=0, le=100, description="小说还原度")
    chapter_alignment: int = Field(ge=0, le=100, description="章节贴合度")
    problems: list[str] = Field(default_factory=list, description="具体问题，指向镜头 id")
    suggestions: list[str] = Field(default_factory=list)

    @property
    def min_score(self) -> int:
        return min(self.relationship_tightness, self.world_consistency, self.novel_fidelity, self.chapter_alignment)
