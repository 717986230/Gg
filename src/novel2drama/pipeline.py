"""端到端流水线 + 工作区持久化（每个阶段都落盘，可断点续跑、可人工修改后重跑）。

workspace/<项目>/
  chapters.json          切章结果
  bible.json             故事圣经（人物、关系、地点、世界观）
  analyses.json          逐章分析与跨章节关联
  episodes/E001.json     分镜 + 质量评分
  output/E001/           成片 mp4、封面、中间素材
  publish.json           发布记录
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import TypeAdapter

from .analysis.analyzer import analyze_novel
from .analysis.storyboard import storyboard_chapter
from .chapters import guess_title, read_text, split_chapters
from .llm import LLM
from .models import Chapter, ChapterAnalysis, Episode, QualityScores, StoryBible
from .styles import StylePreset, get_style

log = logging.getLogger("novel2drama")


class Workspace:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "episodes").mkdir(exist_ok=True)
        (self.root / "output").mkdir(exist_ok=True)

    # ---------------------------------------------------------- 读写
    def _write(self, name: str, data) -> None:
        path = self.root / name
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)

    def _read(self, name: str):
        path = self.root / name
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    @property
    def meta(self) -> dict:
        return self._read("meta.json") or {}

    @property
    def style(self) -> StylePreset:
        return get_style(self.meta.get("style"))

    def set_style(self, name: str) -> None:
        get_style(name)  # 校验
        self._write("meta.json", {**self.meta, "style": name})

    def chapters(self) -> list[Chapter]:
        return TypeAdapter(list[Chapter]).validate_python(self._read("chapters.json") or [])

    def bible(self) -> StoryBible | None:
        data = self._read("bible.json")
        return StoryBible.model_validate(data) if data else None

    def analyses(self) -> list[ChapterAnalysis]:
        return TypeAdapter(list[ChapterAnalysis]).validate_python(self._read("analyses.json") or [])

    def episode(self, n: int) -> tuple[Episode, QualityScores | None] | None:
        data = self._read(f"episodes/E{n:03d}.json")
        if not data:
            return None
        scores = QualityScores.model_validate(data["scores"]) if data.get("scores") else None
        return Episode.model_validate(data["episode"]), scores

    def save_episode(self, ep: Episode, scores: QualityScores | None, lint: dict | None = None) -> None:
        self._write(f"episodes/E{ep.chapter:03d}.json", {
            "episode": ep.model_dump(),
            "scores": scores.model_dump() if scores else None,
            "lint": lint,
        })

    def output_dir(self, n: int) -> Path:
        return self.root / "output" / f"E{n:03d}"

    # ---------------------------------------------------------- 阶段
    def ingest(self, novel_path: str | Path, style: str | None = None) -> list[Chapter]:
        text = read_text(novel_path)
        chapters = split_chapters(text)
        self._write("meta.json", {"title": guess_title(novel_path, text), "source": str(novel_path),
                                  "chapters": len(chapters), "chars": len(text),
                                  "style": style or self.meta.get("style") or get_style(None).name})
        self._write("chapters.json", [c.model_dump() for c in chapters])
        log.info("切分出 %d 章", len(chapters))
        return chapters

    def analyze(self, llm: LLM) -> tuple[StoryBible, list[ChapterAnalysis]]:
        chapters = self.chapters()
        if not chapters:
            raise RuntimeError("请先执行 ingest")
        analyses = self.analyses()

        def checkpoint(bible: StoryBible, a: ChapterAnalysis) -> None:
            analyses.append(a)
            self._write("bible.json", bible.model_dump())
            self._write("analyses.json", [x.model_dump() for x in analyses])
            log.info("第%d章分析完成：%d 角色 / %d 关系 / %d 关联", a.chapter, len(bible.characters),
                     len(bible.relationships), len(a.links))

        bible, _ = analyze_novel(llm, self.meta.get("title", self.root.name), chapters,
                                 bible=self.bible(), done=list(analyses), on_chapter=checkpoint, style=self.style)
        self._write("bible.json", bible.model_dump())
        return bible, self.analyses()

    def storyboard(self, llm: LLM, only: list[int] | None = None, threshold: int = 80,
                   force: bool = False) -> list[Episode]:
        bible = self.bible()
        if bible is None:
            raise RuntimeError("请先执行 analyze")
        history = {a.chapter: a for a in self.analyses()}
        episodes = []
        for ch in self.chapters():
            if only and ch.index not in only:
                continue
            if ch.index not in history:
                log.warning("第%d章尚未分析，跳过", ch.index)
                continue
            if not force and (existing := self.episode(ch.index)):
                episodes.append(existing[0])
                continue
            ep, scores, lint = storyboard_chapter(llm, bible, ch, history[ch.index], history, threshold=threshold,
                                                  style=self.style)
            self.save_episode(ep, scores, {"errors": lint.errors, "coverage": lint.coverage,
                                           "pair_coverage": lint.pair_coverage})
            log.info("第%d集分镜：%d 镜 %.0fs｜关系%d 世界观%d 还原%d 章节%d", ch.index, len(ep.shots), ep.duration,
                     scores.relationship_tightness, scores.world_consistency, scores.novel_fidelity,
                     scores.chapter_alignment)
            episodes.append(ep)
        return episodes

    def render(self, only: list[int] | None = None, image_backend: str | None = None,
               bgm: Path | None = None, clean: bool | None = None) -> list[Path]:
        """clean 不传时跟随工作区 meta.json 的 clean；配音语言取 meta.json 的 language（默认 zh）。"""
        from .render.episode import render_episode
        from .render.images import get_backend
        from .render.tts import TTS

        bible = self.bible()
        if bible is None:
            raise RuntimeError("请先执行 analyze")
        style = self.style
        backend = get_backend(image_backend or style.default_backend)
        tts = TTS(bible)
        videos = []
        for ch in self.chapters():
            if only and ch.index not in only:
                continue
            found = self.episode(ch.index)
            if not found:
                continue
            video, _ = render_episode(found[0], bible, backend, self.output_dir(ch.index), tts=tts, bgm=bgm,
                                      style=style, clean=self.meta.get("clean", False) if clean is None else clean,
                                      language=self.meta.get("language", "zh"))
            log.info("第%d集已渲染：%s", ch.index, video)
            videos.append(video)
        return videos

    def publish(self, only: list[int] | None = None, dry_run: bool = True) -> list[dict]:
        from .publish.douyin import publish_episode

        records = self._read("publish.json") or {}
        results = []
        series = self.meta.get("title", "")
        for ch in self.chapters():
            if only and ch.index not in only:
                continue
            found = self.episode(ch.index)
            video = self.output_dir(ch.index) / f"episode_{ch.index:03d}.mp4"
            if not found or not video.exists():
                continue
            key = str(ch.index)
            if not dry_run and records.get(key, {}).get("item_id"):
                log.info("第%d集已发布过，跳过", ch.index)
                continue
            ep = found[0]
            title = f"第{ch.index}集 {ep.title}"
            result = publish_episode(video, title, ep.hashtags, series, dry_run=dry_run)
            records[key] = result
            self._write("publish.json", records)
            results.append(result)
        return results
