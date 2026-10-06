"""把一集分镜渲染成成片 mp4 + 封面。"""
from __future__ import annotations

from pathlib import Path

from ..canvas import CanvasSpec, Rect, build_canvas
from ..models import Episode, StoryBible
from .compose import render_base, render_subtitle, render_title_card
from .images import ImageBackend
from .tts import TTS, _silence
from .video import concat, ensure_ffmpeg, render_shot

GAP = 0.15  # 台词之间的停顿


def render_episode(
    ep: Episode,
    bible: StoryBible,
    backend: ImageBackend,
    out_dir: Path,
    tts: TTS | None = None,
    bgm: Path | None = None,
) -> tuple[Path, Path]:
    ensure_ffmpeg()
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / "work"
    work.mkdir(exist_ok=True)
    tts = tts or TTS(bible)
    clips: list[Path] = []
    accent = bible.world.palette[0] if bible.world.palette else "#FFD54F"
    badge = f"第{ep.chapter}集"

    # 前情提要卡
    if ep.recap and ep.chapter > 1:
        card = work / "recap.png"
        render_title_card(1080, 1920, "前情提要", ep.recap, accent).save(card)
        audio = work / "recap.mp3"
        dur = tts.synth("narrator", ep.recap, audio) + 0.4
        spec = CanvasSpec(shot_id="recap", layout="full_bleed", panels=[], subtitles=[],
                          subtitle_box=Rect(x=0, y=0, w=1, h=1), accent=accent, camera="固定",
                          duration=dur, transition="黑场")
        clips.append(render_shot(spec, card, [], [audio], work / "recap.mp4", dur))

    cover: Path | None = None
    for shot in ep.shots:
        spec = build_canvas(shot, bible)
        base = render_base(spec, backend, work / "panels", badge=badge)
        base_png = work / f"{shot.id}.png"
        base.save(base_png)
        if cover is None:
            cover = out_dir / "cover.png"
            _cover(base, ep.title, accent).save(cover)

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

    # 片尾钩子卡
    end_card = work / "next.png"
    render_title_card(1080, 1920, "下集预告", ep.cliffhanger, accent).save(end_card)
    end_audio = work / "next.mp3"
    dur = tts.synth("narrator", ep.cliffhanger, end_audio) + 0.6
    spec = CanvasSpec(shot_id="next", layout="full_bleed", panels=[], subtitles=[],
                      subtitle_box=Rect(x=0, y=0, w=1, h=1), accent=accent, camera="推", duration=dur,
                      transition="叠化")
    clips.append(render_shot(spec, end_card, [], [end_audio], work / "next.mp4", dur))

    video = concat(clips, out_dir / f"episode_{ep.chapter:03d}.mp4", bgm)
    assert cover is not None
    return video, cover


def _cover(base, title: str, accent: str):
    from PIL import ImageDraw

    from .compose import _hex
    from .images import font

    img = base.copy()
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 1180, 1080, 1500], fill=(0, 0, 0))
    draw.text((540, 1340), title[:16], font=font(84), fill=_hex(accent), anchor="mm",
              stroke_width=5, stroke_fill=(0, 0, 0))
    return img
