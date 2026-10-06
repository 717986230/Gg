"""按 CanvasSpec 合成画布：面板贴图、漫画分格描边、集数角标、分句字幕层。"""
from __future__ import annotations

import textwrap
from pathlib import Path

from PIL import Image, ImageDraw

from ..canvas import CanvasSpec, Subtitle
from .images import ImageBackend, font


def _hex(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    try:
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return (255, 213, 79)


def render_base(spec: CanvasSpec, backend: ImageBackend, cache_dir: Path, badge: str = "") -> Image.Image:
    canvas = Image.new("RGB", (spec.width, spec.height), (12, 12, 16))
    draw = ImageDraw.Draw(canvas)
    cache_dir.mkdir(parents=True, exist_ok=True)
    for i, p in enumerate(spec.panels):
        cached = cache_dir / f"{spec.shot_id}_p{i}.png"
        if cached.exists():
            img = Image.open(cached).convert("RGB")
        else:
            img = backend.generate(p, p.rect.w, p.rect.h)
            img.save(cached)
        canvas.paste(img.resize((p.rect.w, p.rect.h)), (p.rect.x, p.rect.y))
        if len(spec.panels) > 1:
            # 漫画分格描边；小窗特写额外加主题色边框
            width = 8 if i > 0 and spec.layout == "inset_closeup" else 4
            color = _hex(spec.accent) if spec.layout == "inset_closeup" and i > 0 else (245, 245, 245)
            draw.rectangle([p.rect.x, p.rect.y, p.rect.x + p.rect.w - 1, p.rect.y + p.rect.h - 1],
                           outline=color, width=width)
    if badge:
        f = font(40)
        x, y = 40, 170
        bbox = draw.textbbox((x, y), badge, font=f)
        draw.rounded_rectangle([bbox[0] - 16, bbox[1] - 10, bbox[2] + 16, bbox[3] + 10], radius=14,
                               fill=_hex(spec.accent))
        draw.text((x, y), badge, font=f, fill=(20, 20, 20))
    return canvas


def render_subtitle(spec: CanvasSpec, sub: Subtitle) -> Image.Image:
    """透明字幕层，按台词逐句叠加到视频上。"""
    layer = Image.new("RGBA", (spec.width, spec.height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    box = spec.subtitle_box
    text_font = font(54)
    lines = textwrap.wrap(sub.text, width=16) or [""]
    line_h = 70
    total_h = line_h * len(lines) + (60 if sub.speaker_name else 0)
    y = box.y + box.h - total_h
    draw.rounded_rectangle([box.x - 20, y - 20, box.x + box.w, box.y + box.h + 20], radius=24, fill=(0, 0, 0, 130))
    if sub.speaker_name:
        draw.text((box.x, y), f"【{sub.speaker_name}】", font=font(42), fill=_hex(sub.color) + (255,),
                  stroke_width=3, stroke_fill=(0, 0, 0, 255))
        y += 60
    for ln in lines:
        draw.text((box.x, y), ln, font=text_font, fill=(255, 255, 255, 255), stroke_width=4, stroke_fill=(0, 0, 0, 255))
        y += line_h
    return layer


def render_title_card(width: int, height: int, title: str, subtitle: str, accent: str) -> Image.Image:
    img = Image.new("RGB", (width, height), (10, 10, 14))
    draw = ImageDraw.Draw(img)
    draw.text((width // 2, height // 2 - 80), title, font=font(72), fill=_hex(accent), anchor="mm",
              stroke_width=4, stroke_fill=(0, 0, 0))
    for i, ln in enumerate(textwrap.wrap(subtitle, width=18)[:4]):
        draw.text((width // 2, height // 2 + 30 + i * 64), ln, font=font(44), fill=(230, 230, 230), anchor="mm")
    return img
