from pathlib import Path

from novel2drama.analysis.storyboard import lint_episode
from novel2drama.canvas import build_canvas
from novel2drama.pipeline import Workspace
from novel2drama.render.scenic import ScenicBackend, parse_figure, parse_scene
from novel2drama.render.stylize import watercolor_line
from novel2drama.styles import LANDSCAPE_SMALL, get_style

SHUIHU = Path(__file__).parent.parent / "examples" / "shuihu" / "ws"


def test_parse_figure_animals():
    f = parse_figure("plump goose official, black winged hat, crimson official robe")
    assert (f.species, f.hat, f.robe) == ("goose", "official", (170, 40, 45))
    f = parse_figure("white lion emperor, golden crown, yellow dragon robe")
    assert f.species == "lion" and f.hat == "crown" and f.robe == (220, 175, 60)  # 取紧挨 robe 的颜色
    f = parse_figure("little white crane taoist boy riding a yellow ox, azure robe, iron flute")
    assert f.species == "crane" and f.mount == "ox" and f.child
    assert parse_figure("huge wild tiger, white forehead, mountain beast").beast


def test_parse_scene_keywords_use_word_boundaries():
    s = parse_scene("a goose climbing the winding mountain path, pitch dark clouds")
    assert s.setting == "path"
    assert "wind" not in s.weather and "pit" not in s.effects
    s = parse_scene("inside the dark hall, a stone tablet carved with 「遇洪而开」, black smoke")
    assert s.setting == "interior" and s.tablet_text == "遇洪而开" and "smoke" in s.effects


def test_watercolor_epic_canvas_is_wide_and_small_figures():
    ws = Workspace(SHUIHU)
    style = ws.style
    assert style.name == "watercolor_epic" and style.canvas == LANDSCAPE_SMALL
    ep = ws.episode(1)[0]
    for shot in ep.shots:
        spec = build_canvas(shot, ws.bible(), style)
        assert (spec.width, spec.height) == (1280, 720)
        assert spec.layout in ("full_bleed", "manga_3")  # 大场景风格不出现分屏/特写小窗
        assert all(p.figure_scale <= 0.2 for p in spec.panels)  # 人物小
        assert spec.subtitle_box.y > spec.height * 0.8  # 字幕贴底，不压住人物站位线
    lint = lint_episode(ep, ws.chapters()[0], ws.bible(), ws.analyses()[0], style)
    assert lint.errors == [] and lint.coverage == 1.0


def test_scenic_render_and_filter(tmp_path):
    ws = Workspace(SHUIHU)
    shot = ws.episode(1)[0].shots[12]  # 殿内石碑
    spec = build_canvas(shot, ws.bible(), "watercolor_epic")
    img = ScenicBackend().generate(spec.panels[0], 320, 180)
    assert img.size == (320, 180)
    out = watercolor_line(img, seed=1)
    assert out.size == img.size
    assert out.tobytes() == watercolor_line(img, seed=1).tobytes()  # 同一种子结果稳定


def test_wide_style_lint_flags_closeups(tmp_path):
    ws = Workspace(SHUIHU)
    ep = ws.episode(1)[0].model_copy(deep=True)
    for s in ep.shots[:8]:
        s.shot_type = "特写"
    errors = lint_episode(ep, ws.chapters()[0], ws.bible(), ws.analyses()[0], get_style("watercolor_epic")).errors
    assert any("70%" in e for e in errors) and any("第三视角" in e for e in errors)
