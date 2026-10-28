"""角色配音。每个角色根据故事圣经里的声线描述固定分配一个音色，全剧不变。"""
from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

import numpy as np

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


class Piper:
    """离线 Piper 语音（sherpa-onnx 运行时），用于德语等 edge-tts 之外或不能联网的场合。

    模型放在 NOVEL2DRAMA_PIPER_DIR 下，每个音色一个目录，就是 sherpa-onnx 发布的
    vits-piper-<语言>-<音色> 压缩包解开后的样子（.onnx + tokens.txt + espeak-ng-data）。
    角色在故事圣经里用 voice 字段点名音色：「piper:karlsson-low」，可加语速「piper:karlsson-low@1.1」；
    旁白用 NOVEL2DRAMA_PIPER_NARRATOR（默认 thorsten-medium，也可带语速「thorsten-medium@0.9」），
    没点名的角色也用它。
    """

    def __init__(self, root: str | Path | None = None, narrator: str | None = None):
        self.root = Path(root or os.environ.get("NOVEL2DRAMA_PIPER_DIR", "piper-voices"))
        self.narrator = narrator or os.environ.get("NOVEL2DRAMA_PIPER_NARRATOR", "thorsten-medium")
        self._engines: dict[str, object] = {}

    def _model_dir(self, name: str) -> Path:
        hits = sorted(d for d in self.root.glob(f"*{name}") if d.is_dir() and list(d.glob("*.onnx")))
        if not hits:
            raise FileNotFoundError(f"在 {self.root} 里找不到 Piper 音色 {name}")
        return hits[0]

    def _engine(self, name: str):
        if name not in self._engines:
            import sherpa_onnx

            d = self._model_dir(name)
            vits = sherpa_onnx.OfflineTtsVitsModelConfig(model=str(next(d.glob("*.onnx"))),
                                                         tokens=str(d / "tokens.txt"),
                                                         data_dir=str(d / "espeak-ng-data"))
            config = sherpa_onnx.OfflineTtsConfig(model=sherpa_onnx.OfflineTtsModelConfig(vits=vits, num_threads=2))
            self._engines[name] = sherpa_onnx.OfflineTts(config)
        return self._engines[name]

    def voice_for(self, spec: str | None) -> tuple[str, float]:
        """「piper:karlsson-low@1.1」→（karlsson-low, 1.1）；不是 piper 音色就用旁白。"""
        if spec and spec.startswith("piper:"):
            name, _, speed = spec[len("piper:"):].partition("@")
            return name.strip(), float(speed or 1.0)
        name, _, speed = self.narrator.partition("@")
        return name, float(speed or 1.0)

    def save(self, text: str, spec: str | None, out: Path) -> None:
        import wave

        name, speed = self.voice_for(spec)
        audio = self._engine(name).generate(text, sid=0, speed=speed)
        wav = out.with_suffix(".wav")
        pcm = (np.clip(np.asarray(audio.samples), -1, 1) * 32767).astype("<i2")
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(audio.sample_rate)
            w.writeframes(pcm.tobytes())
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), "-c:a", "libmp3lame", "-q:a", "3",
                        str(out)], check=True)
        wav.unlink()


class TTS:
    def __init__(self, bible: StoryBible, engine: str | None = None):
        self.voices = assign_voices(bible)
        self.engine = engine or os.environ.get("NOVEL2DRAMA_TTS", "edge")
        if self.engine == "edge":
            try:
                import edge_tts  # noqa: F401
            except ImportError:
                self.engine = "silent"
        if self.engine == "piper":
            self.piper = Piper()
            self.voices = {"narrator": None, **{c.id: c.voice for c in bible.characters}}

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
            elif self.engine == "piper":
                self.piper.save(text, self.voices.get(speaker), out)
            else:
                _silence(out, max(1.2, len(text) / 4.5))
        return audio_duration(out)
