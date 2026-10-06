import json
import shutil
import subprocess
from pathlib import Path

import pytest

from fake_llm import FakeLLM
from novel2drama.analysis.storyboard import storyboard_chapter
from novel2drama.canvas import H, SAFE_BOTTOM, W, build_canvas
from novel2drama.chapters import split_chapters
from novel2drama.llm import strict_schema
from novel2drama.models import Episode
from novel2drama.pipeline import Workspace
from novel2drama.publish.douyin import build_caption, publish_episode

SAMPLE = Path(__file__).parent.parent / "examples" / "青灯渡.txt"


def test_split_chapters_variants():
    text = "书名\n简介\n第一章 开端\n甲\n乙\n第2章：转折\n丙\n第十卷 第三章 高潮\n丁\n番外 后日谈\n戊\nChapter 9 End\n己"
    chs = split_chapters(text)
    assert [c.title for c in chs] == ["第一章 开端", "第2章：转折", "第十卷 第三章 高潮", "番外 后日谈", "Chapter 9 End"]
    assert chs[0].paragraphs == ["甲", "乙"]
    # 正文里出现"第一章"字样的长句不应被误判为标题
    assert len(split_chapters("第一章 a\n他翻开第一章的时候，发现书页早已泛黄，墨迹也模糊得几乎看不清了。")) == 1


def test_split_without_titles_falls_back_to_length():
    chs = split_chapters("\n".join(["字" * 100] * 50), fallback_chars=1000)
    assert len(chs) == 5 and chs[0].title == "第1集"


def test_strict_schema_closes_objects():
    schema = strict_schema(Episode)
    shot = schema["$defs"]["Shot"]
    assert shot["additionalProperties"] is False
    assert set(shot["required"]) == set(shot["properties"])
    assert "minimum" not in json.dumps(schema)


def test_analyze_builds_bible_and_links(tmp_path):
    ws = Workspace(tmp_path / "ws")
    assert len(ws.ingest(SAMPLE)) == 3
    assert ws.meta["title"] == "青灯渡"
    bible, analyses = ws.analyze(FakeLLM())
    assert {c.id for c in bible.characters} == {"shen_yan", "a_heng", "gu_changyuan", "hong_yi"}
    # 中文名引用被解析为 id，关系变化被记录到 history
    rel = bible.relation("shen_yan", "a_heng")
    assert rel.intensity == 5 and rel.history
    assert analyses[0].characters_present[0] == "shen_yan"
    assert analyses[2].links[0].target_chapter == 2
    assert "灯中之眼" in bible.plot_threads


def test_storyboard_retries_until_threshold(tmp_path):
    ws = Workspace(tmp_path / "ws")
    ws.ingest(SAMPLE)
    bible, analyses = ws.analyze(FakeLLM())
    llm = FakeLLM(low_first=True)
    ch = ws.chapters()[0]
    ep, scores, lint = storyboard_chapter(llm, bible, ch, analyses[0], {a.chapter: a for a in analyses})
    assert llm.calls.count("Episode") == 2  # 第一轮评分低，自动重写
    assert scores.min_score >= 80
    assert ep.shots[1].characters == ["shen_yan", "a_heng"]  # 名字被规范成 id
    assert lint.coverage > 0.6


def test_canvas_layout_respects_relationships(tmp_path):
    ws = Workspace(tmp_path / "ws")
    ws.ingest(SAMPLE)
    bible, analyses = ws.analyze(FakeLLM())
    ep = FakeLLM().structured(system="", context="", prompt="为第1章生成", schema=Episode)
    ep.shots[1].characters = ["shen_yan", "a_heng"]
    spec = build_canvas(ep.shots[1], bible)
    # single 布局 + 紧密关系(5) → 自动改成双人同框，且两人站位很近
    assert spec.layout == "two_shot"
    left, right = spec.panels[0].positions
    assert right - left < 0.3
    assert "lean teenage boy" in spec.panels[0].prompt and "xianxia" in spec.panels[0].prompt
    assert spec.subtitle_box.y + spec.subtitle_box.h <= H - SAFE_BOTTOM
    manga = build_canvas(ep.shots[2], bible)
    assert len(manga.panels) == 3
    assert all(p.rect.x + p.rect.w <= W for p in manga.panels)


def test_caption_and_dry_run(tmp_path):
    cap = build_caption("第1集 渡口少年", ["动漫短剧", "#仙侠"], "青灯渡")
    assert cap == "第1集 渡口少年 《青灯渡》动漫短剧 #动漫短剧 #仙侠"
    video = tmp_path / "a.mp4"
    video.write_bytes(b"x")
    assert publish_episode(video, "t", ["a"], "s")["dry_run"] is True


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="需要 ffmpeg")
def test_end_to_end_render(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVEL2DRAMA_TTS", "silent")
    ws = Workspace(tmp_path / "ws")
    ws.ingest(SAMPLE)
    llm = FakeLLM()
    ws.analyze(llm)
    ws.storyboard(llm, only=[1, 2])
    videos = ws.render(only=[2], image_backend="placeholder")
    assert len(videos) == 1 and videos[0].stat().st_size > 10_000
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height", "-of", "csv=p=0", str(videos[0])],
                           capture_output=True, text=True, check=True)
    assert probe.stdout.strip() == "1080,1920"
    assert (ws.output_dir(2) / "cover.png").exists()
    records = ws.publish(dry_run=True)
    assert records and "#动漫短剧" in records[0]["text"]
