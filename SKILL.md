---
name: "douyin-download"
description: "抖音作品获取 v3.1：分享文本/短链/ID 单入口解析，按需下载图/视频/实况/文案/BGM，带元数据、SHA256 与显式状态，跨 agent"
---

# douyin-download v3.1 — 抖音作品获取（需求驱动）

用户发抖音分享链接/口令/ID 时，**按用户要的东西**提取：无码原图 / 视频（含图文里的实况照片）/ 文案（标题正文话题互动数据）/ 音频（BGM）。单入口 `scripts/dy.py`，stdout 只输出一个 JSON manifest。

> 跨 agent 通用（Claude Code / Codex / Cursor / OpenClaw 等 Agent Skills 宿主）；安装、依赖与首次登录准备见仓库 `README.md`。

## 第 0 步：先判定需求（决定抓什么）

| 用户说法 | 需求 | 交付 |
|---|---|---|
| "下载这个" / "把这个下下来" | **媒体**（图或视频，能给啥给啥） | 文件走 `MEDIA:` 附件 |
| "要图" / "配图" / "做封面" | **图片** | 原图（自动换 q75 全尺寸模板） |
| "要视频" / "这是视频啊" | **视频**（note 里查实况照片） | mp4，必要时长宽 |
| "文案" / "他说了什么" / "抄这段" | **文案** | **正文直接贴在消息里**，不发文件 |
| "音乐" / "BGM 叫什么" | **音频/曲名** | mp3 或曲名 |
| 需求不明且内容是文字向（口播/图文长文） | 先给文案 + 问要不要媒体 | 文案优先 |

> 默认原则：先在 fetch 时用 `--want` 把可能要的都拿上（反正按需下载），不要默认"图文=只要图"。

## 前置：环境与 doctor（30 秒）

```bash
cd <skill 目录>
python3 scripts/dy.py doctor          # 全部 required=true 通过再干活
```

- 依赖：python3.10+、`websockets`、`curl`、`ffprobe`（随 ffmpeg）；`yt-dlp` 可选。缺 websockets：`pip install websockets`。
- **需要一个已登录抖音的浏览器挂在 CDP 端口**（默认 `127.0.0.1:18800`）。本 skill 从不自启/重启浏览器：
  - OpenClaw 环境：`browserctl ensure`，等 10 秒重试一次；仍失败告知用户。
  - 其他环境：先手动启动 Chrome 并扫码登录（见仓库 README「首次准备」）；doctor 的 cdp 检查会给出启动命令。
- cookies missing：`python3 scripts/dy.py cookies` 自动开一次页生成（已有 douyin tab 时 1 秒复用）。
- yt-dlp 是 optional：没有/过旧不影响，视频有 DOM 兜底。

## 用法（单入口，JSON 输出）

```bash
python3 scripts/dy.py resolve "<任意分享文本 / 短链 / 长链 / ID>"     # -> aweme_id/type
python3 scripts/dy.py fetch "<同上>" --want text,image,video,audio,cover
python3 scripts/dy.py fetch "<同上>" --route dom                       # 强制 DOM
python3 scripts/dy.py cookies [id]                                     # 刷新 cookie 文件（路径见 doctor）
```

- `--want` 默认 `text,image,video`；要 BGM 加 `audio`，要封面加 `cover`。
- `--route auto`（默认）= 视频先 yt-dlp，失败自动落 DOM；note 直接 DOM。`--route ytdlp` 只走 yt-dlp。
- `--out-dir` 输出目录（默认：OpenClaw 内 `~/.openclaw/workspace/tmp`，其他环境 `~/Downloads/douyin`；env `DOUYIN_OUT_DIR` / `DOUYIN_COOKIE_FILE` / `DOUYIN_CDP` 可整体覆盖）。
- `--api-fallback` 实验性兜底（iesdouyin `_ROUTER_DATA` → detail API + ttwid 自动注册），默认关；2026-08/09 起这两条路大多已失效，只在 DOM 全挂时试。
- `--wait` 页面渲染等待上限（默认 15s）；输出 manifest 同时落盘 `dy_<id>_manifest.json`。
- 退出码：0 = success/partial，1 = failed；以 JSON 的 `status` 为准更稳。

## 状态语义（不确定绝不报成功）

| status / 错误码 | 含义 | 怎么办 |
|---|---|---|
| `success` | --want 全都要到了 | 正常交付 |
| `partial` | 拿到一部分（如 note 本来就没有实况视频） | 交付已拿到 + 说明缺什么 |
| `failed` + `blocked_by_verification` | 页面出验证码/滑块 | 截图告知用户，不硬绕、不清 cookie |
| `failed` + `login_required` | 登录墙 | 报告受限，不硬试 |
| `failed` + `page_drift` | 页面结构变了 | 重试一次；仍漂移则记录并报告 |
| `empty_video_url` | 视频 URL 缺 video_id/aid | 已等播放重读仍缺 → 不下载（会 200 空 body） |
| `invalid_input` / `unsupported_profile` | 不是单作品（主页链接等） | 跟用户确认要哪一条作品 |
| warning `ytdlp_failed` | yt-dlp 失败，已落 DOM | 最终成功则忽略 |
| warning `no_video_in_post` / `no_images_in_post` | 作品本身没有该类媒体 | 如实告知 |
| warning `download_failed_*` | 单个资源 403/过期 | 重取 cookie 重试一次 |

## 元数据（manifest 里都有；缺失留空，不推测）

作者昵称、粉丝/获赞（`stats.followers` / `stats.author_likes`）、互动数（赞评藏转）、发布时间、BGM 名、封面 URL、话题（从文案抽 `#`）。文案 = `desc`（页面没有 desc 元素时回退 `document.title` 的正文，这是页面自己给的标题，不是猜的）。

## 技术基线（别改）

- CDP 默认 `127.0.0.1:18800`（`--cdp` / env `DOUYIN_CDP` 可换）；cookie 文件默认 `<tmpdir>/dy_cookies_netscape.txt`（env `DOUYIN_COOKIE_FILE` 可换；Netscape 0600，缺失时脚本自动生成；禁止清 cookie、禁止自启浏览器）。
- 图片 **绝不删签名 query**（`x-expires`/`x-signature`）；提清晰度只换路径模板 `~tplv-dy-aweme-images:q75`，失败自动回退原 URL。
- **B3 铁律**：视频 URL 必须含 `video_id=` 和 `aid=`，缺则等播放 2~3 秒重读；仍缺就是不下载。
- 交付前脚本已 ffprobe/file + SHA256；`media[].probe.ok=false` 的文件不要用。

## ⚠️ 三条硬教训（2026-09-15 实单踩坑，原样保留）

1. **`/note/` ≠ 没有视频**。图文笔记可以是**实况照片（Live Photo）**：数据字段 `video.play_addr` 为空，但 DOM 里挂着 `<video>`，内嵌 3~4 秒真视频。判定顺序：**先看 DOM `<video>.currentSrc`，再看数据字段**。（v3 在 `--want video` 时自动多等 8 秒实况视频挂载。）
2. **不要把分享文案当铁证**。口令里的「图文作品」只是分享卡片标签，实况照片照样显示它。用户说"这就是视频"时，**先验证再反驳**。
3. **交付文件放对目录**：OpenClaw 下必须放 `~/.openclaw/workspace/tmp/`（`/tmp` 会被 Telegram 附件通道和 `image` 工具拒绝）；其他环境用 `--out-dir` 指到用户拿得到文件的位置。

## 轨道与降级（v3）

| 轨道 | 做法 | 现状 |
|---|---|---|
| 视频 | yt-dlp 首选 → 失败落 DOM `video.currentSrc` | 2026-09 起 yt-dlp 抖音 extractor 频繁报错（上游 issue），DOM 兜底是常态 |
| 图文/实况 | 开页读渲染后 DOM（自适应轮询） | 有效路线 |
| 数据字段 | PC `RENDER_DATA` 只有 `app`；iesdouyin `_ROUTER_DATA` 2026-08 改版后大多只剩壳 | 已证失效；仅 `--api-fallback` 实验 |
| 解析站 | 不用 | 不稳 + 泄露链接 |

## 交付（固定动作，别省）

1. 看 `status`：success/partial 才按常规交付；failed 按上表处理。
2. 用 manifest 的 `media[].path`；放到交付目录（默认 out_dir）并改**有语义中文名**。
3. 附件与登记（按运行环境二选一）：
   - **OpenClaw 环境**：附件用独立成行的 `MEDIA:<workspace 下绝对路径>`，不套 markdown、不写在句子里；并登记素材库：
     `~/.openclaw/tools/asset add <文件> --source manual --category <图片/配图|视频/片段> --theme 未分类 --title "<作品标题>" --tags 抖音`
   - **其他环境**：直接把文件路径报给用户（或按宿主 agent 的附件方式交付）；不执行 asset 命令。
4. 简报：标题、作者、时长/分辨率、大小；note 类说明**取到几张图 / 有无实况视频 / BGM**；failed 时直接贴 `errors[].code` 和原因。
5. 文案需求：正文原文贴出，保留话题标签原样。

## 兼容入口

- `scripts/note_media.py <note_id> [...]` → 转发 `dy.py fetch`（输出同一 JSON 契约）。
- `scripts/get_douyin_cookies.py [id] [--json]` → 直接刷新 cookie 文件。
- `scripts/doctor.py` → 同 `dy.py doctor`。
- 自检（离线）：`python3 -m unittest discover scripts/tests`（25 项）。

## 红线

- 频率：一天十几条以内没问题；**不做评论区批量扒取、不做关键词爬取**（用户 2026-09-14 确认）。
- 只读：不点赞/评论/关注/发布。
- 只管理自己创建的 tab，用完 `json/close/<targetId>` 关闭；禁止按域名关页面。
- 能拿原片绝不做后期去码（裁边/AI 修复只作用户自带成品素材的兜底）。
- 用户纠正判断时：先跑验证再回复，不要用旧结论反驳。
- 版权提醒默认省略（个人素材用途）；涉及搬运发布由内容流程判断。
