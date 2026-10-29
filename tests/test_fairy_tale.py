"""德语童话（格林）那一套：真动物画法、叠罗汉、Piper 音色解析、干净版成片和字幕导出。"""
import json
import shutil
from pathlib import Path

from novel2drama.canvas import build_canvas
from novel2drama.pipeline import Workspace
from novel2drama.render.scenic import ScenicBackend, parse_figure, parse_scene
from novel2drama.render.tts import Piper

GRIMM = Path(__file__).parent.parent / "examples" / "grimm" / "ws"


def test_parse_real_animals_and_european_settings():
    for prompt, species in [("old gray donkey, real animal on four legs", "donkey"),
                            ("old gray cat, real animal on four legs", "cat"),
                            ("white rooster with red comb, real animal", "rooster")]:
        f = parse_figure(prompt)
        assert f.species == species and f.beast  # 不穿袍子、四脚着地
    assert not parse_figure("fox robber in a dark gray robe").beast
    assert parse_scene("village farmyard with farmhouses").setting == "village"
    assert parse_scene("country road through green meadow").setting == "meadow"
    s = parse_scene("night, lonely cottage in the forest with a lit window")
    assert (s.setting, s.time, s.lit) == ("cottage", "night", True)
    assert not parse_scene("lonely cottage in the forest, night").lit
    assert parse_scene("lonely cottage in the forest, night").crowd == ""  # 「强盗的房子」不该画出一群强盗
    assert parse_scene("night, the robber band fleeing into the dark forest").crowd == "robbers"


def test_every_grimm_shot_renders():
    ws = Workspace(GRIMM)
    for shot in ws.episode(1)[0].shots:  # 覆盖左右朝向、叠罗汉、夜景
        spec = build_canvas(shot, ws.bible(), ws.style)
        assert ScenicBackend().generate(spec.panels[0], 192, 108).size == (192, 108)


def test_piper_voice_spec(monkeypatch):
    monkeypatch.setenv("NOVEL2DRAMA_PIPER_NARRATOR", "thorsten-medium@0.9")
    p = Piper(root="unused")
    assert p.voice_for("piper:karlsson-low@1.1") == ("karlsson-low", 1.1)
    assert p.voice_for("piper:kerstin-low") == ("kerstin-low", 1.0)
    assert p.voice_for("苍老温和") == ("thorsten-medium", 0.9)  # 不是 piper 音色就用旁白
    assert p.voice_for(None) == ("thorsten-medium", 0.9)


def test_clean_render_writes_subtitles(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVEL2DRAMA_TTS", "silent")
    ws_dir = tmp_path / "ws"
    shutil.copytree(GRIMM, ws_dir, ignore=shutil.ignore_patterns("output"))
    ws = Workspace(ws_dir)
    episode = json.loads((ws_dir / "episodes" / "E001.json").read_text(encoding="utf-8"))
    episode["episode"]["shots"] = episode["episode"]["shots"][:2]  # 两个镜头够用了
    (ws_dir / "episodes" / "E001.json").write_text(json.dumps(episode, ensure_ascii=False), encoding="utf-8")

    video = ws.render(image_backend="placeholder")[0]
    out = video.parent
    assert not (out / "work" / "head.mp4").exists() and not (out / "work" / "badge.png").exists()
    cues = json.loads((out / "episode_001.cues.json").read_text(encoding="utf-8"))
    assert set(cues) == {"de", "zh", "en"}  # 配音语言来自 meta.json 的 language
    assert len(cues["de"]) == len(cues["zh"]) == 4
    assert cues["de"][0]["text"].startswith("Die Bremer Stadtmusikanten")
    assert all(a["end"] <= b["start"] for a, b in zip(cues["de"], cues["de"][1:]))
    srt = (out / "episode_001.zh.srt").read_text(encoding="utf-8")
    assert srt.startswith("1\n00:00:00,200 --> ") and "从前有个人" in srt
