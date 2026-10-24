"""按 CanvasSpec 合成画布：面板贴图、风格化后期、漫画分格描边、集数角标、分句字幕层。

所有尺寸按画布短边缩放，竖屏 1080x1920 与横屏 1280x720 / 1920x1080 共用一套代码。
"""
from __future__ import annotations

import textwrap
from pathlib import Path

from PIL import Image, ImageDraw

from ..canvas import CanvasSpec, Subtitle, stable_seed
from .images import ImageBackend, font
from .stylize import apply_postprocess


def _hex(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    try:
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return (255, 213, 79)


def _u(spec_w: int, spec_h: int) -> float:
    return min(spec_w, spec_h) / 1080


def render_badge(spec: CanvasSpec, badge: str) -> Image.Image:
    """集数角标做成透明叠加层，在运镜之后叠加，不会被推拉镜头裁掉。"""
    u = _u(spec.width, spec.height)
    layer = Image.new("RGBA", (spec.width, spec.height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    f = font(int(40 * u))
    landscape = spec.width > spec.height
    x, y = (int(36 * u), int(30 * u)) if landscape else (40, 170)
    bbox = draw.textbbox((x, y), badge, font=f)
    pad = int(14 * u)
    draw.rounded_rectangle([bbox[0] - pad, bbox[1] - pad * 0.7, bbox[2] + pad, bbox[3] + pad * 0.7],
                           radius=int(14 * u), fill=_hex(spec.accent) + (230,))
    draw.text((x, y), badge, font=f, fill=(20, 20, 20, 255))
    return layer


def render_base(spec: CanvasSpec, backend: ImageBackend, cache_dir: Path, badge: str = "") -> Image.Image:
    u = _u(spec.width, spec.height)
    canvas = Image.new("RGB", (spec.width, spec.height), (12, 12, 16))
    draw = ImageDraw.Draw(canvas)
    cache_dir.mkdir(parents=True, exist_ok=True)
    for i, p in enumerate(spec.panels):
        cached = cache_dir / f"{spec.shot_id}_p{i}.png"
        if cached.exists():
            img = Image.open(cached).convert("RGB")
        else:
            img = backend.generate(p, p.rect.w, p.rect.h)
            # 风格化后期对每个面板单独做，分格边线保持干净
            img = apply_postprocess(img.resize((p.rect.w, p.rect.h)), spec.postprocess, seed=stable_seed(spec.shot_id, str(i)))
            img.save(cached)
        canvas.paste(img.resize((p.rect.w, p.rect.h)), (p.rect.x, p.rect.y))
        if len(spec.panels) > 1:
            # 漫画分格描边；小窗特写额外加主题色边框
            width = max(2, int((8 if i > 0 and spec.layout == "inset_closeup" else 4) * u))
            color = _hex(spec.accent) if spec.layout == "inset_closeup" and i > 0 else (245, 240, 228)
            draw.rectangle([p.rect.x, p.rect.y, p.rect.x + p.rect.w - 1, p.rect.y + p.rect.h - 1],
                           outline=color, width=width)
    if badge:
        canvas.paste(render_badge(spec, badge), (0, 0), render_badge(spec, badge))
    return canvas


def render_subtitle(spec: CanvasSpec, sub: Subtitle) -> Image.Image:
    """透明字幕层，按台词逐句叠加到视频上。"""
    u = _u(spec.width, spec.height)
    layer = Image.new("RGBA", (spec.width, spec.height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    box = spec.subtitle_box
    landscape = spec.width > spec.height
    size = int((46 if landscape else 54) * u)
    text_font = font(size)
    chars_per_line = max(8, int(box.w / (size * 1.02)))
    lines = textwrap.wrap(sub.text, width=chars_per_line) or [""]
    line_h = int(size * 1.3)
    name_h = int(size * 1.1) if sub.speaker_name else 0
    total_h = line_h * len(lines) + name_h
    y = box.y + box.h - total_h
    stroke = max(2, int(4 * u))
    if landscape:
        # 横屏：居中字幕 + 半透明底条，不遮挡大场景
        draw.rounded_rectangle([box.x, y - 10 * u, box.x + box.w, box.y + box.h + 6 * u], radius=int(16 * u),
                               fill=(20, 16, 14, 110))
        cx = box.x + box.w // 2
        if sub.speaker_name:
            draw.text((cx, y), f"【{sub.speaker_name}】", font=font(int(size * 0.8)), fill=_hex(sub.color) + (255,),
                      stroke_width=stroke, stroke_fill=(0, 0, 0, 255), anchor="ma")
            y += name_h
        for ln in lines:
            draw.text((cx, y), ln, font=text_font, fill=(255, 255, 255, 255), stroke_width=stroke,
                      stroke_fill=(0, 0, 0, 255), anchor="ma")
            y += line_h
        return layer
    draw.rounded_rectangle([box.x - 20, y - 20, box.x + box.w, box.y + box.h + 20], radius=24, fill=(0, 0, 0, 130))
    if sub.speaker_name:
        draw.text((box.x, y), f"【{sub.speaker_name}】", font=font(int(size * 0.78)), fill=_hex(sub.color) + (255,),
                  stroke_width=3, stroke_fill=(0, 0, 0, 255))
        y += name_h
    for ln in lines:
        draw.text((box.x, y), ln, font=text_font, fill=(255, 255, 255, 255), stroke_width=stroke,
                  stroke_fill=(0, 0, 0, 255))
        y += line_h
    return layer


def render_title_card(width: int, height: int, title: str, subtitle: str, accent: str,
                      background: Image.Image | None = None) -> Image.Image:
    u = _u(width, height)
    if background is not None:
        img = background.resize((width, height)).convert("RGB")
        img = Image.blend(img, Image.new("RGB", (width, height), (16, 14, 12)), 0.55)
    else:
        img = Image.new("RGB", (width, height), (10, 10, 14))
    draw = ImageDraw.Draw(img)
    draw.text((width // 2, height // 2 - int(80 * u)), title, font=font(int(72 * u)), fill=_hex(accent), anchor="mm",
              stroke_width=max(2, int(4 * u)), stroke_fill=(0, 0, 0))
    per_line = 24 if width > height else 18
    for i, ln in enumerate(textwrap.wrap(subtitle, width=per_line)[:4]):
        draw.text((width // 2, height // 2 + int((30 + i * 64) * u)), ln, font=font(int(44 * u)), fill=(236, 232, 222),
                  anchor="mm")
    return img


def render_cover(base: Image.Image, title: str, accent: str) -> Image.Image:
    w, h = base.size
    u = _u(w, h)
    img = base.copy()
    draw = ImageDraw.Draw(img, "RGBA")
    band_h = int(h * (0.22 if w > h else 0.17))
    y0 = int(h * (0.62 if w > h else 0.61))
    draw.rectangle([0, y0, w, y0 + band_h], fill=(0, 0, 0, 170))
    draw.text((w // 2, y0 + band_h // 2), title[:16], font=font(int(84 * u)), fill=_hex(accent), anchor="mm",
              stroke_width=max(2, int(5 * u)), stroke_fill=(0, 0, 0))
    return img
