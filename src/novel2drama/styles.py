"""画面风格预设：画布尺寸、镜头偏好、角色设计方式、后期滤镜。

同一套流水线可以出完全不同气质的片子：
- portrait_anime   竖屏 9:16 赛璐璐动漫，人物近景多，适合抖音主信息流（默认）
- watercolor_epic  横屏小屏 16:9 水彩线条动画，大场景、人物小、第三视角、动物角色
风格保存在工作区 meta.json 中，分析、分镜、渲染各阶段共用。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CanvasProfile:
    width: int
    height: int
    safe_top: int
    safe_bottom: int
    safe_left: int
    safe_right: int

    @property
    def landscape(self) -> bool:
        return self.width > self.height

    @property
    def unit(self) -> float:
        """字号等尺寸的缩放基准（以 1080 短边为 1）。"""
        return min(self.width, self.height) / 1080


# 抖音竖屏：顶部状态栏/搜索，底部标题文案+进度条，右侧点赞评论栏
PORTRAIT = CanvasProfile(1080, 1920, safe_top=150, safe_bottom=420, safe_left=60, safe_right=140)
# 横屏：抖音横屏全屏播放时 UI 很少，只需留出底部进度条
LANDSCAPE = CanvasProfile(1920, 1080, safe_top=60, safe_bottom=90, safe_left=120, safe_right=120)
LANDSCAPE_SMALL = CanvasProfile(1280, 720, safe_top=40, safe_bottom=60, safe_left=80, safe_right=80)


@dataclass(frozen=True)
class StylePreset:
    name: str
    description: str
    canvas: CanvasProfile
    art_style: str
    negative: str
    figure_scale: float = 0.45          # 人物高度占画面高度的比例（占位/程序化渲染使用）
    wide_first: bool = False            # 强制大场景：双人/分屏布局改为全幅大景
    postprocess: str = ""               # 后期滤镜：watercolor_line
    character_rules: str = ""           # 分析阶段：角色设计要求
    storyboard_rules: str = ""          # 分镜阶段：镜头语言要求
    default_backend: str = "placeholder"
    tags: list[str] = field(default_factory=list)


PRESETS: dict[str, StylePreset] = {
    "portrait_anime": StylePreset(
        name="portrait_anime",
        description="竖屏 9:16 动漫短剧，人物近景为主",
        canvas=PORTRAIT,
        art_style="chinese anime, cel shading, vibrant colors",
        negative="lowres, bad anatomy, extra fingers, deformed face, text, watermark, logo, blurry",
        storyboard_rules=(
            "- 竖屏 9:16；单集 60-180 秒；镜头 12-40 个；近景、特写与中景为主，突出人物表情。\n"
            "- 关系紧密（intensity≥4）的角色同场时，优先 two_shot / split_vertical / inset_closeup，并安排反应镜头。"
        ),
    ),
    "watercolor_epic": StylePreset(
        name="watercolor_epic",
        description="横屏小屏 16:9 水彩线条动画：大场景、人物小、第三视角、动物角色",
        canvas=LANDSCAPE_SMALL,
        art_style=(
            "traditional chinese watercolor and ink line animation, loose ink outlines, soft watercolor washes, "
            "rice paper texture, epic wide landscape, tiny figures in vast scenery, third-person view, "
            "anthropomorphic animal characters in period costume"
        ),
        negative="close-up, portrait, face focus, photorealistic, 3d render, text, watermark, logo, human characters",
        figure_scale=0.13,
        wide_first=True,
        postprocess="watercolor_line",
        default_backend="scenic",
        character_rules=(
            "本剧所有角色都设计为拟人化动物（穿着符合时代的服饰、直立行走）。按性格、身份、绰号选择动物："
            "如威猛者用虎豹熊，文雅者用鹤鹿，奸猾者用狐鼠，憨直者用牛熊，傲慢的官员可用鹅、孔雀。"
            "visual_prompt 必须以英文动物名开头（如 'goose official, ...'），并写明服饰颜色和标志物；"
            "personality 与原著一致，动物只是外形，不改变人物性格与情节。"
        ),
        storyboard_rules=(
            "- 横屏 16:9 小屏；单集 60-150 秒；镜头 12-30 个；单镜 3-8 秒。\n"
            "- 大场景、人物小：至少 70% 镜头用 大远景/远景/全景，人物只占画面很小一部分，靠山川、宫殿、云雾、天象交代气势。\n"
            "- 一律第三视角（旁观者视角），不用主观镜头和特写；需要强调情绪时用 全景 + 动作 + 旁白，而不是脸部特写。\n"
            "- layout 以 full_bleed（全幅大景）为主，偶尔用 manga_3 做节奏蒙太奇；不要 split_vertical。\n"
            "- 运镜偏好缓慢的 推/拉/摇/移，像水墨长卷徐徐展开；旁白(narrator)承担叙事，角色台词简短。\n"
            "- image_prompt 写清：场景主体（山/宫殿/道观/大殿/长路/江河/天空）、时间天气（黄昏/夜/雾/雨）、"
            "人物在画面中的位置与动作、特效（黑气、金光、狂风）。"
        ),
        tags=["水彩", "国风动画"],
    ),
}

DEFAULT_STYLE = "portrait_anime"


def get_style(name: str | None) -> StylePreset:
    name = name or DEFAULT_STYLE
    if name not in PRESETS:
        raise ValueError(f"未知风格 {name}，可选：{', '.join(PRESETS)}")
    return PRESETS[name]
