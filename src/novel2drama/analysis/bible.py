"""故事圣经（Story Bible）的合并逻辑。

逐章分析得到的增量（新角色、关系变化、新地点、世界观设定…）在这里确定性地
合并进全书唯一的 StoryBible，保证后续分镜/绘图都引用同一份人物与世界设定。
"""
from __future__ import annotations

from ..models import AnalysisDelta, Character, StoryBible


def _norm(s: str) -> str:
    return s.strip().lower()


def resolve_character_id(bible: StoryBible, ref: str) -> str | None:
    """通过 id / 姓名 / 别名查找角色，处理模型偶尔用中文名代替 id 的情况。"""
    key = _norm(ref)
    for c in bible.characters:
        if _norm(c.id) == key or _norm(c.name) == key or key in {_norm(a) for a in c.aliases}:
            return c.id
    return None


def _merge_character(old: Character, new: Character) -> Character:
    merged = old.model_copy(deep=True)
    merged.aliases = sorted((set(old.aliases) | set(new.aliases)) - {old.name})
    for field in ("role", "personality", "appearance", "visual_prompt", "voice", "arc"):
        value = getattr(new, field)
        if value:
            setattr(merged, field, value)
    merged.first_chapter = min(old.first_chapter, new.first_chapter)
    return merged


def apply_delta(bible: StoryBible, delta: AnalysisDelta) -> StoryBible:
    bible = bible.model_copy(deep=True)
    chapter = delta.analysis.chapter

    for c in [*delta.new_characters, *delta.character_updates]:
        existing = resolve_character_id(bible, c.id) or resolve_character_id(bible, c.name)
        if existing:
            idx = next(i for i, x in enumerate(bible.characters) if x.id == existing)
            bible.characters[idx] = _merge_character(bible.characters[idx], c)
        else:
            bible.characters.append(c)

    for r in [*delta.new_relationships, *delta.relationship_updates]:
        src = resolve_character_id(bible, r.source) or r.source
        dst = resolve_character_id(bible, r.target) or r.target
        r = r.model_copy(update={"source": src, "target": dst})
        old = bible.relation(src, dst)
        if old is None:
            bible.relationships.append(r)
            continue
        if old.type != r.type or old.intensity != r.intensity:
            old.history.append(f"第{chapter}章: {old.type}({old.intensity}) → {r.type}({r.intensity})")
        old.type, old.intensity = r.type, r.intensity
        old.description = r.description or old.description
        old.history.extend(h for h in r.history if h not in old.history)

    known_locations = {loc.id for loc in bible.locations}
    for loc in delta.new_locations:
        if loc.id not in known_locations:
            bible.locations.append(loc)
            known_locations.add(loc.id)

    bible.world.rules.extend(r for r in delta.world_rules_added if r not in bible.world.rules)
    closed = {_norm(t) for t in delta.plot_threads_closed}
    bible.plot_threads = [t for t in bible.plot_threads if _norm(t) not in closed]
    bible.plot_threads.extend(t for t in delta.plot_threads_opened if t not in bible.plot_threads)

    # 规范化章节分析里的角色引用
    a = delta.analysis
    a.characters_present = [resolve_character_id(bible, c) or c for c in a.characters_present]
    return bible


def bible_brief(bible: StoryBible) -> str:
    """给 LLM 的故事圣经文本（JSON），用作可缓存的上下文前缀。"""
    return "【故事圣经 Story Bible — 人物、关系、世界观的唯一事实来源】\n" + bible.model_dump_json(indent=1)
