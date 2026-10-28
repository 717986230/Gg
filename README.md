# novel2drama · 小说一键转动漫短剧并发布抖音

上传整本小说，自动完成：**分章 → 人物 / 关系 / 世界观 / 章节关联分析 → 自动分镜 → 画布重组 → 配音合成 → 发布抖音**。

```
小说.txt/.epub
   │ chapters.py      识别「第X章/卷/回、序章、番外、Chapter N」，识别不到则按字数分集
   ▼
故事圣经 Story Bible   analysis/analyzer.py + bible.py
   │  · 角色：id、别名、性格、外貌、英文画面锚点 visual_prompt、声线
   │  · 关系图：类型 + 紧密度 1-5 + 逐章演变 history
   │  · 世界观：题材、时代、硬设定 rules、势力、统一画风 art_style、主色调
   │  · 伏笔池：逐章开启 / 回收
   ▼  逐章顺序分析，每章都基于“截至上一章”的圣经 + 前情摘要
章节分析               摘要、关键事件、出场人物、与前文的关联（因果/伏笔回收/人物重逢/关系转折…）、结尾钩子
   ▼
自动分镜 + 质量闭环     analysis/storyboard.py
   │  · 每个镜头都用 source_paragraphs 回指原文段落
   │  · 规则校验：角色/地点合法、原文覆盖率、紧密关系角色是否同框
   │  · Claude 评审四项分：人物关系紧密度 / 世界观贴合度 / 小说还原度 / 章节贴合度
   │  · 任一项低于阈值（默认 80）→ 带着问题清单重写，最多 3 轮
   ▼
画布重组               canvas.py
   │  · 9:16 版式：单人 / 双人同框 / 上下分屏 / 左右分屏 / 三格漫画 / 大场景+特写小窗 / 全幅
   │  · 关系紧密度 → 双人站位距离与朝向；敌对关系 → 对峙构图
   │  · 提示词 = 全剧画风 + 地点锚点 + 角色外貌锚点 + 镜头描述；角色特写用固定种子，保持长相一致
   │  · 字幕避开抖音顶部、底部文案区和右侧按钮栏
   ▼
渲染                   render/
   │  · 图像：placeholder（离线占位）或 sdwebui（Stable Diffusion WebUI / Forge API）
   │  · 配音：edge-tts，每个角色按声线固定分配音色
   │  · ffmpeg：运镜（推/拉/摇/移/环绕/手持）、逐句字幕、转场、前情提要卡、下集预告卡、BGM
   ▼
抖音发布               publish/douyin.py（抖音开放平台：上传/分片上传 → 创建视频）
```

## 安装

```bash
pip install -e '.[tts,epub,web,dev]'   # 需要 Python ≥ 3.10，以及系统里的 ffmpeg
cp .env.example .env                    # 填入 ANTHROPIC_API_KEY 等
```

## 使用

```bash
# 一键全流程（默认只生成抖音发布清单，不真正发布）
novel2drama run examples/青灯渡.txt -w workspace/qingdeng

# 分阶段执行（每一步都会落盘，可以人工修改 JSON 后继续）
novel2drama ingest     examples/青灯渡.txt -w workspace/qingdeng
novel2drama analyze    -w workspace/qingdeng
novel2drama storyboard -w workspace/qingdeng -c 1-3 --threshold 85
novel2drama render     -w workspace/qingdeng -c 1-3 --image-backend sdwebui --bgm bgm.mp3
novel2drama publish    -w workspace/qingdeng -c 1 --publish   # 真正发布

# 网页上传界面
novel2drama serve   # http://127.0.0.1:8000
```

工作区结构：

```
workspace/<项目>/
  chapters.json        切章结果
  bible.json           故事圣经（可手工修订人物外貌、关系、画风，后续分镜自动生效）
  analyses.json        逐章分析与跨章节关联
  episodes/E001.json   分镜 + 四项质量评分 + 规则校验结果
  output/E001/         episode_001.mp4、cover.png、中间素材
  publish.json         发布记录（已发布的集不会重复发）
```

## 画面风格

用 `--style` 选择风格；风格会写入工作区，分析、分镜、渲染各阶段都按它执行：

| 风格 | 画幅 | 特点 |
|---|---|---|
| `portrait_anime`（默认） | 竖屏 1080×1920 | 赛璐璐动漫，近景、特写多，双人同框/分屏表现人物关系 |
| `watercolor_epic` | 横屏 1280×720 | 水彩加墨线；大场景、人物小、第三视角；角色都设计成拟人动物；远景为主，运镜像长卷缓缓展开 |

`watercolor_epic` 风格的具体规则：
- 分析阶段：要求模型按性格、身份、绰号给每个角色选动物（威猛者用虎豹、文雅者用鹤鹿、傲慢的官员用鹅等），英文外貌描述以动物名开头。
- 分镜阶段：至少 70% 是远景镜头，禁用主观镜头和特写，规则检查不过会自动重写。
- 画布阶段：双人、分屏、小窗等布局统一改为全幅大景，人物按景别缩小，偏向画面一侧站位。
- 渲染阶段：默认使用 `scenic` 后端。它根据镜头描述里的关键词，用程序画出山水、宫殿、道观、殿内、石碑、黑气、金光、星火等场景和小动物人物，不需要显卡。最后每帧再经过一遍水彩墨线滤镜，统一画风。接入 SD 后用 `--image-backend sdwebui`，滤镜照样生效。

### 示例：《水浒传》第一回（动物版）

`examples/shuihu/` 里有第一回的白话改写稿，以及一个已经做好的工作区：故事圣经和 18 个镜头的分镜都是人工撰写的，没经过模型生成和评分。不需要 API Key，可以直接渲染：

```bash
novel2drama render -w examples/shuihu/ws   # 输出 examples/shuihu/ws/output/E001/episode_001.mp4
```

角色设定：洪太尉是穿绯袍、戴乌纱帽的胖白鹅；宋仁宗是穿龙袍的白狮；上清宫住持是老龟道长；张天师化身为倒骑黄牛、吹铁笛的小白鹤道童；山中大虫和雪白大蛇按原文保留为精怪。

想让模型从头分析整本书：

```bash
novel2drama run 水浒传.txt -w workspace/shuihu --style watercolor_epic -c 1-3
```

### 示例：格林童话《不莱梅的城市乐手》（德语配音，给 Vido 用）

`examples/grimm/` 是按格林童话第 27 篇（公版）手写的德语分镜：驴、狗、猫、公鸡是四脚着地的真动物，
强盗是穿袍子的狐狸；每句台词都带中文、英文译文（`translations`）。工作区的 `meta.json` 里
`"language": "de"`、`"clean": true`，所以渲染出来的是「干净版」：不烧字幕、不加片头片尾卡，
另外输出 `episode_001.{de,zh,en}.srt` 和 `episode_001.cues.json`，交给自带字幕的播放器显示。

```bash
pip install -e '.[piper]'
# 下载音色（都来自 sherpa-onnx 的 tts-models 发布页）：thorsten-medium、karlsson-low、ramona-low、kerstin-low、eva_k-x_low
NOVEL2DRAMA_TTS=piper NOVEL2DRAMA_PIPER_DIR=./piper-voices \
NOVEL2DRAMA_PIPER_NARRATOR=thorsten-medium@0.92 NOVEL2DRAMA_LINE_GAP=0.7 \
novel2drama render -w examples/grimm/ws
```

角色在故事圣经里用 `voice` 指定音色，如 `"piper:karlsson-low@0.95"`。不要用 pavoque 音色，它是 CC BY-NC-SA，不能商用。

## 配置说明

| 变量 | 说明 |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API Key。剧情分析、分镜和评审都用 Claude，默认模型 `claude-opus-5-5`，可用 `--model` 覆盖 |
| `NOVEL2DRAMA_IMAGE_BACKEND` | `placeholder`、`scenic` 或 `sdwebui`（不设置时跟随风格）；建议在 SD 里加载动漫底模，并为主角训练 LoRA，一致性更好 |
| `SDWEBUI_URL` | SD WebUI 地址，需要用 `--api` 启动 |
| `NOVEL2DRAMA_TTS` | `edge`（默认）、`piper`（离线，德语等）或 `silent` |
| `NOVEL2DRAMA_PIPER_DIR` | Piper 音色目录，里面每个音色一个 `vits-piper-*` 文件夹（sherpa-onnx 发布的模型包解开即可） |
| `NOVEL2DRAMA_PIPER_NARRATOR` | 旁白音色，默认 `thorsten-medium`，可带语速如 `thorsten-medium@0.92` |
| `NOVEL2DRAMA_LINE_GAP` | 台词之间停顿的秒数，默认 0.15；给学语言的人看建议 0.6–0.8 |
| `NOVEL2DRAMA_FONT` | 字幕字体路径（默认自动查找文泉驿 / 思源黑体 / 苹方 / 微软雅黑） |
| `DOUYIN_*` | 抖音开放平台凭证，见下文 |

### Claude 调用细节
- 使用结构化输出（`output_config.format` + JSON Schema），并用 Pydantic 校验，失败自动重试；
- 系统提示和故事圣经放在请求前缀并打 `cache_control`，逐章调用时可以命中提示缓存；
- 开启了服务端 fallback（`fallbacks: "default"`）：请求被安全分类器拒绝时，服务端会自动换模型重试。如不需要，可在 `llm.py` 中删除；
- 长篇小说逐章增量分析，不会一次把整本书塞进上下文，所以几千章也能处理（按章计费，耗时与章数成正比）。

### 抖音发布
1. 在[抖音开放平台](https://developer.open-douyin.com)创建应用，申请视频发布能力（`video.create` 等 scope）；
2. 用目标账号完成 OAuth 授权，拿到 `access_token`、`refresh_token`、`open_id`，写入 `.env`；
3. `novel2drama publish -w ... --publish`。不加 `--publish` 时只输出标题、话题等发布清单。

> 开放平台的接口路径和字段可能调整，所有路径集中在 `publish/douyin.py` 的 `ENDPOINTS` 中。首次正式发布前，请对照官方文档核对一遍。
> 改编并发布他人作品需要取得版权方授权；平台对 AI 生成内容有标注要求，请遵守。

## 测试

```bash
pytest -q   # 离线跑：假 LLM + 占位图 + 静音配音，包含一集完整视频的渲染（需要 ffmpeg）
```

`examples/青灯渡.txt` 是为演示写的原创三章短篇。
