"""画布重组引擎。

把一个分镜 Shot 变成画布版式规格（CanvasSpec），画布尺寸和镜头偏好来自风格预设：
- 按 layout 切分面板（全幅 / 双人同框 / 分屏 / 三格漫画 / 大场景 + 特写小窗），竖屏横屏自适应；
- 根据人物关系强度决定双人构图距离与朝向（关系越紧密越靠近，敌对则拉开对峙）；
- 大场景风格（wide_first）下把双人/分屏布局改成全幅大景，人物缩小放进山水里；
- 为每个面板拼装一致性提示词：全剧画风 + 地点锚点 + 角色外貌锚点 + 镜头描述；
- 字幕放在安全区内，避开平台界面遮挡。
渲染层（render/compose.py）只负责照着 CanvasSpec 画图，不做决策。
"""
from __future__ import annotations

import hashlib

from pydantic import BaseModel, Field

from .models import Shot, StoryBible
from .styles import CanvasProfile, StylePreset, get_style

GUTTER = 14

SHOT_TYPE_EN = {
    "大远景": "extreme wide shot", "远景": "wide shot", "全景": "full shot", "中景": "medium shot",
    "近景": "medium close-up", "特写": "close-up", "过肩": "over-the-shoulder shot", "主观": "point-of-view shot",
}
# 各景别下人物高度相对于风格基准的倍数
SHOT_FIGURE_FACTOR = {
    "大远景": 0.75, "远景": 1.0, "全景": 1.5, "中景": 2.4, "近景": 3.2, "特写": 4.0, "过肩": 2.4, "主观": 1.0,
}


class Rect(BaseModel):
    x: int
    y: int
    w: int
    h: int


class Panel(BaseModel):
    rect: Rect
    prompt: str
    negative_prompt: str = ""
    characters: list[str]
    figure_prompts: list[str] = Field(default_factory=list, description="与 characters 一一对应的外貌锚点")
    focus: str = "scene"  # scene | character
    seed: int
    positions: list[float] = Field(default_factory=list, description="人物在画面中的水平位置 0-1")
    figure_scale: float = 0.45  # 人物高度 / 面板高度


class Subtitle(BaseModel):
    speaker: str
    speaker_name: str
    text: str
    color: str


class CanvasSpec(BaseModel):
    shot_id: str
    width: int
    height: int
    layout: str
    panels: list[Panel]
    subtitles: list[Subtitle]
    subtitle_box: Rect
    accent: str
    camera: str
    duration: float
    transition: str
    postprocess: str = ""


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
    if any(k in rtype for k in ("敌", "仇", "对手", "对立")):
        return "two characters facing off, tense confrontation, opposite sides of frame", [0.2, 0.8]
    if intensity >= 5:
        return "two characters very close together, intimate framing, shoulders touching", [0.42, 0.58]
    if intensity >= 4:
        return "two characters standing close, warm interaction, looking at each other", [0.35, 0.65]
    if intensity >= 3:
        return "two characters side by side in conversation", [0.3, 0.7]
    return "two characters at a distance, reserved body language", [0.22, 0.78]


def _group_positions(n: int, seed: int, wide: bool) -> list[float]:
    """大场景中人物不居中排排站：整体偏向画面一侧（三分法），彼此稍有错落。"""
    if n == 0:
        return []
    if not wide:
        return [(i + 1) / (n + 1) for i in range(n)]
    center = 0.33 if seed % 2 == 0 else 0.67
    spread = min(0.06 * n, 0.4)
    start = center - spread / 2
    step = spread / max(1, n - 1)
    return [round(start + i * step, 3) for i in range(n)]


def _compose_prompt(bible: StoryBible, style: StylePreset, shot: Shot, chars: list[str], extra: str,
                    shot_type: str | None = None) -> str:
    loc = bible.location(shot.location)
    parts = [
        bible.world.art_style or style.art_style,
        "vertical 9:16 composition" if not style.canvas.landscape else "horizontal 16:9 composition",
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


def _choose_layout(shot: Shot, bible: StoryBible, style: StylePreset) -> str:
    """模型给的 layout 不合理或与风格冲突时做纠正。"""
    n = len(shot.characters)
    layout = shot.layout
    if style.wide_first and layout in ("two_shot", "split_vertical", "split_horizontal", "single", "inset_closeup"):
        return "full_bleed"
    if layout in ("two_shot", "split_vertical", "split_horizontal") and n < 2:
        return "single" if n == 1 else "full_bleed"
    if layout == "single" and n >= 2:
        r = bible.relation(shot.characters[0], shot.characters[1])
        if r and r.intensity >= 4:
            return "two_shot"
    return layout


def build_canvas(shot: Shot, bible: StoryBible, style: StylePreset | str | None = None) -> CanvasSpec:
    style = style if isinstance(style, StylePreset) else get_style(style)
    cv: CanvasProfile = style.canvas
    W, H = cv.width, cv.height
    layout = _choose_layout(shot, bible, style)
    full = Rect(x=0, y=0, w=W, h=H)
    chars = shot.characters
    panels: list[Panel] = []
    seed = stable_seed(bible.title, shot.id)
    base_scale = style.figure_scale * SHOT_FIGURE_FACTOR.get(shot.shot_type, 1.0)

    def panel(rect: Rect, cs: list[str], extra: str = "", focus: str = "scene", shot_type: str | None = None,
              positions: list[float] | None = None, salt: str = "", scale: float | None = None) -> Panel:
        # 角色单人特写用角色 id 作为种子，保持同一角色多次特写的长相稳定
        pseed = (stable_seed(bible.title, cs[0]) if focus == "character" and len(cs) == 1
                 else stable_seed(str(seed), salt))
        figures = []
        for c in cs:
            ch = bible.character(c)
            figures.append(ch.visual_prompt if ch else c)
        return Panel(
            rect=rect, prompt=_compose_prompt(bible, style, shot, cs, extra, shot_type),
            negative_prompt=style.negative, characters=cs, figure_prompts=figures, focus=focus, seed=pseed,
            positions=positions if positions is not None else _group_positions(len(cs), pseed, style.wide_first),
            figure_scale=min(0.9, scale if scale is not None else base_scale),
        )

    if layout == "two_shot" and len(chars) >= 2:
        staging, pos = _pair_staging(bible, chars[0], chars[1])
        panels.append(panel(full, chars[:2], staging, positions=pos))
    elif layout in ("split_vertical", "split_horizontal") and len(chars) >= 2:
        # 竖屏上下分、横屏左右分，保证每格都接近原画幅比例
        vertical = layout == "split_vertical" and not cv.landscape
        a, b = _split(full, 2, vertical=vertical)
        staging, _ = _pair_staging(bible, chars[0], chars[1])
        close = style.figure_scale * SHOT_FIGURE_FACTOR["近景"]
        panels.append(panel(a, [chars[0]], f"{staging}, this panel shows only one side", "character", "近景",
                            salt="a", scale=close))
        panels.append(panel(b, [chars[1]], f"{staging}, reverse angle", "character", "近景", salt="b", scale=close))
    elif layout == "manga_3":
        if cv.landscape:
            main = Rect(x=0, y=0, w=int(W * 0.62), h=H)
            side = Rect(x=main.w + GUTTER, y=0, w=W - main.w - GUTTER, h=H)
            s1, s2 = _split(side, 2, vertical=True)
        else:
            main = Rect(x=0, y=0, w=W, h=int(H * 0.42))
            rest = Rect(x=0, y=main.h + GUTTER, w=W, h=H - main.h - GUTTER)
            s1, s2 = _split(rest, 2, vertical=False)
        detail = style.figure_scale * SHOT_FIGURE_FACTOR["全景"] * (1 if style.wide_first else 2.5)
        panels.append(panel(main, chars, "establishing moment", shot_type="全景" if not style.wide_first else None,
                            salt="1"))
        panels.append(panel(s1, chars[:1], "reaction", "character", "全景" if style.wide_first else "特写",
                            salt="2", scale=detail))
        panels.append(panel(s2, chars[1:2] or chars[:1], "detail of the action", "character",
                            "全景" if style.wide_first else "特写", salt="3", scale=detail))
    elif layout == "inset_closeup" and chars:
        panels.append(panel(full, chars, salt="bg"))
        iw, ih = (int(W * 0.24), int(H * 0.36)) if cv.landscape else (380, 480)
        inset = Rect(x=W - cv.safe_right - iw, y=cv.safe_top + 40, w=iw, h=ih)
        panels.append(panel(inset, chars[:1], "emotional facial expression", "character", "特写", salt="inset",
                            scale=0.75))
    else:
        focus = ("character" if layout == "single" and len(chars) == 1 and shot.shot_type in ("近景", "特写")
                 else "scene")
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
    if cv.landscape:
        # 横屏：贴底的窄字幕条，画面主体（人物站位线 ≤ 0.8H）不被遮挡
        bw = int(W * 0.76)
        bh = int(H * 0.13)
        box = Rect(x=(W - bw) // 2, y=H - int(H * 0.025) - bh, w=bw, h=bh)
    else:
        box = Rect(x=cv.safe_left, y=H - cv.safe_bottom - 260, w=W - cv.safe_left - cv.safe_right, h=240)
    return CanvasSpec(
        shot_id=shot.id, width=W, height=H, layout=layout, panels=panels, subtitles=subtitles, subtitle_box=box,
        accent=accent, camera=shot.camera, duration=shot.duration, transition=shot.transition,
        postprocess=style.postprocess,
    )
