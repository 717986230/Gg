"""程序化水彩场景绘制后端（scenic）——无需 GPU 就能出"大场景、人物小"的国风画面。

根据面板提示词里的关键词搭建场景：
  场景：palace 宫殿 / throne 金殿 / temple 道观 / mountain 群山 / path 山路 / road 长路 / river 江河 /
        hall 大殿外景 / interior 殿内 / sky 天空
  时间天气：dusk 黄昏 / night 夜 / mist 雾 / rain 雨 / plague 瘟疫阴霾
  特效：black smoke 黑气 / golden light 金光 / stars 星火 / wind 狂风 / tablet 石碑 / pit 深穴 / seal 封条
人物按外貌锚点（visual_prompt）解析为拟人动物：goose 鹅、crane 鹤、tortoise 龟、lion 狮、tiger 虎、ox 牛……
画面再经 stylize.watercolor_line 统一成水彩墨线质感。
接入 SD 等真实绘图模型后可以替换本后端，镜头/构图/字幕/滤镜流程不变。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from ..canvas import Panel
from .images import font

SS = 2  # 超采样倍数，绘制后缩小抗锯齿


def _has(text: str, *words: str) -> bool:
    """英文单词按词边界匹配（避免 winding→wind、pitch→pit），中文按子串匹配。"""
    for w in words:
        if w.isascii():
            if re.search(rf"(?<![a-z]){re.escape(w)}", text):
                return True
        elif w in text:
            return True
    return False


# ------------------------------------------------------------------ 场景解析
@dataclass
class Scene:
    setting: str = "mountain"
    time: str = "day"
    weather: set[str] = field(default_factory=set)
    effects: set[str] = field(default_factory=set)
    tablet_text: str = ""
    plaque_text: str = ""
    crowd: str = ""
    lit: bool = False  # 房子窗里有灯光


def parse_scene(prompt: str) -> Scene:
    p = prompt.lower()
    s = Scene()
    # 镜头描述（image_prompt）优先于地点锚点，所以按"更具体"的场景优先匹配
    if _has(p, "interior", "inside the", "殿内"):
        s.setting = "interior"
    elif _has(p, "throne", "金殿", "audience hall"):
        s.setting = "throne"
    elif _has(p, "sealed hall", "demon hall", "伏魔", "paper seals"):
        s.setting = "sealed_hall"
    elif _has(p, "mountain path", "winding path", "stone steps up", "山路"):
        s.setting = "path"
    elif _has(p, "temple", "taoist monastery", "道观", "上清宫"):
        s.setting = "temple"
    elif _has(p, "palace", "capital city", "宫城", "京城"):
        s.setting = "palace"
    elif _has(p, "robbers' house", "cottage", "lit window", "小屋"):
        s.setting = "cottage"
    elif _has(p, "farmyard", "village", "farm", "村庄", "农庄"):
        s.setting = "village"
    elif _has(p, "forest", "woods", "森林"):
        s.setting = "forest"
    elif _has(p, "country road", "meadow", "草地"):
        s.setting = "meadow"
    elif _has(p, "road", "procession", "官道"):
        s.setting = "road"
    elif _has(p, "night sky", "sky full", "漫天"):
        s.setting = "sky"
    elif _has(p, "river", "江"):
        s.setting = "river"
    if _has(p, "night", "夜"):
        s.time = "night"
    elif _has(p, "dusk", "sunset", "黄昏", "evening"):
        s.time = "dusk"
    for key, words in {
        "mist": ("mist", "fog", "cloud sea", "云雾", "雾"),
        "rain": ("rain ", "rainy", "rainfall", "雨"),
        "plague": ("plague", "pestilence", "瘟疫", "gloomy"),
        "wind": ("strong wind", "gale", "gust", "狂风"),
    }.items():
        if _has(p, *words):
            s.weather.add(key)
    for key, words in {
        "smoke": ("black smoke", "black qi", "黑气", "dark smoke"),
        "lights": ("golden light", "streaks of light", "金光", "golden streaks"),
        "stars": ("stars", "shooting", "流星", "魔星"),
        "tablet": ("tablet", "stele", "石碑"),
        "pit": ("pit ", "pit,", "abyss", "dug up", "深穴"),
        "seals": ("seal", "封条"),
        "torches": ("torch", "火把", "lantern"),
        "pines": ("pine", "松"),
        "bell": ("bell", "钟"),
    }.items():
        if _has(p, *words):
            s.effects.add(key)
    if s.setting in ("path", "temple", "mountain"):
        s.effects.add("pines")
    s.lit = _has(p, "lit window", "窗里有灯")
    m = re.search(r"「(.+?)」", prompt)
    s.tablet_text = m.group(1) if m else ""
    m = re.search(r"『(.+?)』", prompt)
    s.plaque_text = m.group(1) if m else ""
    if _has(p, "robber band", "fleeing robbers", "强盗们"):
        s.crowd = "robbers"
    elif _has(p, "procession", "retinue", "followers", "随从"):
        s.crowd = "retinue"
    elif _has(p, "priests", "道众", "taoists"):
        s.crowd = "priests"
    elif _has(p, "officials", "courtiers", "百官"):
        s.crowd = "courtiers"
    elif _has(p, "workers", "laborers", "众人"):
        s.crowd = "workers"
    return s


# ------------------------------------------------------------------ 人物解析
SPECIES = {
    # name: (头色, 特征色, 类型)
    "goose": ((250, 248, 240), (238, 140, 40), "bird"),
    "crane": ((250, 250, 250), (200, 40, 40), "crane"),
    "tortoise": ((120, 140, 100), (90, 110, 70), "tortoise"),
    "turtle": ((120, 140, 100), (90, 110, 70), "tortoise"),
    "lion": ((225, 190, 110), (190, 120, 50), "lion"),
    "tiger": ((235, 150, 60), (40, 30, 30), "tiger"),
    "leopard": ((230, 180, 90), (60, 40, 30), "tiger"),
    "ox": ((200, 150, 80), (240, 235, 220), "ox"),
    "buffalo": ((110, 110, 110), (240, 235, 220), "ox"),
    "bear": ((90, 65, 50), (60, 40, 30), "bear"),
    "rabbit": ((205, 205, 200), (240, 190, 190), "rabbit"),
    "dog": ((170, 130, 90), (120, 85, 60), "dog"),
    "donkey": ((150, 145, 140), (235, 230, 222), "donkey"),
    "cat": ((95, 90, 88), (240, 236, 228), "cat"),
    "fox": ((220, 120, 50), (250, 245, 235), "fox"),
    "monkey": ((150, 110, 80), (230, 190, 160), "monkey"),
    "deer": ((190, 140, 90), (240, 230, 210), "deer"),
    "rat": ((150, 150, 150), (230, 170, 170), "rabbit"),
    "rooster": ((240, 235, 225), (210, 40, 40), "bird"),
    "peacock": ((40, 110, 140), (60, 160, 120), "bird"),
    "serpent": ((240, 240, 235), (200, 60, 60), "serpent"),
    "snake": ((240, 240, 235), (200, 60, 60), "serpent"),
}
COLORS = {
    "crimson": (170, 40, 45), "red": (185, 55, 50), "scarlet": (195, 50, 40), "yellow": (220, 175, 60),
    "golden": (215, 165, 55), "gold": (215, 165, 55), "blue": (60, 95, 150), "azure": (80, 140, 180),
    "green": (70, 120, 80), "teal": (50, 120, 115), "white": (235, 232, 222), "black": (45, 42, 45),
    "gray": (130, 128, 125), "grey": (130, 128, 125), "purple": (110, 70, 120), "brown": (120, 85, 55),
    "indigo": (55, 60, 110), "ochre": (190, 140, 70),
}


@dataclass
class Figure:
    species: str = "dog"
    robe: tuple[int, int, int] = (130, 128, 125)
    hat: str = ""  # official | crown | taoist | ""
    mount: str = ""  # ox
    beast: bool = False  # 不穿衣服的野兽/精怪
    pose: str = "stand"  # stand | kneel | fallen
    child: bool = False


def parse_figure(visual_prompt: str) -> Figure:
    p = visual_prompt.lower()
    f = Figure()
    for name in SPECIES:
        if re.search(rf"\b{name}", p):
            f.species = name
            break
    # 袍服颜色：优先取紧挨 robe 前面的颜色词，否则取最先出现的颜色词
    found = sorted((m.start(), name) for name in COLORS for m in re.finditer(rf"\b{name}\b", p))
    robe_at = p.find("robe")
    near = [(pos, n) for pos, n in found if robe_at != -1 and 0 <= robe_at - pos <= 20]
    if near:
        f.robe = COLORS[near[-1][1]]
    elif found:
        f.robe = COLORS[found[0][1]]
    if _has(p, "crown", "emperor"):
        f.hat = "crown"
    elif _has(p, "official", "winged hat", "black hat"):
        f.hat = "official"
    elif _has(p, "taoist", "topknot", "priest"):
        f.hat = "taoist"
    if _has(p, "riding", "rides") and _has(p, "ox", "buffalo"):
        f.mount = "ox"
    # 不穿衣服、四脚着地的真动物（童话里的驴、狗、猫、公鸡）也走「野兽」画法
    f.beast = _has(p, "giant", "beast", "wild", "spirit tiger", "huge", "animal", "four-legged") and not _has(p, "robe")
    f.child = _has(p, "boy", "child", "little")
    return f


def _pose_for(prompt: str, species: str) -> str:
    p = prompt.lower()
    for pose, words in {"fallen": ("falls", "fallen", "collapses", "knocked", "跌倒"),
                        "kneel": ("kneel", "bows", "跪")}.items():
        for w in words:
            for m in re.finditer(re.escape(w), p):
                window = p[max(0, m.start() - 30): m.end() + 12]  # 英文主语在动词前
                if species in window:
                    return pose
    return "stand"


# ------------------------------------------------------------------ 调色
def _palette(scene: Scene) -> dict:
    pal = {
        "sky_top": (168, 196, 210), "sky_bottom": (238, 230, 206),
        "mountains": [(178, 192, 194), (132, 156, 150), (92, 118, 104), (66, 88, 72)],
        "ground": (150, 160, 115), "ink": (40, 36, 34),
    }
    if scene.time == "dusk":
        pal.update(sky_top=(118, 110, 150), sky_bottom=(242, 186, 128),
                   mountains=[(160, 140, 160), (120, 105, 130), (85, 75, 95), (60, 55, 70)], ground=(130, 115, 90))
    elif scene.time == "night":
        pal.update(sky_top=(26, 32, 58), sky_bottom=(72, 74, 102),
                   mountains=[(70, 76, 100), (52, 58, 80), (38, 42, 60), (28, 30, 44)], ground=(50, 52, 60))
    if "plague" in scene.weather:
        pal.update(sky_top=(112, 114, 100), sky_bottom=(176, 170, 140),
                   mountains=[(150, 150, 135), (120, 122, 105), (95, 98, 82), (70, 72, 60)], ground=(120, 118, 95))
    return pal


def _lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


# ------------------------------------------------------------------ 绘制基元
class Painter:
    def __init__(self, w: int, h: int, scene: Scene, seed: int):
        self.w, self.h = w * SS, h * SS
        self.scene = scene
        self.rng = np.random.default_rng(seed)
        self.pal = _palette(scene)
        self.img = Image.new("RGB", (self.w, self.h))
        self.ground_y = lambda x: self.h * 0.86  # 人物站位地面线，由各场景覆盖
        self.anchor = (self.w * 0.5, self.h * 0.6)  # 特效发源点

    # --- 通用 ---
    def layer(self) -> tuple[Image.Image, ImageDraw.ImageDraw]:
        lay = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        return lay, ImageDraw.Draw(lay)

    def merge(self, lay: Image.Image, blur: float = 0) -> None:
        if blur:
            lay = lay.filter(ImageFilter.GaussianBlur(blur * SS))
        self.img.paste(lay, (0, 0), lay)

    def sky(self) -> None:
        top, bottom = self.pal["sky_top"], self.pal["sky_bottom"]
        d = ImageDraw.Draw(self.img)
        for y in range(self.h):
            d.line([(0, y), (self.w, y)], fill=_lerp(top, bottom, y / self.h))
        lay, ld = self.layer()
        if self.scene.time == "dusk":
            cx, cy, r = self.w * 0.78, self.h * 0.32, self.h * 0.06
            ld.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(235, 120, 70, 220))
        elif self.scene.time == "night":
            cx, cy, r = self.w * 0.8, self.h * 0.16, self.h * 0.045
            ld.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(240, 236, 210, 230))
            for _ in range(60):
                x, y = self.rng.random() * self.w, self.rng.random() * self.h * 0.5
                ld.ellipse([x, y, x + 2 * SS, y + 2 * SS], fill=(230, 230, 210, 160))
        for _ in range(int(self.rng.integers(3, 7))):  # 云
            cx, cy = self.rng.random() * self.w, self.rng.random() * self.h * 0.35
            rw, rh = self.w * (0.08 + self.rng.random() * 0.12), self.h * 0.03
            alpha = 90 if self.scene.time != "night" else 40
            ld.ellipse([cx - rw, cy - rh, cx + rw, cy + rh], fill=(255, 252, 245, alpha))
        self.merge(lay, blur=6)

    def ridge(self, base: float, amp: float, n_peaks: int, sharp: float) -> np.ndarray:
        xs = np.linspace(0, 1, self.w // 4 + 1)
        y = np.full_like(xs, base)
        for _ in range(n_peaks):
            c = self.rng.random()
            s = sharp * (0.5 + self.rng.random())
            y -= amp * (0.4 + 0.6 * self.rng.random()) * np.exp(-((xs - c) / s) ** 2)
        y += np.convolve(self.rng.normal(0, amp * 0.03, xs.size), np.ones(5) / 5, mode="same")
        return y * self.h

    def mountains(self, layers: int = 4, tall: float = 1.0, base: float = 0.62) -> list[np.ndarray]:
        ridges = []
        cols = self.pal["mountains"]
        for i in range(layers):
            t = i / max(1, layers - 1)
            r = self.ridge(base + 0.08 * t, (0.42 - 0.22 * t) * tall, n_peaks=5 - i // 2, sharp=0.07 + 0.05 * t)
            ridges.append(r)
            lay, ld = self.layer()
            xs = np.linspace(0, self.w, r.size)
            poly = list(zip(xs, r)) + [(self.w, self.h), (0, self.h)]
            col = cols[min(i, len(cols) - 1)]
            ld.polygon(poly, fill=col + (255,))
            ld.line(list(zip(xs, r)), fill=_lerp(col, self.pal["ink"], 0.45) + (200,), width=2 * SS)
            self.merge(lay, blur=0.6)
            if "mist" in self.scene.weather or i < layers - 1:
                self.mist_band(float(np.median(r)) + self.h * 0.05, strength=110 if "mist" in self.scene.weather else 50)
        return ridges

    def mist_band(self, y: float, strength: int = 90) -> None:
        lay, ld = self.layer()
        for _ in range(6):
            cx = self.rng.random() * self.w
            rw, rh = self.w * (0.2 + self.rng.random() * 0.3), self.h * 0.035
            ld.ellipse([cx - rw, y - rh, cx + rw, y + rh], fill=(250, 248, 240, strength))
        self.merge(lay, blur=10)

    def ground(self, top: float) -> None:
        lay, ld = self.layer()
        col = self.pal["ground"]
        xs = np.linspace(0, self.w, 40)
        ys = top * self.h + np.sin(xs / self.w * math.pi * 2 + self.rng.random() * 6) * self.h * 0.012
        ld.polygon(list(zip(xs, ys)) + [(self.w, self.h), (0, self.h)], fill=col + (255,))
        self.merge(lay, blur=0.8)

    def pine(self, x: float, base: float, height: float) -> None:
        lay, ld = self.layer()
        ink = self.pal["ink"]
        lean = (self.rng.random() - 0.5) * height * 0.25
        ld.line([(x, base), (x + lean * 0.5, base - height * 0.5), (x + lean, base - height)],
                fill=(80, 60, 45, 255), width=max(2, int(height * 0.05)))
        green = _lerp((40, 70, 55), self.pal["mountains"][-1], 0.3)
        for k in range(5):
            t = k / 5
            cy = base - height * (0.35 + 0.65 * t)
            rw = height * (0.32 - 0.18 * t)
            cx = x + lean * (0.35 + 0.65 * t)
            ld.ellipse([cx - rw, cy - height * 0.06, cx + rw, cy + height * 0.05], fill=green + (235,),
                       outline=ink + (160,), width=SS)
        self.merge(lay)

    def tree(self, x: float, base: float, height: float, dark: bool = False) -> None:
        """阔叶树：一团团树冠叠在树干上（欧洲童话的森林、村口）。"""
        lay, ld = self.layer()
        ink = self.pal["ink"] + (200,)
        trunk_w = height * 0.07
        ld.rectangle([x - trunk_w / 2, base - height * 0.45, x + trunk_w / 2, base], fill=(95, 70, 50, 255), outline=ink)
        leaf = (55, 85, 60) if dark else (85, 125, 75)
        if self.scene.time == "night":
            leaf = _lerp(leaf, (25, 30, 45), 0.55)
        for _ in range(5):
            r = height * (0.16 + 0.08 * self.rng.random())
            cx = x + (self.rng.random() - 0.5) * height * 0.35
            cy = base - height * (0.55 + 0.3 * self.rng.random())
            ld.ellipse([cx - r, cy - r, cx + r, cy + r], fill=_lerp(leaf, (0, 0, 0), 0.15 * self.rng.random()) + (255,),
                       outline=ink, width=SS)
        self.merge(lay, blur=0.4)

    def house(self, cx: float, base: float, width: float, lit: bool = False) -> None:
        """欧洲乡间房子：白墙、木筋、陡坡屋顶；lit=True 时窗里透出灯光。"""
        lay, ld = self.layer()
        ink = self.pal["ink"] + (255,)
        night = self.scene.time == "night"
        wall = (200, 190, 170) if not night else (95, 92, 100)
        wall_h = width * 0.55
        top = base - wall_h
        ld.rectangle([cx - width / 2, top, cx + width / 2, base], fill=wall + (255,), outline=ink, width=SS)
        beam = (110, 75, 50, 255) if not night else (55, 45, 45, 255)
        for k in range(1, 4):  # 木筋
            x = cx - width / 2 + width * k / 4
            ld.line([(x, top), (x, base)], fill=beam, width=2 * SS)
        ld.line([(cx - width / 2, top + wall_h * 0.45), (cx + width / 2, top + wall_h * 0.45)], fill=beam, width=2 * SS)
        roof = (150, 70, 50) if not night else (70, 45, 45)
        ld.polygon([(cx - width * 0.6, top), (cx, top - width * 0.55), (cx + width * 0.6, top)], fill=roof + (255,),
                   outline=ink, width=SS)
        ld.rectangle([cx + width * 0.22, top - width * 0.45, cx + width * 0.32, top - width * 0.15], fill=roof + (255,),
                     outline=ink)  # 烟囱
        win = (250, 205, 110) if lit else ((175, 195, 205) if not night else (45, 50, 70))
        ww = width * 0.16
        for wx in (cx - width * 0.28, cx + width * 0.12):
            ld.rectangle([wx, top + wall_h * 0.18, wx + ww, top + wall_h * 0.4], fill=win + (255,), outline=ink, width=SS)
            ld.line([(wx + ww / 2, top + wall_h * 0.18), (wx + ww / 2, top + wall_h * 0.4)], fill=ink, width=SS)
        ld.rectangle([cx - width * 0.06, base - wall_h * 0.42, cx + width * 0.06, base], fill=beam, outline=ink)  # 门
        self.merge(lay)
        if lit:  # 窗口的光晕
            glow, gd = self.layer()
            for wx in (cx - width * 0.28, cx + width * 0.12):
                gd.ellipse([wx - ww, top, wx + ww * 2, top + wall_h * 0.6], fill=(255, 210, 120, 70))
            self.merge(glow, blur=8)

    def roof(self, ld, cx: float, y: float, width: float, height: float, color) -> None:
        """上翘飞檐屋顶。"""
        half = width / 2
        tip = height * 0.45
        pts = [(cx - half * 1.12, y - tip), (cx - half * 0.85, y + height * 0.15), (cx - half * 0.55, y - height * 0.55),
               (cx - half * 0.25, y - height), (cx + half * 0.25, y - height), (cx + half * 0.55, y - height * 0.55),
               (cx + half * 0.85, y + height * 0.15), (cx + half * 1.12, y - tip)]
        ld.polygon(pts, fill=color + (255,), outline=self.pal["ink"] + (220,))
        ld.line([(cx - half * 0.3, y - height), (cx + half * 0.3, y - height)], fill=self.pal["ink"] + (255,),
                width=max(SS, int(height * 0.12)))

    def building(self, cx: float, base: float, width: float, tiers: int = 1, wall=(170, 60, 50),
                 roof=(70, 72, 82), seals: bool = False, plaque: str = "", platform: bool = True) -> tuple:
        lay, ld = self.layer()
        ink = self.pal["ink"]
        bh = width * 0.32
        if platform:
            ph = width * 0.08
            ld.rectangle([cx - width * 0.62, base - ph, cx + width * 0.62, base], fill=(205, 200, 185, 255),
                         outline=ink + (180,))
            for k in range(4):  # 台阶
                sw = width * (0.16 + 0.03 * k)
                sy = base - ph + ph * k / 4
                ld.rectangle([cx - sw / 2, sy, cx + sw / 2, sy + ph / 4], fill=(222, 218, 205, 255), outline=ink + (120,))
            base -= ph
        ld.rectangle([cx - width * 0.45, base - bh, cx + width * 0.45, base], fill=wall + (255,), outline=ink + (200,))
        n_col = 6
        for k in range(n_col + 1):
            x = cx - width * 0.45 + width * 0.9 * k / n_col
            ld.line([(x, base - bh), (x, base)], fill=_lerp(wall, ink, 0.4) + (255,), width=max(SS, int(width * 0.012)))
        door_w = width * 0.14
        ld.rectangle([cx - door_w / 2, base - bh * 0.75, cx + door_w / 2, base], fill=_lerp(wall, ink, 0.5) + (255,))
        if seals:
            for sgn in (-1, 1):
                ld.line([(cx - door_w * 0.7, base - bh * 0.8), (cx + door_w * 0.7, base - bh * 0.05)][::sgn],
                        fill=(245, 240, 225, 255), width=max(2, int(width * 0.025)))
            ld.line([(cx - door_w * 0.8, base - bh * 0.42), (cx + door_w * 0.8, base - bh * 0.42)],
                    fill=(245, 240, 225, 255), width=max(2, int(width * 0.02)))
        top = base - bh
        for t in range(tiers):
            rw = width * (1.05 - 0.28 * t)
            rh = width * (0.16 - 0.03 * t)
            self.roof(ld, cx, top - t * rh * 0.9, rw, rh, roof)
        if plaque:
            pw, ph = width * 0.2, width * 0.06
            py = top + bh * 0.08
            ld.rectangle([cx - pw / 2, py, cx + pw / 2, py + ph], fill=(40, 50, 70, 255), outline=(200, 160, 60, 255))
            ld.text((cx, py + ph / 2), plaque, font=font(max(8, int(ph * 0.7))), fill=(225, 185, 80, 255), anchor="mm")
        self.merge(lay)
        return cx, top - tiers * width * 0.12

    def stairs_path(self, pts: list[tuple[float, float]], width: float) -> None:
        lay, ld = self.layer()
        ld.line(pts, fill=(214, 200, 168, 240), width=int(width), joint="curve")
        ld.line(pts, fill=self.pal["ink"] + (90,), width=max(1, SS))
        self.merge(lay, blur=0.5)

    # --- 特效 ---
    def smoke(self, src: tuple[float, float]) -> None:
        lay, ld = self.layer()
        x, y = src
        steps = 26
        for k in range(steps):
            t = k / steps
            yy = y - t * (y + self.h * 0.05)
            xx = x + math.sin(t * 7 + self.rng.random()) * self.w * 0.05 * t
            r = self.h * (0.03 + 0.12 * t)
            a = int(230 - 120 * t)
            ld.ellipse([xx - r, yy - r * 0.8, xx + r, yy + r * 0.8], fill=(25, 22, 28, a))
        self.merge(lay, blur=5)

    def lights(self, src: tuple[float, float], count: int = 40) -> None:
        glow, gd = self.layer()
        core, cd = self.layer()
        x0, y0 = src
        for k in range(count):
            ang = -math.pi * (0.05 + 0.9 * self.rng.random())
            length = self.h * (0.25 + 0.6 * self.rng.random())
            x1, y1 = x0 + math.cos(ang) * length * 1.4, y0 + math.sin(ang) * length
            gd.line([(x0, y0), (x1, y1)], fill=(255, 200, 80, 120), width=5 * SS)
            cd.line([((x0 + x1) / 2, (y0 + y1) / 2), (x1, y1)], fill=(255, 240, 170, 230), width=max(1, SS))
            cd.ellipse([x1 - 3 * SS, y1 - 3 * SS, x1 + 3 * SS, y1 + 3 * SS], fill=(255, 245, 200, 255))
        self.merge(glow, blur=4)
        self.merge(core)

    def stars(self, count: int = 108) -> None:
        glow, gd = self.layer()
        core, cd = self.layer()
        for _ in range(count):
            x, y = self.rng.random() * self.w, self.rng.random() * self.h * 0.6
            ang = self.rng.random() * math.pi * 2
            tail = self.h * (0.02 + 0.05 * self.rng.random())
            tx, ty = x - math.cos(ang) * tail, y - math.sin(ang) * tail
            gd.line([(tx, ty), (x, y)], fill=(255, 205, 90, 200), width=7 * SS)
            cd.line([(tx, ty), (x, y)], fill=(255, 240, 175, 255), width=2 * SS)
            cd.ellipse([x - 2 * SS, y - 2 * SS, x + 2 * SS, y + 2 * SS], fill=(255, 250, 215, 255))
        self.merge(glow, blur=3)
        self.merge(core)

    def wind(self) -> None:
        lay, ld = self.layer()
        for _ in range(14):
            x, y = self.rng.random() * self.w, self.rng.random() * self.h * 0.8
            r = self.h * (0.05 + 0.1 * self.rng.random())
            ld.arc([x - r * 2, y - r, x + r * 2, y + r], 200, 330, fill=(250, 250, 245, 150), width=2 * SS)
        self.merge(lay, blur=1)

    def rain(self) -> None:
        lay, ld = self.layer()
        for _ in range(500):
            x, y = self.rng.random() * self.w, self.rng.random() * self.h
            ld.line([(x, y), (x - 6 * SS, y + 22 * SS)], fill=(220, 225, 230, 90), width=SS)
        self.merge(lay)

    def plague_fog(self) -> None:
        lay, ld = self.layer()
        for _ in range(10):
            cx, cy = self.rng.random() * self.w, self.h * (0.5 + 0.4 * self.rng.random())
            rw, rh = self.w * 0.3, self.h * 0.08
            ld.ellipse([cx - rw, cy - rh, cx + rw, cy + rh], fill=(150, 160, 120, 70))
        self.merge(lay, blur=14)

    def torch(self, x: float, y: float, size: float) -> None:
        glow, gd = self.layer()
        gd.ellipse([x - size * 2, y - size * 2, x + size * 2, y + size * 2], fill=(255, 170, 60, 120))
        self.merge(glow, blur=8)
        lay, ld = self.layer()
        ld.line([(x, y), (x, y + size * 2)], fill=(70, 50, 40, 255), width=max(2, int(size * 0.3)))
        ld.ellipse([x - size * 0.4, y - size * 0.8, x + size * 0.4, y + size * 0.2], fill=(255, 220, 120, 255))
        self.merge(lay)

    # --- 人物与生灵 ---
    def figure(self, fig: Figure, x: float, base: float, height: float, facing: int) -> None:
        size = int(height * 3.2) + 4
        lay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        ld = ImageDraw.Draw(lay)
        cx, by = size / 2, size * 0.8
        ink = self.pal["ink"] + (255,)
        lw = max(1, int(height * 0.035))
        head_c, feat_c, kind = SPECIES.get(fig.species, SPECIES["dog"])
        h = height * (0.78 if fig.child else 1.0)

        if fig.beast or kind == "serpent":
            self._beast(ld, kind, cx, by, h, head_c, feat_c, facing, lw)
        else:
            if fig.mount == "ox":
                self._ox(ld, cx, by, h * 0.9, facing, lw)
                by -= h * 0.5
                h *= 0.85
            kneel = fig.pose == "kneel"
            body_h = h * (0.45 if kneel else 0.62)
            top = by - body_h
            # 袍服
            ld.polygon([(cx - h * 0.14, top), (cx + h * 0.14, top), (cx + h * 0.27, by), (cx - h * 0.27, by)],
                       fill=fig.robe + (255,), outline=ink, width=lw)
            ld.line([(cx - h * 0.27, by), (cx + h * 0.27, by)], fill=_lerp(fig.robe, (0, 0, 0), 0.4) + (255,),
                    width=lw * 2)
            # 袖
            ld.polygon([(cx + facing * h * 0.08, top + body_h * 0.15), (cx + facing * h * 0.3, top + body_h * 0.45),
                        (cx + facing * h * 0.2, top + body_h * 0.55)], fill=_lerp(fig.robe, (0, 0, 0), 0.15) + (255,),
                       outline=ink, width=lw)
            self._head(ld, kind, cx, top, h, head_c, feat_c, facing, lw, fig.hat)
        if fig.pose == "fallen":
            lay = lay.rotate(-70 * facing, resample=Image.BICUBIC, center=(cx, by))
        self.img.paste(lay, (int(x - size / 2), int(base - size * 0.8)), lay)

    def _head(self, ld, kind, cx, top, h, head_c, feat_c, facing, lw, hat) -> None:
        ink = self.pal["ink"] + (255,)
        r = h * 0.13
        hx, hy = cx, top - r * 0.9
        if kind in ("bird", "crane"):
            neck = h * (0.22 if kind == "crane" else 0.12)
            ld.line([(cx, top), (cx + facing * r * 0.3, top - neck)], fill=head_c + (255,), width=int(r * 0.8))
            hx, hy = cx + facing * r * 0.3, top - neck - r * 0.5
            r *= 0.8
        elif kind == "tortoise":
            ld.pieslice([cx - h * 0.3, top - h * 0.05, cx + h * 0.3, top + h * 0.5], 180, 360,
                        fill=feat_c + (255,), outline=ink, width=lw)  # 背甲
        if kind == "lion":
            ld.ellipse([hx - r * 1.6, hy - r * 1.6, hx + r * 1.6, hy + r * 1.6], fill=feat_c + (255,), outline=ink,
                       width=lw)
        ld.ellipse([hx - r, hy - r, hx + r, hy + r], fill=head_c + (255,), outline=ink, width=lw)
        ex = hx + facing * r * 0.4
        ld.ellipse([ex - r * 0.12, hy - r * 0.25, ex + r * 0.12, hy - r * 0.01], fill=ink)
        if kind == "bird":
            ld.polygon([(hx + facing * r * 0.8, hy - r * 0.15), (hx + facing * r * 1.9, hy + r * 0.1),
                        (hx + facing * r * 0.8, hy + r * 0.35)], fill=feat_c + (255,), outline=ink)
            if feat_c[0] > 200 and feat_c[1] < 80:  # 鸡冠
                ld.ellipse([hx - r * 0.3, hy - r * 1.4, hx + r * 0.4, hy - r * 0.7], fill=feat_c + (255,))
        elif kind == "crane":
            ld.line([(hx + facing * r * 0.8, hy), (hx + facing * r * 2.6, hy + r * 0.3)], fill=(50, 45, 40, 255),
                    width=max(1, int(r * 0.3)))
            ld.ellipse([hx - r * 0.35, hy - r * 1.05, hx + r * 0.35, hy - r * 0.55], fill=feat_c + (255,))
        elif kind in ("rabbit",):
            for d in (-0.4, 0.3):
                ld.ellipse([hx + d * r - r * 0.18, hy - r * 2.4, hx + d * r + r * 0.18, hy - r * 0.6],
                           fill=head_c + (255,), outline=ink, width=lw)
        elif kind in ("dog", "fox", "bear", "monkey", "tiger", "lion", "deer"):
            for d in (-0.6, 0.6):
                ld.ellipse([hx + d * r - r * 0.3, hy - r * 1.2, hx + d * r + r * 0.3, hy - r * 0.55],
                           fill=head_c + (255,), outline=ink, width=lw)
            ld.ellipse([hx + facing * r * 0.6 - r * 0.45, hy, hx + facing * r * 0.6 + r * 0.45, hy + r * 0.6],
                       fill=feat_c + (255,) if kind in ("fox", "monkey", "deer") else _lerp(head_c, (255, 255, 255), 0.4) + (255,))
            if kind == "tiger":
                for k in (-0.3, 0.1):
                    ld.line([(hx + k * r, hy - r * 0.9), (hx + k * r + r * 0.15, hy - r * 0.4)], fill=ink, width=lw)
            if kind == "deer":
                for d in (-0.4, 0.4):
                    ld.line([(hx + d * r, hy - r), (hx + d * r * 2, hy - r * 2.2)], fill=(110, 80, 50, 255), width=lw * 2)
        elif kind == "ox":
            for d in (-1, 1):
                ld.arc([hx + d * r * 0.9 - r * 0.6, hy - r * 1.6, hx + d * r * 0.9 + r * 0.6, hy - r * 0.4],
                       200 if d < 0 else 250, 290 if d < 0 else 340, fill=feat_c + (255,), width=lw * 2)
        # 帽冠
        if hat == "official":
            ld.rectangle([hx - r * 0.75, hy - r * 1.55, hx + r * 0.75, hy - r * 0.75], fill=(30, 30, 34, 255))
            ld.line([(hx - r * 2.2, hy - r * 1.1), (hx + r * 2.2, hy - r * 1.1)], fill=(30, 30, 34, 255),
                    width=max(1, int(r * 0.22)))
        elif hat == "crown":
            ld.polygon([(hx - r * 0.8, hy - r * 0.8), (hx - r * 0.6, hy - r * 1.7), (hx, hy - r * 1.2),
                        (hx + r * 0.6, hy - r * 1.7), (hx + r * 0.8, hy - r * 0.8)], fill=(225, 175, 60, 255), outline=ink)
        elif hat == "taoist":
            ld.ellipse([hx - r * 0.35, hy - r * 1.55, hx + r * 0.35, hy - r * 0.85], fill=(40, 38, 40, 255))
            ld.line([(hx - r * 0.6, hy - r * 1.2), (hx + r * 0.6, hy - r * 1.2)], fill=(200, 170, 90, 255), width=lw)

    def _ox(self, ld, cx, by, h, facing, lw) -> None:
        ink = self.pal["ink"] + (255,)
        body = (200, 160, 90, 255)
        bw, bh = h * 0.9, h * 0.38
        for dx in (-0.32, -0.15, 0.18, 0.34):
            ld.line([(cx + dx * bw, by - bh * 0.4), (cx + dx * bw, by)], fill=ink, width=lw * 3)
        ld.ellipse([cx - bw / 2, by - bh * 1.35, cx + bw / 2, by - bh * 0.25], fill=body, outline=ink, width=lw)
        hx = cx + facing * bw * 0.55
        ld.ellipse([hx - bh * 0.35, by - bh * 1.35, hx + bh * 0.35, by - bh * 0.7], fill=body, outline=ink, width=lw)
        ld.arc([hx - bh * 0.5, by - bh * 1.7, hx + bh * 0.5, by - bh * 1.05], 200, 340, fill=(240, 235, 220, 255),
               width=lw * 2)

    def _beast(self, ld, kind, cx, by, h, head_c, feat_c, facing, lw) -> None:
        ink = self.pal["ink"] + (255,)
        if kind == "serpent":
            pts = []
            for k in range(40):
                t = k / 39
                pts.append((cx - facing * h * 1.1 + facing * t * h * 2.0, by - h * 0.1 - math.sin(t * math.pi * 3) * h * 0.12))
            ld.line(pts, fill=ink, width=int(h * 0.2))
            ld.line(pts, fill=head_c + (255,), width=int(h * 0.15))
            hx, hy = pts[-1][0] + facing * h * 0.05, pts[-1][1] - h * 0.35
            ld.line([pts[-1], (hx, hy)], fill=head_c + (255,), width=int(h * 0.14))
            ld.ellipse([hx - h * 0.12, hy - h * 0.1, hx + h * 0.14, hy + h * 0.08], fill=head_c + (255,), outline=ink,
                       width=lw)
            ld.line([(hx + facing * h * 0.13, hy), (hx + facing * h * 0.28, hy + h * 0.03)], fill=feat_c + (255,),
                    width=lw)
            return
        if kind in ("donkey", "dog", "cat", "bird"):
            self._animal(ld, kind, cx, by, h, head_c, feat_c, facing, lw)
            return
        # 四足野兽（虎、豹、熊）
        bw, bh = h * 1.6, h * 0.5
        for dx in (-0.35, -0.2, 0.22, 0.36):
            ld.line([(cx + dx * bw, by - bh * 0.5), (cx + dx * bw + facing * h * 0.08, by)], fill=head_c + (255,),
                    width=int(h * 0.11))
        ld.ellipse([cx - bw / 2, by - bh * 1.3, cx + bw / 2, by - bh * 0.3], fill=head_c + (255,), outline=ink, width=lw)
        if kind == "tiger":
            for k in range(6):
                x = cx - bw * 0.35 + k * bw * 0.13
                ld.line([(x, by - bh * 1.25), (x + bw * 0.03, by - bh * 0.75)], fill=feat_c + (255,), width=lw * 2)
        ld.line([(cx - facing * bw * 0.48, by - bh), (cx - facing * bw * 0.75, by - bh * 1.6)], fill=head_c + (255,),
                width=int(h * 0.07))
        hx, hy = cx + facing * bw * 0.52, by - bh * 1.3
        r = h * 0.28
        ld.ellipse([hx - r, hy - r, hx + r, hy + r], fill=head_c + (255,), outline=ink, width=lw)
        for d in (-0.6, 0.6):
            ld.ellipse([hx + d * r - r * 0.25, hy - r * 1.2, hx + d * r + r * 0.25, hy - r * 0.6], fill=head_c + (255,),
                       outline=ink, width=lw)
        ld.ellipse([hx + facing * r * 0.35 - r * 0.12, hy - r * 0.3, hx + facing * r * 0.35 + r * 0.12, hy - r * 0.05],
                   fill=(250, 220, 60, 255), outline=ink)
        if kind == "tiger":
            ld.text((hx, hy - r * 0.45), "王", font=font(max(6, int(r * 0.6))), fill=feat_c + (255,), anchor="mm")
        ld.ellipse([hx + facing * r * 0.5 - r * 0.3, hy + r * 0.05, hx + facing * r * 0.5 + r * 0.3, hy + r * 0.55],
                   fill=(250, 245, 235, 255), outline=ink, width=lw)


    def _animal(self, ld, kind, cx, by, h, head_c, feat_c, facing, lw) -> None:
        """童话里的真动物，四脚着地、不穿衣服：驴、狗、猫，和站着的公鸡。h 是这只动物的身高。"""
        ink = self.pal["ink"] + (255,)
        body = head_c + (255,)
        if kind == "bird":  # 公鸡
            ld.polygon([(cx - facing * h * 0.25, by - h * 0.55), (cx - facing * h * 0.55, by - h * 0.95),
                        (cx - facing * h * 0.42, by - h * 0.5)], fill=(40, 70, 60, 255), outline=ink)  # 尾羽
            ld.ellipse([cx - h * 0.3, by - h * 0.7, cx + h * 0.3, by - h * 0.25], fill=body, outline=ink, width=lw)
            for dx in (-0.08, 0.08):
                ld.line([(cx + dx * h, by - h * 0.28), (cx + dx * h, by)], fill=(220, 160, 50, 255), width=lw * 2)
            hx, hy, r = cx + facing * h * 0.22, by - h * 0.82, h * 0.13
            ld.ellipse([hx - r, hy - r, hx + r, hy + r], fill=body, outline=ink, width=lw)
            ld.ellipse([hx - r * 0.6, hy - r * 2.0, hx + r * 0.6, hy - r * 0.7], fill=feat_c + (255,))  # 鸡冠
            ld.polygon([(hx + facing * r * 0.8, hy - r * 0.2), (hx + facing * r * 1.8, hy + r * 0.1),
                        (hx + facing * r * 0.8, hy + r * 0.4)], fill=(230, 170, 50, 255), outline=ink)
            ld.ellipse([hx - r * 0.25, hy + r * 0.6, hx + r * 0.25, hy + r * 1.4], fill=feat_c + (255,))  # 肉垂
            ld.ellipse([hx + facing * r * 0.3 - r * 0.15, hy - r * 0.3, hx + facing * r * 0.3 + r * 0.15, hy],
                       fill=ink)
            return
        bw, bh = h * 1.0, h * 0.5
        leg = h * (0.45 if kind == "donkey" else 0.35)
        top = by - leg - bh * 0.5
        for dx in (-0.36, -0.22, 0.24, 0.38):
            ld.line([(cx + dx * bw, top + bh * 0.5), (cx + dx * bw, by)], fill=_lerp(head_c, (0, 0, 0), 0.15) + (255,),
                    width=max(2, int(h * (0.08 if kind == "donkey" else 0.07))))
        ld.ellipse([cx - bw / 2, top, cx + bw / 2, top + bh], fill=body, outline=ink, width=lw)
        tail_x = cx - facing * bw * 0.48
        if kind == "cat":  # 竖起来的尾巴
            ld.line([(tail_x, top + bh * 0.4), (tail_x - facing * h * 0.1, top - h * 0.35)], fill=body, width=int(h * 0.07))
        else:
            ld.line([(tail_x, top + bh * 0.3), (tail_x - facing * h * 0.12, top + bh * 1.2)], fill=body,
                    width=max(2, int(h * 0.05)))
        # 脖子和头
        nx = cx + facing * bw * 0.42
        hx = nx + facing * h * (0.12 if kind == "donkey" else 0.06)
        hy = top - h * (0.22 if kind == "donkey" else 0.08)
        ld.line([(nx, top + bh * 0.4), (hx, hy)], fill=body, width=int(h * (0.18 if kind == "donkey" else 0.14)))
        r = h * (0.17 if kind == "donkey" else 0.22 if kind == "dog" else 0.2)
        if kind == "donkey":  # 长脸
            x0, x1 = sorted((hx - facing * r * 0.7, hx + facing * r * 1.6))
            ld.ellipse([x0, hy - r, x1, hy + r * 0.7], fill=body, outline=ink, width=lw)
            ld.ellipse([hx + facing * r * 1.1 - r * 0.45, hy - r * 0.1, hx + facing * r * 1.1 + r * 0.45, hy + r * 0.65],
                       fill=feat_c + (255,), outline=ink, width=lw)  # 白鼻头
            for d in (-0.35, 0.15):  # 竖长耳朵
                ld.ellipse([hx + d * r - r * 0.18, hy - r * 2.4, hx + d * r + r * 0.18, hy - r * 0.6], fill=body,
                           outline=ink, width=lw)
            ld.line([(nx - facing * r * 0.2, top), (hx - facing * r * 0.4, hy - r * 0.6)], fill=(70, 65, 62, 255),
                    width=lw * 2)  # 鬃毛
        else:
            ld.ellipse([hx - r, hy - r, hx + r, hy + r], fill=body, outline=ink, width=lw)
            ld.ellipse([hx + facing * r * 0.65 - r * 0.45, hy - r * 0.05, hx + facing * r * 0.65 + r * 0.45, hy + r * 0.6],
                       fill=feat_c + (255,), outline=ink, width=lw)
            if kind == "cat":  # 尖耳朵、胡子
                for d in (-0.55, 0.35):
                    ld.polygon([(hx + d * r - r * 0.3, hy - r * 0.6), (hx + d * r, hy - r * 1.5),
                                (hx + d * r + r * 0.3, hy - r * 0.6)], fill=body, outline=ink)
                for k in (-0.1, 0.15):
                    ld.line([(hx + facing * r * 0.9, hy + r * (0.3 + k)), (hx + facing * r * 1.9, hy + r * (0.2 + 2 * k))],
                            fill=ink, width=max(1, lw // 2))
            else:  # 狗：耷拉的耳朵
                ld.ellipse([hx - facing * r * 0.6 - r * 0.3, hy - r * 0.7, hx - facing * r * 0.6 + r * 0.3, hy + r * 0.5],
                           fill=feat_c + (255,) if kind != "dog" else _lerp(head_c, (0, 0, 0), 0.3) + (255,),
                           outline=ink, width=lw)
        ld.ellipse([hx + facing * r * 0.25 - r * 0.13, hy - r * 0.4, hx + facing * r * 0.25 + r * 0.13, hy - r * 0.12],
                   fill=ink)


# 真动物彼此的大小：以驴为 1
ANIMAL_SIZE = {"donkey": 1.0, "dog": 0.62, "cat": 0.45, "bird": 0.42}


# ------------------------------------------------------------------ 各场景
def _paint_setting(p: Painter) -> None:
    s, w, h = p.scene, p.w, p.h
    if s.setting == "interior":
        _interior(p)
        return
    if s.setting == "throne":
        _throne(p)
        return
    p.sky()
    if s.setting in ("palace",):
        p.mountains(layers=2, tall=0.35, base=0.55)
        p.ground(0.66)
        for k, (cx, width) in enumerate([(0.18, 0.18), (0.82, 0.2), (0.5, 0.36)]):
            p.building(w * cx, h * (0.7 if k < 2 else 0.74), w * width, tiers=2 if k == 2 else 1,
                       wall=(175, 55, 48), roof=(205, 150, 55) if k == 2 else (80, 85, 95), plaque=s.plaque_text if k == 2 else "")
        lay, ld = p.layer()  # 宫墙
        ld.rectangle([0, h * 0.76, w, h * 0.8], fill=(170, 60, 50, 255), outline=p.pal["ink"] + (200,))
        p.merge(lay)
        p.ground_y = lambda x: h * 0.8
        p.anchor = (w * 0.5, h * 0.6)
    elif s.setting == "road":
        p.mountains(layers=3, tall=0.7, base=0.55)
        p.ground(0.7)
        lay, ld = p.layer()
        pts = [(-w * 0.05, h * 0.84), (w * 0.3, h * 0.79), (w * 0.6, h * 0.76), (w * 1.05, h * 0.72)]
        ld.line(pts, fill=(212, 192, 150, 255), width=int(h * 0.06), joint="curve")
        river = [(-w * 0.05, h * 0.78), (w * 0.4, h * 0.74), (w * 1.05, h * 0.7)]
        ld.line(river, fill=(150, 180, 190, 220), width=int(h * 0.035), joint="curve")
        p.merge(lay, blur=1)
        p.ground_y = lambda x: h * (0.8 - 0.07 * min(1.0, max(0.0, x / w)))
        for k in range(5):
            p.pine(w * (0.05 + 0.22 * k), h * 0.93, h * 0.18)
    elif s.setting == "path":
        ridges = p.mountains(layers=3, tall=1.1, base=0.5)
        p.ground(0.6)
        pts = []
        for k in range(9):
            t = k / 8
            pts.append((w * (0.12 + 0.8 * t + 0.1 * math.sin(t * 9)), h * (0.81 - 0.48 * t)))
        p.stairs_path(pts, h * 0.035)
        for k in range(9):
            x = w * p.rng.random()
            p.pine(x, h * (0.62 + 0.36 * p.rng.random()), h * (0.16 + 0.1 * p.rng.random()))
        xs = [pt[0] for pt in pts]
        ys = [pt[1] for pt in pts]
        p.ground_y = lambda x: float(np.interp(x, xs, ys))
        p.anchor = (w * 0.5, h * 0.4)
        del ridges
    elif s.setting in ("temple", "sealed_hall"):
        p.mountains(layers=4, tall=1.0, base=0.58)
        p.ground(0.74 if s.setting == "temple" else 0.68)
        if s.setting == "temple":
            p.building(w * 0.7, h * 0.38, w * 0.12, tiers=1)
            p.stairs_path([(w * 0.55, h * 0.95), (w * 0.62, h * 0.75), (w * 0.7, h * 0.42)], h * 0.03)
            p.building(w * 0.42, h * 0.8, w * 0.3, tiers=2, plaque=s.plaque_text)
        else:
            p.building(w * 0.5, h * 0.8, w * 0.42, tiers=2, seals="seals" in s.effects, plaque=s.plaque_text,
                       wall=(150, 50, 45), roof=(55, 58, 66))
            p.anchor = (w * 0.5, h * 0.5)
        if "bell" in s.effects:
            p.building(w * 0.12, h * 0.76, w * 0.1, tiers=2)
        p.ground_y = lambda x: h * 0.8
        for k in range(4):
            p.pine(w * (0.04 + 0.3 * k) + w * 0.05 * p.rng.random(), h * 0.95, h * 0.2)
    elif s.setting in ("village", "meadow"):
        p.mountains(layers=2, tall=0.22, base=0.6)  # 平缓的丘陵
        p.ground(0.7)
        lay, ld = p.layer()
        pts = [(-w * 0.05, h * 0.9), (w * 0.35, h * 0.83), (w * 0.7, h * 0.79), (w * 1.05, h * 0.76)]
        ld.line(pts, fill=(205, 185, 140, 255), width=int(h * 0.05), joint="curve")
        p.merge(lay, blur=1)
        if s.setting == "village":
            for cx, bw in ((0.14, 0.17), (0.86, 0.15)):
                p.house(w * cx, h * 0.78, w * bw, lit=s.lit)
        for k in range(3):
            p.tree(w * (0.32 + 0.2 * k + 0.05 * p.rng.random()), h * 0.74, h * 0.2)
        p.ground_y = lambda x: h * 0.86
    elif s.setting in ("forest", "cottage"):
        p.mountains(layers=1, tall=0.15, base=0.62)
        p.ground(0.66)
        for k in range(9):  # 远处一排树
            p.tree(w * (k / 8) + w * 0.03 * p.rng.random(), h * 0.68, h * 0.3, dark=True)
        if s.setting == "cottage":
            p.house(w * 0.66, h * 0.8, w * 0.3, lit=s.lit)
            p.anchor = (w * 0.66, h * 0.6)
        for x in (0.04, 0.95):  # 前景大树
            p.tree(w * x, h * 1.02, h * 0.62, dark=True)
        p.ground_y = lambda x: h * 0.86
    elif s.setting == "sky":
        p.mountains(layers=3, tall=0.6, base=0.66)
        p.ground(0.86)
        p.ground_y = lambda x: h * 0.8
        p.anchor = (w * 0.5, h * 0.75)
    else:  # mountain / river
        p.mountains(layers=4, tall=1.15, base=0.6)
        if s.setting == "river":
            lay, ld = p.layer()
            ld.rectangle([0, h * 0.72, w, h * 0.84], fill=(150, 180, 188, 255))
            for _ in range(30):
                x, y = p.rng.random() * w, h * (0.73 + 0.1 * p.rng.random())
                ld.line([(x, y), (x + w * 0.05, y)], fill=(235, 240, 240, 180), width=SS)
            p.merge(lay, blur=0.6)
        p.ground(0.84)
        p.ground_y = lambda x: h * 0.8
        if "temple" in s.plaque_text or "pines" in s.effects:
            for k in range(3):
                p.pine(w * (0.08 + 0.4 * k), h * 0.96, h * 0.22)


def _interior(p: Painter) -> None:
    w, h, ink = p.w, p.h, p.pal["ink"]
    d = ImageDraw.Draw(p.img)
    for y in range(h):
        t = y / h
        d.line([(0, y), (w, y)], fill=_lerp((28, 22, 22), (70, 52, 42), t))
    lay, ld = p.layer()
    ld.polygon([(w * 0.2, h * 0.55), (w * 0.8, h * 0.55), (w, h), (0, h)], fill=(92, 78, 64, 255))
    for k in range(7):  # 地砖透视线
        x = w * (0.2 + 0.6 * k / 6)
        ld.line([(x, h * 0.55), (w * (k / 6), h)], fill=(60, 50, 42, 200), width=SS)
    for sx in (0.08, 0.24, 0.76, 0.92):  # 柱
        cw = w * (0.035 if sx in (0.24, 0.76) else 0.05)
        top = h * (0.05 if sx in (0.08, 0.92) else 0.12)
        bot = h * (0.98 if sx in (0.08, 0.92) else 0.6)
        ld.rectangle([w * sx - cw, top, w * sx + cw, bot], fill=(110, 40, 35, 255), outline=ink + (220,))
    ld.rectangle([0, 0, w, h * 0.1], fill=(45, 32, 28, 255))
    p.merge(lay, blur=0.5)
    s = p.scene
    cx, base = w * 0.5, h * 0.66
    if "pit" in s.effects:
        lay, ld = p.layer()
        ld.ellipse([cx - w * 0.14, base - h * 0.06, cx + w * 0.14, base + h * 0.06], fill=(8, 6, 8, 255),
                   outline=(90, 80, 70, 255), width=2 * SS)
        # 掘倒的石碑斜躺一旁
        ld.polygon([(cx + w * 0.17, base + h * 0.02), (cx + w * 0.33, base - h * 0.06), (cx + w * 0.35, base - h * 0.01),
                    (cx + w * 0.19, base + h * 0.07)], fill=(140, 140, 135, 255), outline=ink + (255,))
        p.merge(lay)
        p.anchor = (cx, base)
    elif "tablet" in s.effects:
        lay, ld = p.layer()
        tw, th = w * 0.09, h * 0.38
        ld.ellipse([cx - tw * 1.1, base - h * 0.03, cx + tw * 1.1, base + h * 0.06], fill=(105, 105, 100, 255),
                   outline=ink + (255,))  # 龟趺
        ld.rounded_rectangle([cx - tw / 2, base - th, cx + tw / 2, base], radius=int(tw * 0.35), fill=(150, 150, 145, 255),
                             outline=ink + (255,), width=2 * SS)
        if s.tablet_text:
            fs = int(min(tw * 0.6, th / (len(s.tablet_text) + 1)))
            for i, ch in enumerate(s.tablet_text):
                ld.text((cx, base - th + fs * (1.0 + i * 1.15)), ch, font=font(max(8, fs)), fill=(150, 30, 30, 255),
                        anchor="mm")
        p.merge(lay)
        p.anchor = (cx, base - th)
    for sx in (0.3, 0.7):  # 殿内常燃火把
        p.torch(w * sx, h * 0.38, h * 0.025)
    p.ground_y = lambda x: h * 0.8


def _throne(p: Painter) -> None:
    w, h, ink = p.w, p.h, p.pal["ink"]
    d = ImageDraw.Draw(p.img)
    for y in range(h):
        d.line([(0, y), (w, y)], fill=_lerp((70, 30, 28), (150, 110, 70), y / h))
    lay, ld = p.layer()
    ld.polygon([(w * 0.1, h * 0.6), (w * 0.9, h * 0.6), (w, h), (0, h)], fill=(175, 150, 110, 255))
    for k in range(6):
        x = w * (0.05 + 0.18 * k)
        ld.rectangle([x - w * 0.02, 0, x + w * 0.02, h * 0.62], fill=(165, 45, 40, 255), outline=ink + (200,))
        ld.line([(x - w * 0.02, h * 0.2), (x + w * 0.02, h * 0.25)], fill=(220, 170, 60, 255), width=3 * SS)
    for k in range(5):  # 丹陛
        sw = w * (0.32 - 0.04 * k)
        y = h * (0.6 - 0.04 * k)
        ld.rectangle([w * 0.5 - sw / 2, y - h * 0.04, w * 0.5 + sw / 2, y], fill=(205, 175, 120, 255), outline=ink + (160,))
    ld.rectangle([w * 0.44, h * 0.3, w * 0.56, h * 0.42], fill=(215, 165, 55, 255), outline=ink + (255,))  # 宝座
    ld.rectangle([w * 0.38, h * 0.12, w * 0.62, h * 0.32], fill=(120, 40, 35, 255), outline=(220, 170, 60, 255),
                 width=2 * SS)  # 屏风
    p.merge(lay, blur=0.4)
    p.ground_y = lambda x: h * 0.8
    p.throne = (w * 0.5, h * 0.42)


def _crowd(p: Painter, kind: str, avoid: list[float]) -> None:
    species, robe, hat, n = {
        "retinue": ("dog", (120, 110, 100), "", 7),
        "priests": ("rabbit", (130, 128, 125), "taoist", 5),
        "courtiers": ("deer", (60, 95, 150), "official", 8),
        "workers": ("dog", (140, 120, 90), "", 6),
        "robbers": ("fox", (70, 60, 70), "", 4),
    }[kind]
    fh = p.figure_h * 0.8
    for k in range(n):
        x = p.w * (0.08 + 0.84 * (k + 0.5) / n) + (p.rng.random() - 0.5) * p.w * 0.04
        if any(abs(x - a) < p.figure_h * 0.6 for a in avoid):
            x += p.figure_h
        f = Figure(species=species, robe=robe, hat=hat, pose="kneel" if kind == "courtiers" else "stand")
        p.figure(f, x, p.ground_y(x) + p.rng.random() * fh * 0.15, fh, facing=1 if x < p.w / 2 else -1)


def _stack(p: Painter, figs: list[Figure], x: float) -> None:
    """不莱梅的音乐家：驴在最下面，狗站驴背上，猫站狗背上，公鸡在最上面。按入镜顺序从下往上叠。"""
    base = p.ground_y(x)
    for fig in figs:
        kind = SPECIES.get(fig.species, SPECIES["dog"])[2]
        fh = p.figure_h * 1.8 * ANIMAL_SIZE.get(kind, 1.0)
        p.figure(fig, x, base, fh, facing=1)
        leg = 0.45 if kind == "donkey" else 0.32
        base -= fh * (leg + 0.2) if kind != "bird" else fh


class ScenicBackend:
    def generate(self, panel: Panel, width: int, height: int) -> Image.Image:
        scene = parse_scene(panel.prompt)
        p = Painter(width, height, scene, panel.seed)
        _paint_setting(p)
        if "plague" in scene.weather:
            p.plague_fog()
        if "smoke" in scene.effects:
            p.smoke(p.anchor)
        if "lights" in scene.effects:
            p.lights((p.anchor[0], p.anchor[1] - p.h * 0.15 if scene.setting != "interior" else p.anchor[1]))
        if "stars" in scene.effects:
            p.stars()

        p.figure_h = p.h * panel.figure_scale
        positions = panel.positions or [(i + 1) / (len(panel.characters) + 1) for i in range(len(panel.characters))]
        xs = [pos * p.w for pos in positions[: len(panel.characters)]]
        if scene.crowd:
            _crowd(p, scene.crowd, xs)
        center = sum(xs) / len(xs) if xs else p.w / 2
        figs = [parse_figure(panel.figure_prompts[i] if i < len(panel.figure_prompts) else panel.characters[i])
                for i in range(len(xs))]
        if _has(panel.prompt.lower(), "stacked", "on top of each other", "叠罗汉") and all(f.beast for f in figs):
            _stack(p, figs, center)
            figs, xs = [], []
        for i, x in enumerate(xs):
            fig = figs[i]
            fig.pose = _pose_for(panel.prompt, fig.species)
            facing = 1 if x <= center else -1
            if len(xs) == 1:
                facing = 1 if x < p.w / 2 else -1
            kind = SPECIES.get(fig.species, SPECIES["dog"])[2]
            fh = p.figure_h * (1.8 * ANIMAL_SIZE.get(kind, 1.0) if fig.beast else 1.0)
            base = p.ground_y(x)
            if getattr(p, "throne", None) and fig.hat == "crown":
                x, base = p.throne
                fh *= 0.9
                facing = 1
            p.figure(fig, x, base, fh, facing)

        if "wind" in scene.weather:
            p.wind()
        if "rain" in scene.weather:
            p.rain()
        img = p.img.resize((width, height), Image.LANCZOS)
        return img
