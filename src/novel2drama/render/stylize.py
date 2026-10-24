"""后期风格化滤镜：把任意画面（程序化场景或 SD 出图）统一成"水彩 + 墨线"的手绘质感。

步骤：色块柔化 → 水彩晕染（低频颜料浓淡 + 边缘积色）→ 提取墨线（带轻微抖动）→ 宣纸纹理。
对全剧每一帧使用同一个滤镜，是保持画风统一的关键；随机性由 seed 决定，同一镜头重渲染结果不变。
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter

PAPER = np.array([244, 237, 222], dtype=np.float32) / 255.0


def _noise(h: int, w: int, scale: int, rng: np.random.Generator) -> np.ndarray:
    """低频噪声：小尺寸随机图双线性放大后再模糊，0-1。"""
    small = rng.random((max(2, h // scale), max(2, w // scale))).astype(np.float32)
    img = Image.fromarray((small * 255).astype(np.uint8)).resize((w, h), Image.BILINEAR)
    img = img.filter(ImageFilter.GaussianBlur(scale / 3))
    return np.asarray(img, dtype=np.float32) / 255.0


def paper_texture(w: int, h: int, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    grain = rng.normal(0, 0.018, (h, w)).astype(np.float32)
    fibers = (_noise(h, w, 6, rng) - 0.5) * 0.05 + (_noise(h, w, 60, rng) - 0.5) * 0.06
    return PAPER[None, None, :] + (grain + fibers)[..., None]


def watercolor_line(img: Image.Image, seed: int = 0, line_strength: float = 0.85) -> Image.Image:
    img = img.convert("RGB")
    w, h = img.size
    rng = np.random.default_rng(seed)

    # 1) 色块柔化：中值 + 轻模糊，去掉细碎纹理，留下"涂色块"
    soft = img.filter(ImageFilter.MedianFilter(5)).filter(ImageFilter.GaussianBlur(1.2))
    color = np.asarray(soft, dtype=np.float32) / 255.0

    # 2) 水彩晕染：颜料浓淡不均 + 整体提亮透出纸色
    bloom = _noise(h, w, 90, rng) * 0.6 + _noise(h, w, 25, rng) * 0.4
    pigment = 0.72 + 0.38 * bloom                       # 颜料浓度
    paper = paper_texture(w, h, seed + 1)
    wash = paper * (1 - pigment[..., None]) + color * pigment[..., None]
    # 透明叠色：色彩越深越像多层罩染
    wash = 1 - (1 - wash) * (0.88 + 0.12 * bloom[..., None])

    # 边缘积色：色块边缘颜料沉积更深（湿画法特征）
    lum = np.asarray(soft.convert("L"), dtype=np.float32) / 255.0
    blur_big = np.asarray(soft.convert("L").filter(ImageFilter.GaussianBlur(6)), dtype=np.float32) / 255.0
    pooling = np.clip((blur_big - lum) * 2.2, 0, 0.35)
    wash = wash * (1 - pooling[..., None])

    # 3) 墨线：灰度边缘检测，轻微位移模拟手绘抖动
    edges_src = img.convert("L").filter(ImageFilter.GaussianBlur(0.8))
    edges = np.asarray(edges_src.filter(ImageFilter.FIND_EDGES), dtype=np.float32) / 255.0
    edges = np.clip((edges - 0.04) * 5.0, 0, 1)
    jitter = _noise(h, w, 14, rng)
    edges = edges * (0.55 + 0.45 * jitter)               # 线条有轻有重，断续自然
    line_img = Image.fromarray((edges * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(3))
    line_img = line_img.filter(ImageFilter.GaussianBlur(0.6))
    lines = np.asarray(line_img, dtype=np.float32) / 255.0 * line_strength
    ink = np.array([0.16, 0.13, 0.12], dtype=np.float32)
    out = wash * (1 - lines[..., None]) + ink[None, None, :] * lines[..., None]

    # 高光保留：金光、星火、月亮、白色封条等亮部不被墨线和纸色压暗
    orig = np.asarray(img, dtype=np.float32) / 255.0
    lum_o = orig.mean(axis=2)
    warm = np.clip(orig[..., 0] - orig[..., 2], 0, 1)
    hl = np.clip((lum_o - 0.7) / 0.18, 0, 1) * np.clip(0.6 + warm * 2, 0, 1)
    hl = np.asarray(Image.fromarray((hl * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.5)),
                    dtype=np.float32) / 255.0
    out = out * (1 - hl[..., None]) + np.maximum(out, orig) * hl[..., None]

    # 4) 纸张暗角
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    vignette = 1 - 0.12 * (((xx / w - 0.5) ** 2 + (yy / h - 0.5) ** 2) * 2.2)
    out = out * vignette[..., None]
    return Image.fromarray((np.clip(out, 0, 1) * 255).astype(np.uint8))


FILTERS = {"watercolor_line": watercolor_line}


def apply_postprocess(img: Image.Image, name: str, seed: int = 0) -> Image.Image:
    if not name:
        return img
    if name not in FILTERS:
        raise ValueError(f"未知后期滤镜 {name}")
    return FILTERS[name](img, seed=seed)
