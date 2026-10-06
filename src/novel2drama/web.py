"""极简网页：上传整本小说 → 后台跑全流程 → 查看进度、分镜评分、下载成片。

pip install 'novel2drama[web]' && novel2drama serve
"""
from __future__ import annotations

import html
import re
import threading
import traceback
from pathlib import Path

ROOT = Path("workspace")
JOBS: dict[str, dict] = {}


def _run_job(name: str, novel: Path, publish: bool) -> None:
    from .llm import ClaudeLLM
    from .pipeline import Workspace

    job = JOBS[name]
    try:
        ws = Workspace(ROOT / name)
        job["stage"] = "切分章节"
        ws.ingest(novel)
        llm = ClaudeLLM()
        job["stage"] = "分析人物关系与世界观"
        ws.analyze(llm)
        job["stage"] = "自动分镜与质量评审"
        ws.storyboard(llm)
        job["stage"] = "画布重组与视频合成"
        ws.render()
        job["stage"] = "发布抖音" if publish else "生成发布清单"
        ws.publish(dry_run=not publish)
        job["stage"] = "完成"
    except Exception:  # noqa: BLE001 - 把错误展示在页面上
        job["stage"] = "失败"
        job["error"] = traceback.format_exc()


def create_app():
    from fastapi import FastAPI, File, Form, UploadFile
    from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

    from .pipeline import Workspace

    app = FastAPI(title="novel2drama")

    @app.get("/", response_class=HTMLResponse)
    def index():
        rows = "".join(
            f"<li><a href='/p/{html.escape(n)}'>{html.escape(n)}</a> — {html.escape(j['stage'])}</li>"
            for n, j in JOBS.items()
        )
        return f"""<!doctype html><meta charset=utf-8><title>小说转动漫短剧</title>
<body style="font-family:sans-serif;max-width:720px;margin:40px auto">
<h1>小说 → 动漫短剧 → 抖音</h1>
<form action=/upload method=post enctype=multipart/form-data>
<p><input type=file name=file accept=".txt,.epub" required></p>
<p><label><input type=checkbox name=publish value=1> 渲染完成后直接发布到抖音（需配置凭证）</label></p>
<button>上传并开始</button></form><h2>任务</h2><ul>{rows}</ul></body>"""

    @app.post("/upload")
    async def upload(file: UploadFile = File(...), publish: str = Form("")):
        name = re.sub(r"[^\w一-鿿-]", "_", Path(file.filename or "novel").stem)[:40] or "novel"
        target = ROOT / name
        target.mkdir(parents=True, exist_ok=True)
        novel = target / f"source{Path(file.filename or '.txt').suffix}"
        novel.write_bytes(await file.read())
        JOBS[name] = {"stage": "排队中"}
        threading.Thread(target=_run_job, args=(name, novel, bool(publish)), daemon=True).start()
        return RedirectResponse(f"/p/{name}", status_code=303)

    @app.get("/p/{name}", response_class=HTMLResponse)
    def project(name: str):
        ws = Workspace(ROOT / name)
        job = JOBS.get(name, {"stage": "未知"})
        bible = ws.bible()
        rows = []
        for ch in ws.chapters():
            found = ws.episode(ch.index)
            score = ""
            if found and found[1]:
                s = found[1]
                score = (f"关系 {s.relationship_tightness} · 世界观 {s.world_consistency} · "
                         f"还原 {s.novel_fidelity} · 章节 {s.chapter_alignment}")
            video = ws.output_dir(ch.index) / f"episode_{ch.index:03d}.mp4"
            link = f"<a href='/v/{html.escape(name)}/{ch.index}'>下载</a>" if video.exists() else ""
            rows.append(f"<tr><td>{ch.index}</td><td>{html.escape(ch.title)}</td><td>{score}</td><td>{link}</td></tr>")
        chars = ""
        if bible:
            chars = "".join(f"<li><b>{html.escape(c.name)}</b>（{html.escape(c.role)}）{html.escape(c.personality)}</li>"
                            for c in bible.characters)
            rels = "".join(f"<li>{html.escape(r.source)} ↔ {html.escape(r.target)}：{html.escape(r.type)} "
                           f"({'★' * r.intensity})</li>" for r in bible.relationships)
            chars = f"<h2>人物</h2><ul>{chars}</ul><h2>关系</h2><ul>{rels}</ul>"
        err = f"<pre style='color:#b00'>{html.escape(job.get('error', ''))}</pre>" if job.get("error") else ""
        refresh = "" if job["stage"] in ("完成", "失败") else "<meta http-equiv=refresh content=10>"
        return f"""<!doctype html><meta charset=utf-8>{refresh}<title>{html.escape(name)}</title>
<body style="font-family:sans-serif;max-width:960px;margin:40px auto"><a href=/>← 返回</a>
<h1>{html.escape(name)}</h1><p>当前阶段：<b>{html.escape(job['stage'])}</b></p>{err}
<table border=1 cellpadding=6 style="border-collapse:collapse"><tr><th>集</th><th>章节</th><th>质量评分</th><th>成片</th></tr>
{''.join(rows)}</table>{chars}</body>"""

    @app.get("/v/{name}/{n}")
    def video(name: str, n: int):
        path = Workspace(ROOT / name).output_dir(n) / f"episode_{n:03d}.mp4"
        return FileResponse(path, media_type="video/mp4", filename=path.name)

    return app


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    import uvicorn

    uvicorn.run(create_app(), host=host, port=port)
