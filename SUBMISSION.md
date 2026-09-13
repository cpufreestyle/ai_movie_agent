# AI+∞ 开发者创作大赛 · 第二期「AI+影视流」提交清单

类别：**进阶创作｜电影 Agent**（作品 = ai_movie_agent）
官网提交入口：https://mseo-ai-inf.ms.show/submit

## ⏰ 关键时间点（务必注意）
- **报名截止：2026-09-14 12:00**（需小红书等发帖 + ≥15s Demo）
- **作品提交截止：2026-09-14 22:00**
- 评审 09-15~09-18；公示 09-19

> 今天 2026-09-13，距报名仅约半天，请优先处理需登录的环节。

> 进度快照（2026-09-13 夜）：
> 1) 三集成片《看见未来之前》已全部出片并发布 B 站（见「三、Demo 视频来源」）。
> 2) **魔搭创空间作品已部署上线（status=Running）**：
>    **https://www.modelscope.cn/studios/Michaelqiu/ai-movie-agent**
> 剩余的都是需**用户本人登录**的动作：小红书报名帖、官网提交帖链接、开发者实践发手记。

## 一、报名（09-14 12:00 前，需登录社交账号）
- [x] 准备 **≥15 秒 Demo 视频**（见下方"Demo 来源"）—— 已达成（B 站三集成片）
- [ ] 在小红书（或微博/抖音/B站/知乎/公众号）发帖，文案见 `docs/小红书报名文案.md`
- [ ] 带话题 `#Qoder #AI无限开发者创作大赛 #用AI提前看见未来 #AIGC`，并 @魔搭ModelScope社区 @Qoder
- [ ] 到官网 https://mseo-ai-inf.ms.show/submit 提交公开帖链接 → 报名成功

## 二、提交作品（09-14 22:00 前，电影 Agent 类）
- [x] **部署至魔搭创空间的作品链接** ✅ **已完成（2026-09-13 晚，Agent 用 OpenAPI 创建并部署）**：
  **https://www.modelscope.cn/studios/Michaelqiu/ai-movie-agent**
  （public / sdk=gradio 6.17.3 / 免费 CPU 16G `platform/2v-cpu-16g-mem` / status=Running / 应用 host `michaelqiu-ai-movie-agent.ms.show`）
- [ ] **创作手记**（发布于魔搭开发者实践），文稿见 `docs/参赛手记_电影Agent.md`

## 三、Demo 视频来源（**已达成**：选项 A）
三集成品已产出并发布在 B 站，可直接作为 Demo / 作品链接：
- **EP1 第一集 · 进城** → https://www.bilibili.com/video/BV17uYi6UEPx
- **EP2 第二集 · 觉醒** → https://www.bilibili.com/video/BV1ouYi6mE8Z
- **EP3 第三集 · 对抗** → https://www.bilibili.com/video/BV1ouYi6mEwL

规格：1024x576 / 24fps / 59s，英文旁白 + 中英双语字幕，分区 tid=172（短片）。
本地文件：`outputs/videos/epN_vo_film_mmh3.mp4`；预览拼图：`outputs/preview/_preview_epN_film.png`。
上传小红书成片：**优先用 `outputs/videos/epN_vo_film_mmh3.mp4`**（三集成片，分区 tid=172）。
> 注：先前的高码率超分版 `outputs/ep1_up_mmh3.mp4`(617MB)、`outputs/ep1_vo_up_mmh3.mp4`(538MB) 已在仓库清理中删除；若需更高码率备选版，可经 `python upscale_video.py outputs/videos/ep1_vo_film_mmh3.mp4 outputs/ep1_vo_up_mmh3.mp4` 重生成。

> 备选（如需自录）：选项 B 直接用本地 pipeline 再出一段；选项 C 用静态分镜拼剪 / WebUI 录屏。

## 四、本仓库已准备好的材料（Agent 产出，无需登录）
- `docs/参赛手记_电影Agent.md` —— 创作手记长文（直接复制到魔搭开发者实践发布）
- `docs/小红书报名文案.md` —— 报名帖文案（含话题与 @）
- `space/` —— 魔搭创空间可部署包装（Gradio app + requirements + README）
- `DEPLOY.md` / `deploy.py` —— 跨平台部署说明与统一入口

## 五、仍需你（或你的账号）完成的登录动作
1. 小红书发帖（报名）
2. 官网提交帖链接（报名）
3. ~~魔搭创空间部署 Space（作品链接）~~ ✅ 已完成：https://www.modelscope.cn/studios/Michaelqiu/ai-movie-agent
4. 魔搭开发者实践发布创作手记（作品）

> 备注：Agent 已用 ModelScope OpenAPI 完成创空间创建 + 推送 + 部署（无需网页操作）。
> 但小红书发帖、官网提交、开发者实践发手记均需**用户本人登录态**（微信内置浏览器里的 Cookie 不构成有效会话），无法代登。

## 六、Space 部署（✅ 已完成，2026-09-13；以下留作复盘/重建步骤）
1. **（已由 Agent 用 OpenAPI 完成）** `POST https://modelscope.cn/openapi/v1/studios`
   → `{owner: Michaelqiu, repo_name: ai-movie-agent, sdk_type: gradio, visibility: public, hardware: platform/2v-cpu-16g-mem}`。
2. **推送源码**：`python space/deploy.py --space-url https://www.modelscope.cn/studios/Michaelqiu/ai-movie-agent.git --token <MODELSCOPE_TOKEN>`
   （脚本克隆该仓库，把「完整项目 + Space 包装」推上去：根目录 `app.py`←`space/app.py`、`requirements.txt`←`space/requirements.txt`、
   `README.md`←`space/README.md`，并随附 `agent/ tools/ skills/ workflows/ cli.py config.yaml` 等）。
3. **触发部署**：`POST .../studios/Michaelqiu/ai-movie-agent/deploy`，轮询 `GET .../studios/Michaelqiu/ai-movie-agent`
   （`Building → Deploying → Running`，约 3 分钟）与 `GET .../logs/run` 看日志。
4. 上线后作品链接：**https://www.modelscope.cn/studios/Michaelqiu/ai-movie-agent**。

> 手动方式（备选）：登录 modelscope.cn → 创空间 → 新建（Gradio 模板）→ 复制 git 地址 → 给 Agent 执行 `space/deploy.py`。

> 备注：创空间是 CPU 轻量环境（无 torch / 无 `config_env.py`+`webui.py`，`cli.py` 无法启动），
> 因此 `app.py` 以 **`COMFYUI_API` 为开关**：未配置时直接走「内置模板企划 Demo」（秒级返回，
> 保证可跑通、可展示"一句话创意→世界观→逐镜分镜"的 Agent 能力）；配置后才尝试真实 A→H 流水线。
> 要跑真实流水线并出片，在空间环境变量配置 `COMFYUI_API` / `ENGINE_BACKEND` / `LLM_MODEL`，
> 并在 requirements 安装项目完整依赖（含 torch）。详见 `space/README.md`。
> 已验证（2026-09-13 夜）：现场调 `POST /gradio_api/call/run_agent` 返回完整世界观 + 3 镜分镜，无报错。
