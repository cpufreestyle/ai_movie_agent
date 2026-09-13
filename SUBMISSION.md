# AI+∞ 开发者创作大赛 · 第二期「AI+影视流」提交清单

类别：**进阶创作｜电影 Agent**（作品 = ai_movie_agent）
官网提交入口：https://mseo-ai-inf.ms.show/submit

## ⏰ 关键时间点（务必注意）
- **报名截止：2026-09-14 12:00**（需小红书等发帖 + ≥15s Demo）
- **作品提交截止：2026-09-14 22:00**
- 评审 09-15~09-18；公示 09-19

> 今天 2026-09-13，距报名仅约半天，请优先处理需登录的环节。

> 进度快照（2026-09-13 夜）：三集成片《看见未来之前》已全部出片并发布 B 站（见「三、Demo 视频来源」），
> Demo/作品视频这条最硬的依赖已经落地；剩余的都是需登录的报名/提交动作。

## 一、报名（09-14 12:00 前，需登录社交账号）
- [ ] 准备 **≥15 秒 Demo 视频**（见下方"Demo 来源"）
- [ ] 在小红书（或微博/抖音/B站/知乎/公众号）发帖，文案见 `docs/小红书报名文案.md`
- [ ] 带话题 `#Qoder #AI无限开发者创作大赛 #用AI提前看见未来 #AIGC`，并 @魔搭ModelScope社区 @Qoder
- [ ] 到官网 https://mseo-ai-inf.ms.show/submit 提交公开帖链接 → 报名成功

## 二、提交作品（09-14 22:00 前，电影 Agent 类）
- [ ] **部署至魔搭创空间的作品链接**（Space 包装见仓库 `space/`，部署需 ModelScope 账号）
- [ ] **创作手记**（发布于魔搭开发者实践），文稿见 `docs/参赛手记_电影Agent.md`

## 三、Demo 视频来源（**已达成**：选项 A）
三集成品已产出并发布在 B 站，可直接作为 Demo / 作品链接：
- **EP1 第一集 · 进城** → https://www.bilibili.com/video/BV17uYi6UEPx
- **EP2 第二集 · 觉醒** → https://www.bilibili.com/video/BV1ouYi6mE8Z
- **EP3 第三集 · 对抗** → https://www.bilibili.com/video/BV1ouYi6mEwL

规格：1024x576 / 24fps / 59s，英文旁白 + 中英双语字幕，分区 tid=172（短片）。
本地文件：`outputs/videos/epN_vo_film_mmh3.mp4`；预览拼图：`outputs/preview/_preview_epN_film.png`。

> 备选（如需自录）：选项 B 直接用本地 pipeline 再出一段；选项 C 用静态分镜拼剪 / WebUI 录屏。

## 四、本仓库已准备好的材料（Agent 产出，无需登录）
- `docs/参赛手记_电影Agent.md` —— 创作手记长文（直接复制到魔搭开发者实践发布）
- `docs/小红书报名文案.md` —— 报名帖文案（含话题与 @）
- `space/` —— 魔搭创空间可部署包装（Gradio app + requirements + README）
- `DEPLOY.md` / `deploy.py` —— 跨平台部署说明与统一入口

## 五、仍需你（或你的账号）完成的登录动作
1. 小红书发帖（报名）
2. 官网提交帖链接（报名）
3. 魔搭创空间部署 Space（作品链接）
4. 魔搭开发者实践发布创作手记（作品）

> 以上 4 项均需在对应平台登录，Agent 无法代操作。提供 ModelScope token 可由 Agent 协助部署/发布。

## 六、Space 部署步骤（待你给 token + 空间 git URL）
1. **你（网站操作）**：登录 modelscope.cn → 创空间 → 新建（选 **Gradio** SDK 模板）→
   复制其 git 地址（形如 `https://www.modelscope.cn/studios/<用户名>/<空间名>.git`）；
   再到「账号设置 → 访问令牌」生成 token。
2. 把 **空间 git URL + token** 给我（或设为环境变量 `SPACE_URL` / `MODELSCOPE_TOKEN`）。
3. **我执行**：`python space/deploy.py --space-url <url> --token <token>`
   脚本会克隆该仓库，把「完整项目 + Space 包装」推上去
   （根目录 `app.py`←`space/app.py`、`requirements.txt`←`space/requirements.txt`、`README.md`←`space/README.md`，
   并随附 `agent/ tools/ skills/ workflows/ cli.py config.yaml` 等）。
4. 平台自动 `pip install -r requirements.txt` 并启动 `app.py`；在「设置 → 查看日志」看构建，
   上线后在空间主页拿到**作品链接**。
5. 把该链接填到官网提交页（作品链接）。

> 备注：创空间默认多为 CPU 环境，`app.py` 会自动降级为「内置模板企划 Demo」（保证可跑通、可展示
> "一句话创意→世界观→逐镜分镜"的 Agent 能力）。要跑真实流水线并出片，在空间环境变量配置
> `COMFYUI_API` / `ENGINE_BACKEND` / `LLM_MODEL`，并安装项目完整依赖（含 torch）。详见 `space/README.md`。
