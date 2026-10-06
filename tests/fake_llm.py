"""离线测试用的假 LLM：按请求的 schema 返回针对示例小说《青灯渡》的固定结果。"""
from __future__ import annotations

import re

from novel2drama.analysis.analyzer import BibleSeed
from novel2drama.models import (
    AnalysisDelta, Character, ChapterAnalysis, ChapterLink, DialogueLine, Episode, Location,
    QualityScores, Relationship, Shot, StoryBible, WorldSetting,
)

SHEN = Character(id="shen_yan", name="沈砚", aliases=["砚哥"], role="主角", personality="隐忍善良",
                 appearance="清瘦少年，粗布短打", visual_prompt="lean teenage boy, black messy hair, coarse linen clothes",
                 voice="少年清亮", first_chapter=1)
HENG = Character(id="a_heng", name="阿蘅", role="青梅", personality="倔强温柔", appearance="双辫少女",
                 visual_prompt="young girl, two braids, light green dress", voice="少女清甜", first_chapter=1)
GU = Character(id="gu_changyuan", name="顾长渊", role="导师", personality="冷峻", appearance="墨色大氅，无鞘长剑",
               visual_prompt="middle-aged swordsman, black cloak, sheathless long sword", voice="低沉", first_chapter=1)
RED = Character(id="hong_yi", name="红衣女子", role="反派", personality="阴柔", appearance="红衣白面",
                visual_prompt="pale woman in red robes", voice="女声甜腻", first_chapter=2)


class FakeLLM:
    def __init__(self, low_first: bool = False):
        self.calls: list[str] = []
        self.low_first = low_first
        self._judged = 0

    def structured(self, *, system, context, prompt, schema):
        self.calls.append(schema.__name__)
        if schema is BibleSeed:
            return BibleSeed(bible=StoryBible(
                title="青灯渡",
                world=WorldSetting(genre="仙侠", era="古代", tone="悬疑温情", rules=["守灯人以血养灯"],
                                   art_style="chinese xianxia anime, cel shading", palette=["#3FD0C9", "#1B2A3A"]),
                characters=[SHEN, HENG],
                relationships=[Relationship(source="shen_yan", target="a_heng", type="青梅竹马",
                                            description="相依为命", intensity=4, since_chapter=1)],
                locations=[Location(id="ferry", name="青灯渡", description="江边渡口",
                                    visual_prompt="misty river ferry at dusk, old wooden boat, bronze lantern")],
            ))
        if schema is AnalysisDelta:
            n = int(re.search(r"第(\d+)章", prompt).group(1))
            links = [ChapterLink(target_chapter=n - 1, kind="因果", description="承接上章")] if n > 1 else []
            return AnalysisDelta(
                analysis=ChapterAnalysis(chapter=n, summary=f"第{n}章摘要", key_events=["事件"],
                                         characters_present=["沈砚", "a_heng", "gu_changyuan"], locations=["ferry"],
                                         emotional_arc="紧张", links=links, hook="灯里有眼睛", recap="上回说到…"),
                new_characters=[GU] if n == 1 else ([RED] if n == 2 else []),
                new_relationships=[Relationship(source="gu_changyuan", target="shen_yan", type="师叔",
                                                description="父亲的师弟", intensity=3, since_chapter=n)] if n == 3 else [],
                relationship_updates=[Relationship(source="沈砚", target="阿蘅", type="青梅竹马",
                                                   description="生死相托", intensity=5, since_chapter=1)] if n == 2 else [],
                plot_threads_opened=["灯中之眼"] if n == 3 else [],
            )
        if schema is Episode:
            n = int(re.search(r"为第(\d+)章生成", prompt).group(1))
            shots = [
                Shot(id=f"E{n:02d}-S01", duration=8, shot_type="大远景", camera="推", layout="full_bleed",
                     location="ferry", characters=[], action="江面", image_prompt="misty river",
                     dialogue=[DialogueLine(speaker="narrator", text="暮色压在青灯渡的江面上。")],
                     source_paragraphs=[0]),
                Shot(id=f"E{n:02d}-S02", duration=8, shot_type="中景", camera="固定", layout="single",
                     location="ferry", characters=["沈砚", "阿蘅"], action="对话", image_prompt="talking",
                     dialogue=[DialogueLine(speaker="a_heng", text="砚哥，又是这么点？"),
                               DialogueLine(speaker="shen_yan", text="够给你娘抓一副药了。")],
                     source_paragraphs=list(range(1, 8))),
                Shot(id=f"E{n:02d}-S03", duration=8, shot_type="近景", camera="拉", layout="manga_3",
                     location="ferry", characters=["gu_changyuan", "shen_yan"], action="对峙", image_prompt="standoff",
                     transition="叠化", source_paragraphs=list(range(8, 13))),
                Shot(id=f"E{n:02d}-S04", duration=8, shot_type="特写", camera="手持晃动", layout="inset_closeup",
                     location="ferry", characters=["shen_yan"], action="灯", image_prompt="lantern glowing",
                     transition="闪白", source_paragraphs=[12]),
            ]
            return Episode(chapter=n, title="渡口少年", hashtags=["动漫短剧", "仙侠"], shots=shots, cliffhanger="灯里有眼睛")
        if schema is QualityScores:
            self._judged += 1
            low = self.low_first and self._judged == 1
            v = 60 if low else 90
            return QualityScores(relationship_tightness=v, world_consistency=90, novel_fidelity=90,
                                 chapter_alignment=90, problems=["E01-S02: 关系表现不足"] if low else [])
        raise AssertionError(f"unexpected schema {schema}")
