# douyin-download — 抖音作品获取 Skill

给 AI agent 用的抖音单作品获取工具（[Agent Skills](https://agentskills.io) 开放格式，Claude Code / Codex / Cursor / OpenClaw 等均可使用）：

- **分享即用**：整段分享口令、`v.douyin.com` 短链、长链、纯 ID 都能解析
- **按需下载**：原图（自动换全尺寸模板、不删签名参数）、无水印视频（yt-dlp 优先，DOM 兜底）、实况照片、BGM
- **元数据**：作者/粉丝/赞评藏转/发布时间/话题，缺失留空不推测
- **可靠交付**：每个文件 ffprobe 校验 + SHA256，manifest 落盘；显式状态（success / partial / failed + 错误码），不确定不报成功

> 原理：走浏览器会话（CDP）取平台**本来提供的干净流**，不做后期去水印、不做批量爬取。

## 安装

选一个：

```bash
# 任意 agent（推荐，自动识别 Claude Code / Codex / Cursor 等）
npx skills add Arekejoker/douyin-download-skill

# GitHub 官方 CLI（gh ≥ 2.90）
gh skill install Arekejoker/douyin-download-skill douyin-download --agent claude-code

# OpenClaw
openclaw skills install git:Arekejoker/douyin-download-skill@main

# Claude Code 手动
git clone https://github.com/Arekejoker/douyin-download-skill ~/.claude/skills/douyin-download
```

装好后对 agent 说：`帮我下载这个抖音 https://v.douyin.com/xxxx/`。

## 首次准备（一次性，约 2 分钟）

1. **依赖**：python3.10+、`curl`、`ffprobe`（随 ffmpeg）；Python 包：`pip install -r requirements.txt`（只有 `websockets`）。`yt-dlp` 可选，没有也行（视频有 DOM 兜底）。
2. **启动一个已登录抖音的 Chrome（挂 CDP 端口）**：

   ```bash
   # macOS 把 google-chrome 换成对应应用路径
   google-chrome --remote-debugging-port=18800 --user-data-dir="$HOME/.douyin-chrome" https://www.douyin.com
   ```

   在弹出的窗口里**扫码登录抖音，然后保持窗口开着**。这个 skill 只在你要东西时连接它，从不自启/重启/清理浏览器。
3. **自检**：

   ```bash
   python3 scripts/dy.py doctor   # 全部 required 通过即可用
   ```

   cookie 文件会自动生成（首次 fetch 时），也可以手动 `python3 scripts/dy.py cookies`。

## 使用

```bash
python3 scripts/dy.py resolve "<分享文本 / 短链 / 长链 / ID>"          # 解析出 aweme_id / type
python3 scripts/dy.py fetch  "<分享文本 / 短链 / 长链 / ID>"           # 默认 text,image,video
python3 scripts/dy.py fetch  "<...>" --want text,image,video,audio,cover
python3 scripts/dy.py fetch  "<...>" --route dom                       # 强制 DOM 轨道
python3 scripts/dy.py fetch  "<...>" --out-dir ~/Pictures/douyin       # 指定输出目录
python3 scripts/dy.py doctor                                           # 环境自检
```

输出：stdout 一个 JSON manifest（同时落盘 `dy_<id>_manifest.json`），文件默认输出到 `~/Downloads/douyin`（OpenClaw 环境为 `~/.openclaw/workspace/tmp`）。

环境变量：`DOUYIN_OUT_DIR` / `DOUYIN_COOKIE_FILE` / `DOUYIN_CDP`。

### 状态语义

| status | 含义 |
|---|---|
| `success` | 要的全拿到了 |
| `partial` | 拿到一部分（例如作品本来就没有实况视频 / BGM） |
| `failed` | 没拿到，看 `errors[].code`：`blocked_by_verification`（验证码）、`login_required`、`page_drift`、`empty_video_url`…… |

`errors[].code` 与 `warnings[].code` 都在 JSON 里，agent 会照实汇报，不会假装成功。

## 常见问题

- **yt-dlp 报错 / 403 "fresh cookies needed"**：2026-09 起抖音风控抖动，正常现象——本工具会自动落 DOM 轨道继续；cookie 报错时重跑一次 `dy.py cookies`。
- **弹出验证码/滑块**：去那个 Chrome 窗口手动过一次即可；工具不会硬绕、不会清你的 cookie。
- **`empty_video_url`**：视频地址缺 `video_id`/`aid`（拿不到真流），脚本会等播放重读，仍缺则拒绝下载（避免 200 空文件）。
- **主页链接**：不支持（返回 `unsupported_profile`）——这是单作品工具，把具体作品分享链接发来即可。
- **重复 fetch 同一作品**：manifest 同名覆盖（文件会按时间戳后缀新下载，不覆盖旧文件）。
- **Windows**：脚本本身跨平台，但未在 Windows 实测过（Chrome CDP 启动命令请按 Windows 写法调整）。

## 隐私与合规

- cookie 只存本机临时目录（0600 权限），不会上传、不会外发。
- 只读操作：不点赞、不评论、不关注、不发布；**不做评论区批量采集、不做关键词爬取**。
- 用途为个人素材备份 / 创作参考；请遵守抖音平台条款与原作者版权，不要用于搬运侵权。

## License

MIT（见 [LICENSE](LICENSE)）
