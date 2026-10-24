"""命令行入口。

novel2drama run  小说.txt  -w workspace/xxx          一键：切章→分析→分镜→渲染→(发布)
novel2drama ingest|analyze|storyboard|render|publish  分阶段执行
novel2drama serve                                      启动网页上传界面
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
from pathlib import Path

from .pipeline import Workspace
from .styles import PRESETS


def _load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$", line)
        if m and m.group(2) and m.group(1) not in os.environ:
            os.environ[m.group(1)] = m.group(2).strip().strip('"')


def _chapters(spec: str | None) -> list[int] | None:
    """'1-3,5' -> [1,2,3,5]"""
    if not spec:
        return None
    out: list[int] = []
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a), int(b) + 1))
        elif part.strip():
            out.append(int(part))
    return out


def _llm(args):
    from .llm import ClaudeLLM

    return ClaudeLLM(model=args.model, effort=args.effort)


def main(argv: list[str] | None = None) -> None:
    _load_dotenv()
    p = argparse.ArgumentParser(prog="novel2drama", description="小说 → 动漫短剧 → 抖音")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp, novel: bool = False):
        if novel:
            sp.add_argument("novel", help="小说文件 .txt / .epub")
        sp.add_argument("-w", "--workspace", required=not novel, help="工作区目录")
        sp.add_argument("-c", "--chapters", help="只处理指定章节，如 1-3,5")
        sp.add_argument("--model", default=None, help="Claude 模型 id（默认 claude-opus-5-5）")
        sp.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
        sp.add_argument("--threshold", type=int, default=80, help="四项质量分最低阈值，低于则自动重写分镜")
        sp.add_argument("--image-backend", choices=["placeholder", "scenic", "sdwebui"], default=None,
                        help="默认跟随风格预设")
        sp.add_argument("--style", choices=sorted(PRESETS), default=None,
                        help="画面风格：portrait_anime 竖屏动漫 / watercolor_epic 横屏水彩大场景动物角色")
        sp.add_argument("--bgm", type=Path, default=None, help="背景音乐文件")
        sp.add_argument("--force", action="store_true", help="忽略已有分镜重新生成")
        sp.add_argument("--publish", action="store_true", help="真正发布到抖音（默认只生成发布清单）")

    common(sub.add_parser("run", help="一键全流程"), novel=True)
    common(sub.add_parser("ingest", help="读取并切分章节"), novel=True)
    for name, desc in [("analyze", "人物/关系/世界观/章节关联分析"), ("storyboard", "自动分镜 + 质量评审"),
                       ("render", "画布重组 + 配音 + 合成视频"), ("publish", "发布到抖音")]:
        common(sub.add_parser(name, help=desc))
    sp = sub.add_parser("serve", help="网页上传界面")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8000)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(message)s")

    if args.cmd == "serve":
        from .web import serve

        serve(args.host, args.port)
        return

    ws_path = args.workspace or str(Path("workspace") / Path(args.novel).stem)
    ws = Workspace(ws_path)
    only = _chapters(args.chapters)
    if args.style and args.cmd not in ("run", "ingest"):
        ws.set_style(args.style)

    if args.cmd in ("run", "ingest"):
        ws.ingest(args.novel, style=args.style)
        if args.cmd == "ingest":
            for c in ws.chapters():
                print(f"{c.index:>4}  {c.title}  ({len(c.text)} 字)")
            return
    if args.cmd in ("run", "analyze"):
        ws.analyze(_llm(args))
    if args.cmd in ("run", "storyboard"):
        ws.storyboard(_llm(args), only=only, threshold=args.threshold, force=args.force)
    if args.cmd in ("run", "render"):
        ws.render(only=only, image_backend=args.image_backend, bgm=args.bgm)
    if args.cmd in ("run", "publish"):
        results = ws.publish(only=only, dry_run=not args.publish)
        print(json.dumps(results, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
