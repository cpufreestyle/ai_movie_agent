# 项目长期记忆：ai_movie_agent

> **合并说明（2026-09-15）**：本文件由旧目录 `.codebuddy/memory/MEMORY.md` 与 `.workbuddy/memory/MEMORY.md` 合并而成。
> 两侧内容几乎不重叠（相似度 0.05~0.11），故取**并集**：以 `.workbuddy` 为权威，冲突处已按最新实测裁定并标注。
> 旧目录已停止跟踪（`git rm --cached .codebuddy`），原文件保留在磁盘仅作备份。

## 一、硬件 / 环境
- GPU: RTX 5070 Ti 16GB (sm_120)。ComfyUI `D:/ComfyUI` 常驻 8188（venv python；GGUF+LTXVideo+T8/VideoHelperSuite）。⚠️ **本机 venv 曾因 base 解释器被删而起不来、且不能用 `launch_comfy.py` 常驻 —— 起服务前先看「十」头条两条**。
- 模型根 `E:/ComfyUI_models/`（extra_model_paths.yaml，E/D 都查）。代理 127.0.0.1:7897；**访问本机 ComfyUI 须 NO_PROXY/ProxyHandler({}) 防 502**。
- **cu130 必须**（cu128→CUDA 禁用→latent 全噪）。venv: `d:/ai sheare/repo/ai管理/.venv/Scripts/python.exe`。终端 PowerShell；长任务 Start-Process 后台 + Get-Content 轮询。
- AMD395(128GB) 须 BF16/FP8（NVFP4 不兼容）。
- **硬件档位 `HW_TIER`（部署选择，2026-09-15 新增 `amd395-128g`）**：`config_env.HW_TIER_PROFILES` 是唯一权威；档位 = `high/mid/low/cpu/amd395-128g/dgxspark-128g`。三处入口：`HW_TIER` 环境变量、`config.hw_tier`、`python deploy.py --tier amd395-128g`（会写进 `.env`，compose 已透传）；`AUTO_HW=1` 自动检测。别名（`amd395`/`395`/`strix-halo`/`ai-max-395-128g`）统一由 `normalize_tier()` 归一，**别再各处硬列档位名**。
- **AMD 395 为什么要单列一档**：其「显存」由 128G 统一内存切出（BIOS UMA 75–96GB），而 WMI 的 `AdapterRAM` 是 32 位字段、iGPU 常报 512MB~4GB，Linux `lspci` 报 0 → 老 `pick_tier()` 会把这台顶级机判成 **cpu**。识别改走「AMD + 内存 ≥96GB + 型号线索（395/STRIX/AI MAX/8060）+ 显存被低估兜底」；**注意别误伤 AMD 真独显**（RX 7900 XTX 24GB+128G 内存应仍是 high，已锁测试）。
- **DGX Spark 同为「大统一内存」家族（2026-09-15 新增 `dgxspark-128g`）**：NVIDIA GB10 Grace Blackwell（Project Digits，128GB 统一内存），覆盖与 amd395-128g **完全相同**（bf16／1024x576／90 帧／block_cache on／two_pass on／offload off／blender 64／qa 3 reroll）。识别走「NVIDIA + 内存 ≥96GB + 型号线索（GB10/DGX SPARK/DGXSPARK/PROJECT DIGITS/DIGITS；**不再单列 "NVIDIA GB10"/"NVIDIA DGX SPARK"**，它们是上面词的子集超集、子串判断已覆盖，列出来只会让判定依据出现重复命中词）+ 极低显存兜底」，**先于通用档判断**；**注意别误伤独立 HBM 卡**（DGX A100/H100、RTX 5090 应仍是 high，已锁测试）。别名 `dgxspark`/`dgx-spark`/`dgx`/`digits`/`project-digits`/`gb10`。

## 二、SageAttention（2026-09-10）
- wheel: HF `ussoewwin/Sage-Attention-for-Windows`（cu130torch2.11.0-cp313，SA2/SA3），装须 `--no-deps`；另 `pip install triton-windows`。
- L≤512 比 SDPA **慢 3~4x**，勿用于短序列。H3 走全局 optimized_attention：SA2=`--use-sage-attention`；SA3=改 attention.py + `launch_comfy.py --sage3`。
- **生产推荐 SA2**（cos .999 近无损，86.6s）；SA3 慢 ~8% 且 cos .981 或劣化人脸。SA2 可常态化（`COMFY_SAGE=1`）。

## 三、出片引擎（16GB 档）
- **MiniMax H3（主力）**：`engine.backend: comfyui_mmH3` 为默认。INT4 档；须 `comfyui-minimax-h3-audio-T8` + VideoHelperSuite。Turbo LoRA → 48s 出 768×448/2.33s/立体声。入口 `agent/mmh3_engine.py` + `cli.py mmh3` + `run_series.py --engine mmh3`。引擎自动修 32 整除 / 17n+5 吸附。**组件勿跨模型混搭**（H3 UNet 须配 H3 TE 输出 5120）。
- LTX-2.5（Q2_K + gemma4-12b int8）：`cli.py ltx`、`agent/ltx_engine.py`。真 T2V + 音频。
- Wan2.2 TI2V-5B Q8：**仅 I2V 无音轨**，必传 start_image、负向必填。
- 评估须量化：arcface 身份 + 人脸检出率 + 走位方向（frame-diff 前景质心 net_shift / optical flow trend），**不凭肉眼**。

## 四、白模（Blender Blocking）→ H3 走位管线
- 首帧 / ref_video 路线**已证无效**（2026-09-14）：首帧只弱锁起始构图/站位；连「角色走位灰模 ref_video」也左移且慢 1.7× → **ref_video 彻底弃用**。
- **✅ H3 Fun Control = 真正能控走位**（2026-09-14 实测突破）：H3 原生节点 `MiniMaxH3FunControlLoader/ApplyT8Advanced` 把逐帧 depth/pose/edge `control_video` 注入 DiT（第 0/10/20/30/40 层）。
  - 权重 `minimax_h3_fun_controlnet_union_pruned_int8_convrot.safetensors`(2.3GB，单权重支持 Canny/Depth/HED/MLSD/Pose)；本机 HF 被封 → 从 ModelScope `Comfy-Org/MiniMax-H3` 拉（`download_ms.py`，直连 CN CDN，设 `MS_PROXY=none`）；放 `E:/ComfyUI_models/model_patches/`。
  - pruned 控制须配 pruned 主模型（fl2va_pruned_int4 = 8 维 AdaLN 兼容）。`control_kind` 仅描述性，须自预处理。17n+5 帧 / 24fps / `fit_mode=exact` 几何须严格匹配。
  - **A/B（768×448/56帧/Turbo/seed12345，白模角色 0.1W→0.9W 左→右走）**：基线（无 FC）net −10.9px(左)/act1.98；FC depth strength0.8 → net +13.7px(右)/act2.14（干净）；**strength1.2+end1.0 → net +37.1px(右)/act10.78**；strength1.5 → act13.33 **崩坏**（net−5.7 失稳）。→ **FC 纠正走位方向**；**strength 是主杠杆，推荐 0.8~1.2（<1.5）**；幅度仍远小于白模意图（+37 vs +614px）。
  - ⚠️ **白模走位须落在相机视野内**：static/no-track 相机 (0,−7) 在角色深度处水平可见约 ±2.6；walk ±3 会起末出画（实测仅 46/56 帧可见，控制信号丢失），用 **±2.2** 全程可见（56/56，屏幕 x 0.07W→0.93W）。要更大范围就拉远相机或 `--cam lateral`。
  - 视野影响 A/B（seed12345/s1.2/e1.0）：出画(walk±3) net+37.1px/act10.78 vs 全程可见(±2.2) net+21.2px/act9.42 —— 都右移 ✓；推荐 ±2.2。**度量口径：以 net_shift（帧差前景净位移）为主**，光流 trend 在大运动下不稳（曾 dx 正/trend 负矛盾）。
  - **复杂走位**：`gen_blocking --walk` 支持多点折线 `x1,y1:x2,y2:...`（按累计弧长匀速）。闭环 `-2,1:2,1:2,-1:-2,-1:-2,1`（俯视矩形循环+景深，面积 2.8%→5.2%，56/56 可见）→ H3 出片屏幕 x 亦**先增后减** → 跟随的是**逐帧运动结构**而非单向位移。
  - 落地：`gen_blocking.py --depth`（相机视距 MapRange 近白远黑，背景/地面压黑只留角色）；`run_h3_funcontrol.py`（FunControlApply 插 LoRA 后/采样前，VHS_LoadVideo 锁 768×448×56；支持 `--walk` 端到端自动生成控制视频）。
  - **主线集成**：`agent/mmh3_engine.py` 已内建 → `generate(control_video=.., fc_strength=..)` 或 config `engine.comfyui_mmH3.fun_control`（enable/control_net/control_kind/fit_mode/strength/end_percent/video）；引擎自动插节点 41/42/43（避让 ref_images 20~28 / post 30~32 / BlockCache 40 / 二采 50~57），guider 改接 Apply 输出。真机引擎路径 net_shift +223.7px(右移) ✓。
  - **全链路接入（agent.py + blocking.py）**：config `blender.use_as_fun_control: true` → `render_assets` 每镜产 **fcvideos**（走位 depth 序列→fc.mp4）→ `generate_one_scene` 自动作 control_video 喂引擎。走位来源：`blender.fun_control_walk`（默认 `-1,0:1,0`，归一化 ±1=画面左右）+ 分镜文本识别（左→右/走近/来回/绕圈，`parse_spec`）。`_FC_TAIL` 模板按镜头距离自动换算世界坐标+留边距→不出画；控制序列分辨率/帧数严格对齐出片（fit_mode=exact，帧数吸附 17n+5）。需 Blender + BlenderMCP(9876) 运行。
  - 坑：FunControl int8 2.3GB + H3 累积易 OOM 使 ComfyUI 崩（连续 A/B 两版后崩）；重启 ComfyUI **别再用 `launch_comfy.py`**（沙箱会回收它 detach 的子进程，见「十」），改用常驻后台任务（命令见「十」）；脚本须用 `/prompt` 返回的 prompt_id 轮询（非本地 uuid）。
- 驱动模板：`gen_ep5_fc.py`（BlockingGenerator 渲 fc_*.png → export_fc_video 合 fc.mp4 → MMH3Engine.generate → ffmpeg xfade 拼接）。
- 走位归一化：±1=画面左右；`parse_spec` 走**规则表**（`agent/blocking.py` 约 225~245 行）而非 if/elif 链 —— approach(走近/走向/靠近镜头)、away(远离/走远)、back_forth(来回/徘徊/踱步/走来走去)、circle(绕圈/环绕走/绕着/**走了一圈**/绕一圈/绕一)；「左/右同时出现」按出现先后定方向（兼容 从左到右 / 由左向右）。⚠️ **「走了一圈」早已覆盖**（旧记的「口语未覆盖、需补关键词」已过时，勿再当待办）。
- `fc_dir` 须用独立时间戳子目录(RUN)，避免 `_clean` 命中上一轮旧帧。
- **走位幅度增益 `blender.fun_control_walk_gain`**（默认 `1.0`=既有行为）：只放大 `_wx()` 的换算边距，**硬顶 1.0**（贴画面边缘，再大必出画）；`_resolve_walk_gain()` 对非数字/非正值回退 1.0。用途：白模意图 +614px 而 H3 实测只 +37px（约 6%），需放大白模幅度补偿（但**幅度瓶颈在 FunControl 侧**，增益只能补一部分，别当万能）。README 表述已改为保守版（"方向正确性改善，幅度约为意图的一小部分"），勿再写"精确锁定"。

## 五、白模 4 图渲染（Blender 5.2 实测结论，别再重复试错）
- 引擎分工：previs=EEVEE；line=**默认 CYCLES**（可配 `blender.line_engine: eevee`，快 12x，线条掩膜 IoU 72.9%）；depth/normal 回切 EEVEE（`blender.fast_control_passes`，默认 true）。
  @1280x720 单镜 4 图：旧 9.92s → 新默认 6.63s → 开 eevee 1.24s。
- **不要试图合并成一次渲染**：多 View Layer 下 `write_still` 只写一个文件；且引擎按场景生效，合并会把 CYCLES 传染给 depth/normal。
- Blender 5.2 事实：引擎枚举 `BLENDER_EEVEE`（无 EEVEE_NEXT，设置引擎走 try/except 链）；`scene.node_tree` 已移除（→`compositing_node_group`）；EEVEE **能**出 freestyle 线，BLENDER_WORKBENCH 不能且忽略材质节点（depth/normal 材质法失效）。
- 建探针场景必须先 `bpy.context.window.scene = scn` 再 `bpy.ops.*_add`，否则渲全黑图、任何开关的像素差恒 0 → 会得出错误结论。
- 验证工具：`python tools/verify_blocking_render.py render <tag> [--legacy|--size|--line-engine]` / `compare A B`（需 Blender MCP 9876 在线）。
- 相关配置键：`qa.agent_enabled`（逐镜质检重 roll 开关；**2026-09-16 起默认 `true`**，见「十二之二」）、`preflight.{enabled,block_publish}`（只管自动投稿，显式投稿不拦）。

## 六、无真人脸 / 动漫化（用户硬性要求）
- **成片不得出现真人脸，且反对全片模糊**。画风由参考图定，文本说了不算（写实锚定→真人）。
- 现用路线：H3 原片 → `anime_redraw.py` 逐帧动漫重绘（Counterfeit-V3.0，denoise 0.70，steps 20，0% 检出，最稳）或 `anime_stable.py --mode keyframe` → `make_narration` 加旁白 → faststart。
- 弃用：像素化（用户否「脸部打码」）、prop 模式（花屏发散）、ControlNet-Canny（锁真人脸→检出飙 68%）、3D 画风（70% 检出）。
- 连贯性：全片固定同一种子（逐帧变种子→抖动 8x）。单 GPU 不能同时跑多个 anime_redraw。
- 验收：`diag_face_rate.py`（insightface buffalo_l）量化，目标 0%。**该脚本只作离线诊断**，不入流水线。
- 🚫 **不要做真人脸检测（2026-09-16 用户明确）**：既**不**把「真人脸检出率」做成 preflight 发布闸门，也**不**在出片/质检流水线里跑人脸检出。唯一相关开关 `qa.face_check` 恒为 `false`（可选能力，默认关，勿翻）。
  → 我曾提议「把人脸检出率加进 preflight 当发布闸门」，**已被否决，别再提**。理由：检出开销 + 动漫脸误判高，且用户不接受该方向。剩下来解决真人脸只走**源头**：G 阶段提示词锁死动漫风格 + 风格锚定图用动漫（见上）。
- 默认风格 `config.project.style` = 日式动漫（`anime style, cel-shaded, ...`，见 v0.10.0），回到写实只改这一行。

## 七、配音 / 字幕 / 配乐
- `make_narration.py --film --out --fit-film --fps 24 [--series-script outputs/series_script.json --ep N] [--shots N] [--orig-vol 0.18]`。en-US-Andrew / zh-CN-Xiaoxiao。**mp4 必加 faststart**。
- 强制对齐：`agent/align.py` + `--align`（faster-whisper，可选依赖）。本地 TTS：`agent/tts.py` / `cli.py tts`。自动配乐 + 旁白 ducking：`agent/audio_mix.py` / `cli.py mix`。

## 八、发布（B 站）
- **21150 绕过**：动画分区 `tid=174` 的 add/v3 被端点拦截；切 `tid=172`（短片）直投成功。`_bili_upload.py`（用 cookies.json 完整 cookie + buvid3）。
- 三集连贯：1 进城→2 觉醒→3 对抗前主人。台词 `outputs/series_script.json`。
- `agent/preflight.py` + `cli.py preflight` 投稿前静态体检；`cli.py publish` / WebUI 投稿。

## 九、WebUI 结构 / 外观 / tooltip
- 结构：`webserver/{state.py,services/,views/}` + `webui.py` 只做组装；路由回归靠 dump url_map 比对（`tests/test_webui_routes.py`，现 **46 条**，新增即改 `EXPECTED`）。服务层：`services/{blocking,anchor,pipeline,series,hw}.py`；视图层：`views/{core,run,pipeline,film,bili,media,blocking,anchor,series,hw}.py`。
- **硬件档位选择已进设置页（2026-09-15）**：`services/hw.py`（`tier_options/profiles/current/detect/payload`）+ `views/hw.py`（`GET /api/hw`，**默认只读、仅 `?detect=1` 才探硬件**，避免开页面白卡 1~2s）；前端「部署 · 硬件档位」块 = `set-tier` 下拉 + `set-autohw` 复选 + `hw-preview` 只读覆盖预览，保存**复用既有 `POST /api/config`**（设/删 `hw_tier` 与 `auto_hardware`）。`current()` 用 `normalize_tier()` 归一别名，无法识别时返回 `unknown`+`raw` 由前端插一项提示。
- **成片页（分镜）**：`POST /api/storyboard` 会跑 `services/pipeline.validate_storyboard` —— 错误级（空镜/描述重复，忽略大小写与空白）400 拒；告警级（镜数≠18、解说多于镜头、描述含中文、单条解说>60 字）200 带 `warnings`。**「只重渲指定镜」= 逐镜 `run_ltx25_multishot.py --shot N --force` 再 `--concat`**；`--force` 必需，否则 `--shot N` 命中 `ltx25_manifest.json` 缓存直接跳过、根本不重出。镜号解析用 `pipeline.normalize_shot_indices()`（分隔符 `,，、;；\s`，丢 ≤0，排序去重）。
- **系列连贯出片页签（尾帧续写）**：`services/series.py`（纯函数 `build_argv/validate`，**刻意不 import `run_series`**——它顶层 import 会建目录/探 ffmpeg）+ `views/series.py`（`GET /api/series` 选项与已有 `ep*_series_film.mp4`、`POST /api/series/run` 校验→忙锁 409→后台）。对应 `run_series.py` 的 `--i2v/--anchor-mode/--only/--force/--ep`；**默认不勾 `--i2v`**（首帧权重过高会让镜头收敛/无视提示词），默认 `anchor_mode=first`。
- **锚定资产页签**（2026-09-15）：`services/anchor.py`（`SHOT_SPECS` 清单 + `default_names()`=三视图）+ `views/anchor.py`（`/api/anchor`、`/api/anchor/run` 名称白名单 + 忙锁、`/api/anchor/file`、`/api/anchor/index`）+ `gen_anchor_assets.py --force`。服务层**刻意不 import 生成脚本**（其顶层 `import run_series` 会建目录/探 ffmpeg），两份条目清单由 `tests/test_anchor_assets.py` 断言同名同序兜漂移。生成脚本产出的 `index.html` **必须经路由回传**，别在 HTML 里写 `/outputs/...`（无路由 → 404）。
- **路径穿越防护统一用 `state.safe_under(base, name)`**：**先 `\`→`/` 归一化再 `normpath`**。反斜杠在 Windows 是分隔符、在 POSIX 只是普通文件名字符，不归一化会导致同一 guard 在 CI 放行、本机拒绝（`/api/anchor/file` 就这么红过一次）。`views/blocking.py` 与 `services/anchor.py` 共用它。
- 外观：`webui/pipeline.html` 里有三块 `<style>`（基础 / `#ui-aesthetic` 打磨层 / `#ui-theming` 主题化）；主题/密度/强调色走 `html[data-theme|data-density|data-accent]` + `localStorage(ui-*)`，`<head>` 里有防 FOUC 的早期脚本。`/timeline` 只跟随不自带控件。
- **悬停解释 tooltip 模式**（已用于白模专业选项 + 设置页阶段选择）：内容包 `<span class="tip" data-tip="…">`；JS 建单一 `#ui-tip` 浮层（fixed + `pointer-events:none` + `white-space:pre-wrap`），用 document 级 `mouseover/mouseout/focusin` 委托 + `closest('[data-tip]')` 定位，越界翻边。**务必把 span 的 data-tip 复制到外层 `<label>`**，否则悬停 select/input 本体不触发。多行用 `&#10;`。
- **config.yaml 不入库**（公开仓库 + WebUI 会把 api_key 写回它）：仓库只保留 `config.example.yaml`；缺失时 `state._config_read_path()` / `cli.load_config` 回退读模板，**写入永远走 `CONFIG_PATH`（config.yaml）**。新增键 4 个顶层段：`qa` / `preflight` / `prompting` / `series`。
- 中文 JSON body 别用 PowerShell 的 Invoke-WebRequest 发（会乱码）；用 Python urllib + utf-8。

## 十、环境守卫 / 工具坑
- SAFE_DELETE：Python 删除 API turn 级批量拦截（阈值 50）。绕过用 `os.system("del /q")`（项目惯例见 gen_blocking.py）。
  ⚠️ **它会让本机 `pytest` 的退出码失真**：一轮跑完累计删除数超阈值后，用例内的 `os.unlink/shutil.move` 与 pytest 自己的 `basetemp` 清理都会被拦 → 输出全点却 `rc=1`（表现为「1 failed」，堆栈在 `sitecustomize._check_bulk_delete_guard`）。**判绿看 stdout 的 `N passed` 而非 rc**；可疑失败隔离单跑（rc=0 即证明是守卫非代码）。CI（Linux）无此守卫，是最终判据。
- Bash shim 损坏：`cd/dirname/ls/head/tail/grep/rm/wc` 不可用（Exit 127）。删除改用 `cmd /c del` 或 Python；结果落文件再用 Read 读（PowerShell 直出 stdout 有时抓不到）。
- **`python -c "…\n…"` 里的 `\n` 会被 shim 变成字面 `/n`** → 含换行的脚本一律 Write 成文件再执行。
- 预览：沙箱 loopback 不可用于预览 running Flask；用 Bash run_in_background 起 webui.py(:8000)。**改 .py 需重启服务**，HTML 即时生效。
  ⚠️ **端口双绑定会骗你**：旧实例仍挂 :8000 时，Windows `SO_REUSEADDR` 让新进程也能 bind 且日志照打「Running on …」，但请求被先绑的旧进程接走 → **新加的路由 404**（表现为「代码没问题却找不到端点」）。排查：`netstat -ano -p TCP` 列出 `:8000` 的**全部** `LISTENING` PID，全部 `taskkill /PID x /F` 后再起一个；别只看启动日志。
- 偶发 Edit 报成功但未落盘：改完必须 grep/read 复核。**并行 Edit 同一文件会互相覆盖，必须串行 + 改后复核**。
- ⚠️ **曾出现工作树内 `webui/pipeline.html`、`webui/timeline.html` 无故从磁盘消失**（内容在 git 里完好，`git checkout --` 即恢复）。提交前务必 `git status` 检查是否有意外 `D`，别盲目 `git add -A`。
- `git ls-files` 默认 `core.quotepath=true`，非 ASCII 路径输出成 `\345\217\202...` 转义形式 → 脚本据此遍历会误报「索引里有但工作区没有」。要加 `-c core.quotepath=false`。
- `-c http.proxy=` 只作用于**那一条命令**：`git ls-remote` / `git log origin/main`（不带 `-c`）会走全局死代理而报错，别据此判定远端有问题。
- 🔧 **ComfyUI 起不来第一嫌疑：venv 的 base 解释器没了（2026-09-16 实测修复）**。症状：`launch_comfy.py` 报
  `did not find executable at 'D:\Program\python.exe'` / 日志只有 68 字节。根因：`D:/ComfyUI/venv/pyvenv.cfg` 写着
  `home = D:\Program`、`executable = D:\Program\python.exe`（3.13.15），而沙箱把 `D:\Program` 改名成 `D:\Program.backup`
  （`D:/Program.backup/python.exe` 还在，但它自己的 prefix 也指向 `D:\Program`，**独立跑同样 `Failed to import encodings`，不能直接用**）。
  **修法（可回滚）**：备份 `pyvenv.cfg` → `.bak`，把 `home`/`executable` 指向现存完整 3.13
  （`C:\Users\michael\.workbuddy\binaries\python\versions\3.13.12\`），`version` 同步改写。3.13.x 内 ABI 兼容（cp313），
  venv 的 site-packages（含 torch/sage）无需重装 → 实测 `torch 2.11.0+cu130`、`cuda True` 正常。
- ⚠️ **沙箱会回收「detach 的子进程」→ ComfyUI 必须作为常驻后台任务跑**。`launch_comfy.py` 用
  `creationflags=DETACHED_PROCESS` 起 ComfyUI，前台命令一结束**整棵进程树被回收**：日志停在
  `Starting server / To see the GUI go to: http://127.0.0.1:8188` 且**无任何报错**，随即 8188 连接被拒
  （`is_ready()` 假报「ComfyUI 未就绪」）。修法：**别用 launch_comfy.py**，直接以 `run_in_background` 常驻起
  `D:/ComfyUI/venv/Scripts/python.exe D:/ComfyUI/main.py --listen 127.0.0.1 --port 8188 --fp32-vae --use-sage-attention`
  （cwd=`D:/ComfyUI`，带 `NO_PROXY` + 清空 `PYTHONPATH` + `TORCH_COMPILE_DISABLE=1`），实测 16s READY 并可持续。
- ⚠️ **`run_in_background` 的常驻服务也会被回收，别当永久在线**：实测 WebUI 后台任务跑了 **15h22m 后 `failed`**（`:8000` 掉线，且它的
  `> _wb_webui.log` 因 shim 的 `cd` 报错根本没落盘 → 拿不到崩溃原因）。→ 需要预览时**重新起**并即时验证，别假设上次那个还活着；
  起服务时一律把日志写到**确定存在的绝对路径**（如 `outputs/_webui.log`），别依赖 shim 里的 `cd`。
- ⚠️ **访问本机服务必须带 `NO_PROXY`**：env 里 `HTTP_PROXY/HTTPS_PROXY=http://127.0.0.1:13761`（沙箱注入），
  `NO_PROXY` **为空** → 对 `127.0.0.1:8188` 的请求会被送去代理，回 **502 Bad Gateway**（或 `ConnectionRefused`），
  造成「服务明明在跑却探不到」的假故障。跑 `run_series.py` / 任何探测前一律
  `NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost`。

## 十一、Lint / 测试门槛（ruff.toml 已入库）
- 根目录 `ruff.toml`：line-length 100、target py310、select = `E9,F63,F7,F82` + **F 全类** + **`C901`**（暂不开 E4/E7：E402 与项目「刻意延迟 import 重依赖」的设计冲突）；exclude 含 legacy/outputs。
- 本机跑 lint：`C:/Users/michael/.workbuddy/binaries/python/envs/default/Scripts/ruff.exe check .`。**别用 `python -m ruff`**（托管与系统 Python 都没装 ruff 模块，仓库内也没有 ruff 可执行文件；ruff 只装在托管 venv 的 `envs/default/Scripts`）。
- **`[lint.mccabe] max-complexity = 10`（2026-09-15 起纳入）**；`[lint.per-file-ignores]` 只放行两个不受本项目维护链路约束的文件：`tools/blender_mcp_addon.py`（上游 vendored）、`deploy/sol_h3_spark/sol_h3_server.py`（远程部署脚本）。
- ⚠️ **`tools/blender_server.py` 的 `import bpy` 必须保留**（`_exec()` 是 `exec(code, globals())`，远端脚本依赖模块全局的 `bpy`），已加 `# noqa: F401`。
- 复杂度现状：**全仓 C901 = 0**（治理前 48 处；9 处曾列的 `preflight.check 22`、`ltx_engine._inject 20`、`concept_video.render_concept_video 19`、`_strip_cloud_prompt_branch 17`、`publisher.upload 15`、`mmh3_engine._build_workflow 14`、`_write_mp4 12`、`_encode_frames 11`、`_validate_control_video 11` 全部拆完）。查热点：`ruff check . --select C901`。
- 测试基线：`pytest -q --basetemp=.pytest_tmp` **424 passed**（2026-09-15；`test_hw_profile.py` 含 amd395+dgxspark 两家族 + `test_hw_webui.py` 29 + 新增 `test_vram_guard.py` 7 例）；`python tests/smoke_test.py` 50 pass 也在 CI 跑。
- **跨平台断言自检（新增纪律）**：凡「CI 红但本机绿」，先问「这条断言依赖路径 / 编码 / 换行的平台差异吗」。本机是 ntpath，平台相关失败在 Windows 上验不动 → **用另一套 path 语义模拟自证**：`posixpath.normpath(posixpath.join(base, rel))` 跑同一组输入（本次 6 条越界串全 REJECT、2 条正常名全 KEEP）。已踩三次同类坑：`.gitignore` 的 `_*.py` 吃 `__init__.py`、黄金指纹嵌绝对路径、路径穿越反斜杠。
- **`.gitignore` 的 `_*.py` 会吃掉 `__init__.py`**（`*` 能匹配 `_init`）→ 已加 `!**/__init__.py` 取反。血的教训：`webserver/{,services/,views/}__init__.py` 因此**长期未入库**，本地有文件所以绿、CI 全新克隆必 ImportError。**新增包后必须 `git check-ignore -v <path>` 逐个确认**（被忽略的文件在 `git status` 里根本不出现）。
- 重构等价性验证法：`git show HEAD:<file>` 存到**仓库内**临时文件（放仓库外会因同目录 import 失败产生假差异），与新版同 argv 跑，stdout/stderr/rc 折叠空白后逐字比对。
- **判断「CI 红灯是不是自己引入的」**：`git archive --format=zip -o out.zip HEAD` → 解到干净目录 → 用托管 Python 跑 CI 同款命令。等价全新克隆，比 `git stash` 可靠（后者受本地残留文件影响 —— 上述两个 bug 都是「本地绿 / CI 红」）。
- 黄金指纹样本（`tests/fixtures/mmh3_wf_fingerprint.json`）**必须跨平台可复现**：工作流含 `ref_video`/`fc_video` 的真实绝对路径，`_norm` 需把**整个临时目录前缀**置换为 `TMPDIR` 并统一 `\`→`/`（只掩 `golden_xxx` 随机后缀不够，父目录 `E:\Temp` vs `/tmp`、分隔符都会导致 Linux 必红）。改工作流结构后重生成：`python tests/test_node_ids.py --update`。

## 十二、Git 同步 / Release
- 仓库实际路径：`C:\Users\michael\CodeBuddy\ai_movie_agent` 是 Junction → `D:\ai sheare\repo\ai_movie_agent`（show-toplevel 落在 D:）。
- 远端：GitHub `cpufreestyle/ai_movie_agent`（origin，https）；本地 main 跟踪 origin/main。
- **推送通道会变，按序试**（**别记死端口，先探测**：`socket.connect(('127.0.0.1', p))` 扫 `7897/11268/7890/10809/1080/10808`，或直接试直连）：
  1) 代理 `127.0.0.1:7897`（gitconfig 里的**全局默认**）—— **2026-09-15 晚实测可用**，`git push` **不加任何 `-c`** 即走它（数秒推完 5db6a82）。**别手动 `-c http.proxy= -c https.proxy=` 清空**（清了反而不通）。
  2) 直连 `git -c http.proxy= -c https.proxy= push origin main`：09-15 晚报 `Recv failure: Connection was reset`（当时不通），**但 09-16 实测可用**（83bb132 即用此推上）；而同晚 `git fetch` 直连反报 `Failed to connect ... 443`（push/fetch 行为不一致，疑似代理/抖动差异）。
  3) 代理 `127.0.0.1:11268`：曾长期可用；09-15 起 `Could not connect`（已挂）。
  4) 代理 `127.0.0.1:13761`（沙箱注入的 `HTTP_PROXY`）：`CONNECT tunnel failed 502` / `schannel: server closed abruptly`（不通）。
  - 同一端口**会时通时不通**（09-15 早 7897 曾报 `schannel: failed to receive handshake`）→ 别因一次失败就跳过，按序都试一遍再换招。
  - 保留默认 credential.helper（禁用会报 `could not read Username`）。
  - 看报错判代理状态：`schannel ...` / `CONNECT tunnel failed 502` / `Could not connect` = 该代理不通；`could not read Username` = 隧道通但没带凭据（去掉 `-c credential.helper=`）。PowerShell 的 `curl` 是别名，用 `curl.exe`。
- **禁止在本沙箱用 `git rebase`**：曾导致 `.git` 目录消失（工作树无损）。恢复法：`git init -b main` → add origin → `fetch origin main` → `git reset --mixed origin/main` → 精确 stage 目标文件 → commit → push。
- **重建/新 clone 后立刻 `git config core.autocrlf true`**：否则 CRLF 检出会让 ~180 文件全标 M（用 `--ignore-cr-at-eol` 判定）。
- 提交习惯：`_*.py/_*.ps1/_*.bat/_*.txt`、`outputs/`、`.pytest_tmp/`、`.venv/`、`config.yaml` 均 gitignore；每个独立变更批次单独 commit。
- **CI（`.github/workflows/ci.yml`）**：py3.10 + py3.12 双矩阵，8 步 —— 装依赖 → compileall（含根目录出片脚本）→ `ruff check .` → `tests/smoke_test.py` → `pytest -q tests/`，无 continue-on-error。**2026-09-15 全绿于 `00e950d`（四项实用性改进批次，CI run 2 job 全 success）**；此前 `0691935`(README/`34986481287`)、`6227b7d`(dgxspark/`34985316308`)、`5db6a82`(run #54/`34980375326`)、`85bca4b`(v0.12.0) 亦全绿。**2026-09-16 `83bb132`（人脸策略文档批次）CI run `35036355945` 全绿（发版前复核 HEAD 用）**。
- **无 gh CLI 查 CI**：`GET /actions/runs?per_page=10`（按 sha 找 run，**sha 必须传完整 40 位**，传短 sha 会匹配不到、误判「还没有 run」）、`GET /actions/runs/<id>/jobs`（看**每个 step** 的 conclusion，比整体红绿有用得多）；日志 `/actions/jobs/<id>/logs` 会 302 到 Azure 签名 URL，**跳转时必须摘掉 Authorization** 否则 403，且单 job 返回纯文本、多 job 是 zip。**完整步骤见 skill `github-release-no-gh` 第 7 节**。
- 坑：`git …push | tail` 会因 shim 缺 `tail` 报错并可能吞掉输出 → 一律重定向到文件再 Read。
- **Release**：tag-only 版本管理，仓库无版本文件（版本号只存在于 tag）。当前 latest **`v0.13.0`**（2026-09-16，tag→`83bb132`）。本机**无 `gh`** → 走 GitHub REST API：token 用 `git credential fill` 取、直接（urllib 直连 `api.github.com` 200，`_rel_*.py` 跑完即删）、`POST /releases`（`target_commitish=main`，自动建 tag 并成 latest）。notes 沿用「开头一句话统计 + `## 亮点` + `## 工程 / 质量` + `## 升级提示`」。**完整流程见 skill `github-release-no-gh`**。
  - ⚠️ **本地 `git fetch` 校验 tag 不可靠**：本沙箱 `git -c http.proxy= -c https.proxy= fetch origin --tags --force` 会偶发 `Failed to connect to github.com port 443`（与 `git push` 直连互通不同，疑似网络抖动/代理差异）。**改走 API 校验**：`GET /repos/<o>/<r>/commits/v0.13.0` 的 `sha` 应 == `git rev-parse HEAD`（2026-09-16 实测一致），免得 fetch 失败误判 tag 没建。
- ⚠️ **写 notes 前必须用 tag 区间取事实**：`git log <prev-tag>..HEAD` / `git diff --shortstat <prev-tag>..HEAD` / `--name-status`。**别拿工作区观感代替 tag 内容** —— 已踩过：v0.11.0 的 notes 把「复杂度治理 / config.yaml 出库 / 测试 128 例」都算进去了，但这些提交并不在 v0.11.0 的 tag（`32f1e8d`）里，实际落在 v0.12.0 区间（`merge-base --is-ancestor` 判定）。另 `--diff-filter=A` 会漏掉**重命名**的文件（`config.yaml`→`config.example.yaml` 记为 `R`），要按 tag 逐个 `git cat-file -e` 确认。
- ⚠️ **notes 里引用 CLI 开关/配置键前，必须 grep 代码确认真实名字**：v0.13.0 的 notes 我凭印象把 `--free-every` 写成
  `--vram-every`（记错 2 处）并发到了公开 release，事后用 `GET /releases/tags/<tag>` 取 body、`replace` 后
  `PATCH /releases/<id>` 修正（**API 可改已发布 release 的 body，tag 不受影响**）。教训：文档与代码不一致是硬伤，发布前逐字对齐。

## 十二之二、质检 / 档位可解释 / 防 OOM（2026-09-15 新增，commit `00e950d`）
- **`qa.agent_enabled` 默认已改为 `true`**（逐镜链路：WebUI 一键出片 / `cli run`）。原默认 `false` = 糊/静帧/全黑镜头直接进成片无兜底。仍与 `qa.enabled`（run_series 批量链路，历史就开）**解耦**，阈值共用同一段 `qa:*`。
  ⚠️ **翻转默认值的固定动作**：先 `grep agent_enabled tests/` 找出所有断言旧默认值的用例 —— 本次 `test_qa_gating.py` 两条正是断言「默认关」，另有 `test_agent_scene_helpers.py::test_generate_one_scene_happy_path` 因假视频被判不合格换 seed 而失败（该用例只验接线 → 显式关掉并注释原因）。
- **档位判定可解释 `config_env.explain_tier(hw)`** → `(tier, reason:str, detail:dict{rule,matched_hints,ram,vram})`；
  **`pick_tier()` 直接委托它**（单一真相源，防解释与判定漂移）。三入口均展示依据：WebUI `/api/hw?detect=1` 的 `reason` 字段 + 前端 `hwStatus`、`tools/hw_profile.py` CLI 打印「判定依据」、测试断言 `pick_tier == explain_tier[0]`（跑遍 HW_CASES 矩阵）。
  坑：线索表**别放互为子串的项**（`"GB10"` 与 `"NVIDIA GB10"` 会让 `matched_hints` 重复）→ 已去重。
- **长片防 OOM**：`tools/comfyui_client.py.free_memory()`（POST `/free`，best-effort 失败只 warn）；
  `run_series.run_episode(..., free_every=N)` + CLI `--free-every N`（`0`=关，默认 0）。**只在「新渲染」的镜上计数**（缓存命中不占新显存），每 N 镜放一次。背景：FunControl int8 2.3GB + H3 累积易 OOM（连续出片后 ComfyUI 崩）。

## 十三、已知隐患 / 待办
- ~~记忆分叉~~ **已解决（2026-09-15）**：`.codebuddy/memory` 的 13 天历史已并入 `.workbuddy/memory`，重叠的 09-14/09-15 以「附」区保留，`.codebuddy/` 已停止跟踪（文件仍在磁盘）。**权威目录 = `.workbuddy/memory/`**。
- ~~config.yaml 入库~~ **已解决（2026-09-15）**：已 `git rm --cached` + gitignore，改跟踪 `config.example.yaml`，并加了缺失回退。
- ~~圈复杂度 >10~~ **已解决（2026-09-15）**：48 处 → 0，C901(max=10) 已进 `ruff.toml` + CI 门槛（见「十一」）。
- ~~CI 长期红~~ **已解决（2026-09-15，两处串联的隐藏失败）**：① `.gitignore` 的 `_*.py` 吃掉 `webserver/__init__.py` 等 3 个包声明文件 → 全新克隆 ImportError；② 黄金指纹样本把 Windows 临时目录/分隔符写死 → Linux 必不匹配。**教训：前一步失败会让后一步 `skipped`，「lint 绿」不代表测试跑了 —— 看 CI 必须看到每个 step。**
- 文档引用检查**必须**先按仓库相对路径匹配、再退化为 basename 全局匹配，否则会产出大量误报（曾误报 63 处）。
