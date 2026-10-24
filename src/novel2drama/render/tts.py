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
    used: set[str] = {NARRATOR}
    for c in bible.characters:
        desc = f"{c.voice} {c.appearance} {c.visual_prompt}".lower()
        female = any(h in desc for h in FEMALE_HINTS)
        if any(k in c.voice for k in ("童", "孩", "稚")):
            voice = "zh-CN-XiaoyiNeural" if female else "zh-CN-YunxiaNeural"
        elif not female and any(k in c.voice for k in ("苍老", "年迈", "老者")):
            voice = "zh-CN-YunyeNeural"
        elif not female and any(k in c.voice for k in ("低沉", "沙哑", "浑厚", "威严")):
            voice = "zh-CN-YunjianNeural"
        else:
            pool = FEMALE if female else MALE
            free = [v for v in pool if v not in used] or pool
            voice = free[stable_seed(c.id) % len(free)]
        voices[c.id] = voice
        used.add(voice)
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


async def _edge_save(text: str, voice: str, out: Path) -> None:
    """edge-tts 默认用 certifi 证书、且不读代理环境变量；这里改用系统证书库并显式走 HTTPS_PROXY，
    这样在公司代理 / 云端沙箱等需要自定义 CA 的网络里也能用。"""
    import ssl

    import aiohttp
    import edge_tts

    connector = aiohttp.TCPConnector(ssl=ssl.create_default_context())
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or None
    await edge_tts.Communicate(text, voice, connector=connector, proxy=proxy).save(str(out))


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
                    asyncio.run(_edge_save(text, voice, out))
                except Exception:  # 网络失败时退化为静音，不中断整集渲染
                    _silence(out, max(1.2, len(text) / 4.5))
            else:
                _silence(out, max(1.2, len(text) / 4.5))
        return audio_duration(out)
