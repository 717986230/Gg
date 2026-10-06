"""角色配音。每个角色根据故事圣经里的声线描述固定分配一个音色，全剧不变。"""
from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

from ..canvas import stable_seed
from ..models import StoryBible

NARRATOR = "zh-CN-YunyangNeural"
MALE = ["zh-CN-YunxiNeural", "zh-CN-YunjianNeural", "zh-CN-YunxiaNeural", "zh-CN-YunyeNeural"]
FEMALE = ["zh-CN-XiaoxiaoNeural", "zh-CN-XiaoyiNeural", "zh-CN-XiaohanNeural", "zh-CN-XiaomoNeural"]
FEMALE_HINTS = ("女", "少女", "御姐", "萝莉", "姐", "妹", "娘", "母", "她", "female", "girl", "woman")


def assign_voices(bible: StoryBible) -> dict[str, str]:
    voices = {"narrator": NARRATOR}
    for c in bible.characters:
        desc = f"{c.voice} {c.appearance} {c.visual_prompt}".lower()
        pool = FEMALE if any(h in desc for h in FEMALE_HINTS) else MALE
        if any(k in c.voice for k in ("低沉", "沙哑", "苍老", "浑厚")) and pool is MALE:
            voices[c.id] = "zh-CN-YunjianNeural"
        else:
            voices[c.id] = pool[stable_seed(c.id) % len(pool)]
    return voices


def audio_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip() or 0)


def _silence(path: Path, seconds: float) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
         "-t", f"{seconds:.2f}", "-c:a", "libmp3lame", str(path)],
        check=True,
    )


class TTS:
    def __init__(self, bible: StoryBible, engine: str | None = None):
        self.voices = assign_voices(bible)
        self.engine = engine or os.environ.get("NOVEL2DRAMA_TTS", "edge")
        if self.engine == "edge":
            try:
                import edge_tts  # noqa: F401
            except ImportError:
                self.engine = "silent"

    def synth(self, speaker: str, text: str, out: Path) -> float:
        """生成一句台词音频，返回时长（秒）。"""
        if not out.exists():
            if self.engine == "edge":
                import edge_tts

                voice = self.voices.get(speaker, NARRATOR)
                try:
                    asyncio.run(edge_tts.Communicate(text, voice).save(str(out)))
                except Exception:  # 网络失败时退化为静音，不中断整集渲染
                    _silence(out, max(1.2, len(text) / 4.5))
            else:
                _silence(out, max(1.2, len(text) / 4.5))
        return audio_duration(out)
