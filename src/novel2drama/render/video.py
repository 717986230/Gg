"""视频合成（ffmpeg）。

每个镜头：底图 + 运镜（zoompan 推/拉/摇/移）+ 按台词时间轴叠加字幕层 + 配音拼接 + 转场淡入淡出；
所有镜头再 concat 成一集，输出 1080x1920 / 30fps / H.264 + AAC，满足抖音上传规格。
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ..canvas import CanvasSpec

FPS = 30


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 失败: {' '.join(cmd[:6])}...\n{proc.stderr[-2000:]}")


def ensure_ffmpeg() -> None:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("需要安装 ffmpeg（含 ffprobe）并加入 PATH")


def camera_filter(camera: str, duration: float, w: int, h: int) -> str:
    frames = max(1, int(duration * FPS))
    z = "1.0"
    x = "iw/2-(iw/zoom/2)"
    y = "ih/2-(ih/zoom/2)"
    if camera == "推":
        z = f"1.0+0.18*on/{frames}"
    elif camera == "拉":
        z = f"1.18-0.18*on/{frames}"
    elif camera in ("摇", "移", "跟"):
        z = "1.15"
        x = f"(iw-iw/zoom)*on/{frames}"
    elif camera == "环绕":
        z = "1.12"
        x = f"(iw-iw/zoom)*(0.5+0.5*sin(2*PI*on/{frames}))"
    elif camera == "手持晃动":
        z = "1.08"
        x = "iw/2-(iw/zoom/2)+8*sin(on/3)"
        y = "ih/2-(ih/zoom/2)+6*cos(on/4)"
    # 先放大 2 倍再 zoompan，避免缩放抖动
    return (f"scale={w * 2}:{h * 2},zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={w}x{h}:fps={FPS}")


def render_shot(
    spec: CanvasSpec,
    base_png: Path,
    subtitle_pngs: list[tuple[Path, float, float]],
    audio_clips: list[Path],
    out: Path,
    duration: float,
) -> Path:
    """subtitle_pngs: (png, start, end)。"""
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-t", f"{duration:.2f}", "-i", str(base_png)]
    for png, _, _ in subtitle_pngs:
        cmd += ["-loop", "1", "-t", f"{duration:.2f}", "-i", str(png)]
    for a in audio_clips:
        cmd += ["-i", str(a)]

    filters = [f"[0:v]{camera_filter(spec.camera, duration, spec.width, spec.height)},format=yuv420p[v0]"]
    last = "v0"
    for i, (_, start, end) in enumerate(subtitle_pngs, start=1):
        filters.append(f"[{last}][{i}:v]overlay=0:0:enable='between(t,{start:.2f},{end:.2f})'[v{i}]")
        last = f"v{i}"
    fade_d = 0.25
    if spec.transition in ("叠化", "黑场"):
        filters.append(f"[{last}]fade=t=in:st=0:d={fade_d},fade=t=out:st={max(0, duration - fade_d):.2f}:d={fade_d}[vf]")
        last = "vf"
    elif spec.transition == "闪白":
        filters.append(f"[{last}]fade=t=in:st=0:d=0.15:color=white[vf]")
        last = "vf"

    a0 = 1 + len(subtitle_pngs)
    if audio_clips:
        ins = "".join(f"[{a0 + i}:a]" for i in range(len(audio_clips)))
        filters.append(f"{ins}concat=n={len(audio_clips)}:v=0:a=1,aresample=44100,apad[aout]")
    else:
        filters.append("anullsrc=r=44100:cl=stereo[aout]")
    cmd += [
        "-filter_complex", ";".join(filters),
        "-map", f"[{last}]", "-map", "[aout]",
        "-t", f"{duration:.2f}", "-r", str(FPS),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2",
        str(out),
    ]
    _run(cmd)
    return out


def concat(clips: list[Path], out: Path, bgm: Path | None = None) -> Path:
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{c.resolve()}'\n" for c in clips), encoding="utf-8")
    if bgm and bgm.exists():
        _run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
              "-stream_loop", "-1", "-i", str(bgm),
              "-filter_complex", "[1:a]volume=0.18[b];[0:a][b]amix=inputs=2:duration=first:dropout_transition=2[a]",
              "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
              "-movflags", "+faststart", str(out)])
    else:
        _run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
              "-c", "copy", "-movflags", "+faststart", str(out)])
    return out
