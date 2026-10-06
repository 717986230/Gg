"""画布重组引擎。

把一个分镜 Shot 变成 9:16 竖屏画布的版式规格（CanvasSpec）：
- 按 layout 切分面板（单幅 / 上下分屏 / 左右分屏 / 三格漫画 / 大场景 + 特写小窗）；
- 根据人物关系强度决定双人构图距离与朝向（关系越紧密越靠近）；
- 为每个面板拼装一致性提示词：全剧画风 + 地点锚点 + 角色外貌锚点 + 镜头描述；
- 避开抖音界面遮挡的安全区放置字幕。
渲染层（render/compose.py）只负责照着 CanvasSpec 画图，不做决策。
"""
from __future__ import annotations

import hashlib

from pydantic import BaseModel

from .models import Shot, StoryBible

W, H = 1080, 1920
# 抖音 UI 遮挡：顶部状态栏/搜索，底部标题文案+进度条，右侧点赞评论栏
SAFE_TOP, SAFE_BOTTOM, SAFE_RIGHT = 150, 420, 140
GUTTER = 14

SHOT_TYPE_EN = {
    "大远景": "extreme wide shot", "远景": "wide shot", "全景": "full shot", "中景": "medium shot",
    "近景": "medium close-up", "特写": "close-up", "过肩": "over-the-shoulder shot", "主观": "point-of-view shot",
}
NEGATIVE = "lowres, bad anatomy, extra fingers, deformed face, text, watermark, logo, blurry, inconsistent character"


class Rect(BaseModel):
    x: int
    y: int
    w: int
    h: int


class Panel(BaseModel):
    rect: Rect
    prompt: str
    negative_prompt: str = NEGATIVE
    characters: list[str]
    focus: str = "scene"  # scene | character
    seed: int
    # 双人构图：两人在画面中的水平位置（0-1），用于占位渲染和 ControlNet 等扩展
    positions: list[float] = []


class Subtitle(BaseModel):
    speaker: str
    speaker_name: str
    text: str
    color: str


class CanvasSpec(BaseModel):
    shot_id: str
    width: int = W
    height: int = H
    layout: str
    panels: list[Panel]
    subtitles: list[Subtitle]
    subtitle_box: Rect
    accent: str
    camera: str
    duration: float
    transition: str


def stable_seed(*keys: str) -> int:
    return int(hashlib.sha1("|".join(keys).encode()).hexdigest()[:8], 16)


def _char_prompt(bible: StoryBible, cid: str) -> str:
    c = bible.character(cid)
    return f"({c.name}: {c.visual_prompt})" if c else ""


def _pair_staging(bible: StoryBible, a: str, b: str) -> tuple[str, list[float]]:
    """关系强度 → 构图距离/朝向。"""
    r = bible.relation(a, b)
    intensity = r.intensity if r else 2
    rtype = r.type if r else ""
    hostile = any(k in rtype for k in ("敌", "仇", "对手", "对立"))
    if hostile:
        return "two characters facing off, tense confrontation, opposite sides of frame", [0.2, 0.8]
    if intensity >= 5:
        return "two characters very close together, intimate framing, shoulders touching", [0.42, 0.58]
    if intensity >= 4:
        return "two characters standing close, warm interaction, looking at each other", [0.35, 0.65]
    if intensity >= 3:
        return "two characters side by side in conversation", [0.3, 0.7]
    return "two characters at a distance, reserved body language", [0.22, 0.78]


def _compose_prompt(bible: StoryBible, shot: Shot, chars: list[str], extra: str, shot_type: str | None = None) -> str:
    loc = bible.location(shot.location)
    parts = [
        bible.world.art_style,
        "vertical 9:16 composition",
        SHOT_TYPE_EN.get(shot_type or shot.shot_type, "medium shot"),
        extra,
        shot.image_prompt,
        *[_char_prompt(bible, c) for c in chars],
        loc.visual_prompt if loc else "",
        "masterpiece, best quality, consistent character design",
    ]
    return ", ".join(p for p in parts if p)


def _split(rect: Rect, n: int, vertical: bool) -> list[Rect]:
    rects = []
    if vertical:
        h = (rect.h - GUTTER * (n - 1)) // n
        for i in range(n):
            rects.append(Rect(x=rect.x, y=rect.y + i * (h + GUTTER), w=rect.w, h=h))
    else:
        w = (rect.w - GUTTER * (n - 1)) // n
        for i in range(n):
            rects.append(Rect(x=rect.x + i * (w + GUTTER), y=rect.y, w=w, h=rect.h))
    return rects


def _choose_layout(shot: Shot, bible: StoryBible) -> str:
    """模型给的 layout 不合理时做纠正（如 two_shot 但只有一个角色）。"""
    n = len(shot.characters)
    layout = shot.layout
    if layout in ("two_shot", "split_vertical", "split_horizontal") and n < 2:
        return "single" if n == 1 else "full_bleed"
    if layout == "single" and n >= 2:
        r = bible.relation(shot.characters[0], shot.characters[1])
        if r and r.intensity >= 4:
            return "two_shot"
    return layout


def build_canvas(shot: Shot, bible: StoryBible) -> CanvasSpec:
    layout = _choose_layout(shot, bible)
    full = Rect(x=0, y=0, w=W, h=H)
    chars = shot.characters
    panels: list[Panel] = []
    seed = stable_seed(bible.title, shot.id)

    def panel(rect: Rect, cs: list[str], extra: str = "", focus: str = "scene",
              shot_type: str | None = None, positions: list[float] | None = None, salt: str = "") -> Panel:
        # 角色单人特写用角色 id 作为种子，保持同一角色多次特写的长相稳定
        pseed = stable_seed(bible.title, cs[0]) if focus == "character" and len(cs) == 1 else stable_seed(str(seed), salt)
        return Panel(rect=rect, prompt=_compose_prompt(bible, shot, cs, extra, shot_type),
                     characters=cs, focus=focus, seed=pseed, positions=positions or [])

    if layout == "two_shot" and len(chars) >= 2:
        staging, pos = _pair_staging(bible, chars[0], chars[1])
        panels.append(panel(full, chars[:2], staging, positions=pos))
    elif layout == "split_vertical" and len(chars) >= 2:
        top, bottom = _split(full, 2, vertical=True)
        staging, _ = _pair_staging(bible, chars[0], chars[1])
        panels.append(panel(top, [chars[0]], f"{staging}, this panel shows only one side", "character", "近景", salt="a"))
        panels.append(panel(bottom, [chars[1]], f"{staging}, reverse angle", "character", "近景", salt="b"))
    elif layout == "split_horizontal" and len(chars) >= 2:
        left, right = _split(full, 2, vertical=False)
        panels.append(panel(left, [chars[0]], "facing right", "character", "近景", salt="a"))
        panels.append(panel(right, [chars[1]], "facing left", "character", "近景", salt="b"))
    elif layout == "manga_3":
        top = Rect(x=0, y=0, w=W, h=int(H * 0.42))
        rest = Rect(x=0, y=top.h + GUTTER, w=W, h=H - top.h - GUTTER)
        left, right = _split(rest, 2, vertical=False)
        panels.append(panel(top, chars, "establishing moment", shot_type="全景", salt="1"))
        panels.append(panel(left, chars[:1], "reaction", "character", "特写", salt="2"))
        panels.append(panel(right, chars[1:2] or chars[:1], "detail of the action", "character", "特写", salt="3"))
    elif layout == "inset_closeup" and chars:
        panels.append(panel(full, chars, salt="bg"))
        inset = Rect(x=W - SAFE_RIGHT - 380, y=SAFE_TOP + 40, w=380, h=480)
        panels.append(panel(inset, chars[:1], "emotional facial expression", "character", "特写", salt="inset"))
    else:
        focus = "character" if layout == "single" and len(chars) == 1 and shot.shot_type in ("近景", "特写") else "scene"
        panels.append(panel(full, chars, focus=focus))

    accent = bible.world.palette[0] if bible.world.palette else "#FFD54F"
    subtitles = []
    for line in shot.dialogue:
        c = bible.character(line.speaker)
        subtitles.append(Subtitle(
            speaker=line.speaker,
            speaker_name="" if line.speaker == "narrator" else (c.name if c else line.speaker),
            text=line.text,
            color="#FFFFFF" if line.speaker == "narrator" else accent,
        ))
    box = Rect(x=60, y=H - SAFE_BOTTOM - 260, w=W - 60 - SAFE_RIGHT, h=240)
    return CanvasSpec(
        shot_id=shot.id, layout=layout, panels=panels, subtitles=subtitles, subtitle_box=box,
        accent=accent, camera=shot.camera, duration=shot.duration, transition=shot.transition,
    )
