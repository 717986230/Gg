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

## 配置说明

| 变量 | 说明 |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API Key。剧情分析、分镜和评审都用 Claude，默认模型 `claude-opus-5-5`，可用 `--model` 覆盖 |
| `NOVEL2DRAMA_IMAGE_BACKEND` | `placeholder` 或 `sdwebui`；建议在 SD 里加载动漫底模，并为主角训练 LoRA，一致性更好 |
| `SDWEBUI_URL` | SD WebUI 地址，需要用 `--api` 启动 |
| `NOVEL2DRAMA_TTS` | `edge`（默认）或 `silent` |
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
