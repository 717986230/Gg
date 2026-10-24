"""图像生成后端。

- placeholder：离线占位图（场景色块 + 角色剪影 + 镜头描述），无需 GPU，用于联调和预览分镜节奏；
- scenic：程序化水彩场景（山水/宫殿/道观/大殿 + 小人物），无需 GPU，适合大场景风格；
- sdwebui：调用 Stable Diffusion WebUI / Forge 的 /sdapi/v1/txt2img，固定种子保证角色一致。
新增后端只需实现 ImageBackend.generate。
"""
from __future__ import annotations

import base64
import io
import os
import textwrap
from functools import lru_cache
from typing import Protocol

import requests
from PIL import Image, ImageDraw, ImageFont

from ..canvas import Panel, stable_seed

FONT_CANDIDATES = [
    os.environ.get("NOVEL2DRAMA_FONT", ""),
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "C:/Windows/Fonts/msyh.ttc",
]


@lru_cache(maxsize=16)
def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in FONT_CANDIDATES:
        if path and os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


class ImageBackend(Protocol):
    def generate(self, panel: Panel, width: int, height: int) -> Image.Image: ...


def _color(seed: int, offset: int = 0) -> tuple[int, int, int]:
    s = (seed >> offset) & 0xFFFFFF
    return (40 + (s & 0x7F), 40 + ((s >> 8) & 0x7F), 60 + ((s >> 16) & 0x7F))


class PlaceholderBackend:
    def generate(self, panel: Panel, width: int, height: int) -> Image.Image:
        top, bottom = _color(panel.seed), _color(panel.seed, 7)
        img = Image.new("RGB", (width, height))
        draw = ImageDraw.Draw(img)
        for y in range(height):
            t = y / max(1, height - 1)
            draw.line([(0, y), (width, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)))
        n = len(panel.characters)
        positions = panel.positions or [(i + 1) / (n + 1) for i in range(n)]
        scale = 0.75 if panel.focus == "character" else 0.45
        for i, cid in enumerate(panel.characters[: len(positions)]):
            cx = int(positions[i] * width)
            ph = int(height * scale)
            pw = int(ph * 0.32)
            base = height - int(height * 0.08)
            col = _color(stable_seed(cid))
            draw.ellipse([cx - pw // 2.6, base - ph, cx + pw // 2.6, base - ph + pw // 1.3], fill=col)
            draw.rounded_rectangle([cx - pw // 2, base - ph + pw // 1.4, cx + pw // 2, base], radius=pw // 4, fill=col)
            draw.text((cx, base + 8), cid, font=font(max(18, width // 30)), fill="white", anchor="mt")
        caption = textwrap.fill(panel.prompt[:300], width=max(20, width // 22))
        draw.multiline_text((24, int(height * 0.14)), caption, font=font(max(14, width // 48)), fill=(235, 235, 235))
        return img


class SDWebUIBackend:
    def __init__(self, url: str | None = None, steps: int = 28, timeout: int = 600):
        self.url = (url or os.environ.get("SDWEBUI_URL", "http://127.0.0.1:7860")).rstrip("/")
        self.steps = steps
        self.timeout = timeout

    def generate(self, panel: Panel, width: int, height: int) -> Image.Image:
        # SD 需要 8 的倍数，生成后再缩放到面板尺寸
        gw, gh = _sd_size(width, height)
        resp = requests.post(
            f"{self.url}/sdapi/v1/txt2img",
            json={
                "prompt": panel.prompt,
                "negative_prompt": panel.negative_prompt,
                "seed": panel.seed,
                "steps": self.steps,
                "width": gw,
                "height": gh,
                "cfg_scale": 6.5,
                "sampler_name": "DPM++ 2M",
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = base64.b64decode(resp.json()["images"][0])
        return Image.open(io.BytesIO(data)).convert("RGB").resize((width, height), Image.LANCZOS)


def _sd_size(width: int, height: int, long_side: int = 1216) -> tuple[int, int]:
    ratio = width / height
    if ratio < 1:
        h = long_side
        w = int(h * ratio)
    else:
        w = long_side
        h = int(w / ratio)
    return max(64, w // 8 * 8), max(64, h // 8 * 8)


def get_backend(name: str | None = None) -> ImageBackend:
    name = name or os.environ.get("NOVEL2DRAMA_IMAGE_BACKEND", "placeholder")
    if name == "sdwebui":
        return SDWebUIBackend()
    if name == "placeholder":
        return PlaceholderBackend()
    if name == "scenic":
        from .scenic import ScenicBackend

        return ScenicBackend()
    raise ValueError(f"未知图像后端: {name}")
