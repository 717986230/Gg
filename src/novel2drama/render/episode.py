"""把一集分镜渲染成成片 mp4 + 封面。"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from ..canvas import CanvasSpec, Rect, build_canvas
from ..models import Episode, StoryBible
from ..styles import StylePreset, get_style
from .compose import render_badge, render_base, render_cover, render_subtitle, render_title_card
from .images import ImageBackend
from .tts import TTS, _silence
from .video import concat, ensure_ffmpeg, render_shot

GAP = 0.15  # 台词之间的停顿


def _card_spec(shot_id: str, w: int, h: int, accent: str, camera: str, duration: float, transition: str) -> CanvasSpec:
    return CanvasSpec(shot_id=shot_id, width=w, height=h, layout="full_bleed", panels=[], subtitles=[],
                      subtitle_box=Rect(x=0, y=0, w=1, h=1), accent=accent, camera=camera, duration=duration,
                      transition=transition)


def render_episode(
    ep: Episode,
    bible: StoryBible,
    backend: ImageBackend,
    out_dir: Path,
    tts: TTS | None = None,
    bgm: Path | None = None,
    style: StylePreset | str | None = None,
) -> tuple[Path, Path]:
    ensure_ffmpeg()
    style = style if isinstance(style, StylePreset) else get_style(style)
    W, H = style.canvas.width, style.canvas.height
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / "work"
    work.mkdir(exist_ok=True)
    tts = tts or TTS(bible)
    clips: list[Path] = []
    accent = bible.world.palette[0] if bible.world.palette else "#FFD54F"
    badge = f"第{ep.chapter}集"

    # 先渲染第一个镜头的底图，片头卡/片尾卡用它做虚化背景
    first_spec = build_canvas(ep.shots[0], bible, style)
    first_base = render_base(first_spec, backend, work / "panels")

    # 片头：前情提要（第2集起）或剧名卡
    if ep.recap and ep.chapter > 1:
        head_title, head_text = "前情提要", ep.recap
    else:
        head_title, head_text = f"第{ep.chapter}集", ep.title
    card = work / "head.png"
    render_title_card(W, H, head_title, head_text, accent, background=first_base).save(card)
    audio = work / "head.mp3"
    dur = tts.synth("narrator", head_text, audio) + 0.6
    clips.append(render_shot(_card_spec("head", W, H, accent, "推", dur, "黑场"), card, [], [audio],
                             work / "head.mp4", dur))

    cover: Path | None = None
    for shot in ep.shots:
        spec = build_canvas(shot, bible, style)
        base = render_base(spec, backend, work / "panels")
        base_png = work / f"{shot.id}.png"
        base.save(base_png)
        if cover is None:
            cover = out_dir / "cover.png"
            render_cover(base, ep.title, accent).save(cover)

        subs: list[tuple[Path, float, float]] = []
        audios: list[Path] = []
        t = 0.2
        for j, (line, sub) in enumerate(zip(shot.dialogue, spec.subtitles)):
            audio = work / f"{shot.id}_l{j}.mp3"
            d = tts.synth(line.speaker, line.text, audio)
            png = work / f"{shot.id}_sub{j}.png"
            render_subtitle(spec, sub).save(png)
            subs.append((png, t, t + d))
            audios.append(audio)
            t += d + GAP
        # 镜头时长取分镜规划和配音时长的较大值，保证台词说完
        duration = max(shot.duration, t + 0.2)
        badge_png = work / "badge.png"
        if not badge_png.exists():
            render_badge(spec, badge).save(badge_png)
        subs.append((badge_png, 0.0, duration))
        if audios:
            # 开头留白：用静音片段对齐第一句的 0.2s 起点
            lead = work / "lead.mp3"
            if not lead.exists():
                _silence(lead, 0.2)
            gap = work / "gap.mp3"
            if not gap.exists():
                _silence(gap, GAP)
            seq = [lead]
            for k, a in enumerate(audios):
                seq.append(a)
                if k < len(audios) - 1:
                    seq.append(gap)
            audios = seq
        clips.append(render_shot(spec, base_png, subs, audios, work / f"{shot.id}.mp4", duration))

    # 片尾钩子卡：用最后一个镜头做背景
    last = Image.open(work / f"{ep.shots[-1].id}.png")
    end_card = work / "next.png"
    render_title_card(W, H, "下集预告", ep.cliffhanger, accent, background=last).save(end_card)
    end_audio = work / "next.mp3"
    dur = tts.synth("narrator", ep.cliffhanger, end_audio) + 0.8
    clips.append(render_shot(_card_spec("next", W, H, accent, "推", dur, "叠化"), end_card, [], [end_audio],
                             work / "next.mp4", dur))

    video = concat(clips, out_dir / f"episode_{ep.chapter:03d}.mp4", bgm)
    assert cover is not None
    return video, cover
