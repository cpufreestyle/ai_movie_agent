# Sol-H3-Spark 集成指南（ai_movie_agent 视频引擎后端）

把 NVIDIA 官方 [Sol-H3-Spark](https://nvlabs.github.io/Sana/Sol-Engine/Sol-H3-Spark/)
（Speed-of-Light MiniMax H3 on Single DGX Spark）作为 ai_movie_agent 的一个**正式引擎后端**
`engine.backend: sol_h3`，集成进项目部署框架（`deploy.py`）。

## 适用场景

- 你有一台 **DGX Spark（GB10 Grace Blackwell，119GB 统一内存）**，想用它本地出片。
- 本机 agent（Windows/macOS/Linux，可无显卡）经 **HTTP** 调用 DGX Spark 上的常驻服务出视频，
  与「远程 ComfyUI」同解耦思路。

## 架构

```
本机 ai_movie_agent
  agent/sol_h3_engine.py  ──HTTP──►  DGX Spark (GB10)
                                └─ sol_h3_server.py  ──subprocess──►  infer.py (Sana sol-engine)
                                                                      └─ mp4 (1344x768/121帧/24fps)
```

- `tools/sol_h3_client.py`：HTTP 客户端（仿 `comfyui_client` 风格，内网直连绕代理）。
- `agent/sol_h3_engine.py`：引擎后端，与 `LTXEngine`/`MMH3Engine` 同接口，agent.py 透明替换。
- `deploy/sol_h3_spark/sol_h3_server.py`：DGX 侧 HTTP 服务（封装 `infer.py`）。
- `deploy/sol_h3_spark/deploy_sol_h3_spark.sh`：DGX 侧一键部署脚本。

## 一、DGX Spark 侧部署

见 `deploy/sol_h3_spark/README.md`。要点：

```bash
scp -r deploy/sol_h3_spark/ user@<DGX-IP>:~/sol_h3_deploy
ssh user@<DGX-IP>
cd ~/sol_h3_deploy
export HF_TOKEN=hf_xxx            # 已接受 LTX-2.5 条款
export REPO_DIR=$HOME/Sol-H3-Spark SERVER_PORT=8000
bash deploy_sol_h3_spark.sh
```

脚本按 `docs/setup.md` 编排：`git clone` Sana(sol-engine) → 建 Stage1/Stage2/Qwen 三环境
（**aarch64 编译 + 官方未验证，可能需手动微调**）→ `prepare.py` 生成 `paths*.json` →
`cache_builder` 生成通用上下文缓存 → `download_checkpoints.py` 下权重 → 后台启动服务。

自检：`curl http://127.0.0.1:8000/health` 应返回 `{"status":"ok",...}`。

## 二、本机 agent 接入

`config.yaml`：

```yaml
engine:
  backend: sol_h3            # 或 stage_profiles.G.engine: sol_h3
  sol_h3:
    api: http://<DGX-IP>:8000
    paths: paths.json        # t2va；fl2va/ref2va 由引擎按任务自动选
    seed: 0
    timeout: 1800
    resolution: 1344x768     # 只读展示（Sol-H3 固定）
    fps: 24
    num_frames: 121
```

或环境变量：`ENGINE_BACKEND=sol_h3` + `SOL_H3_API=http://<DGX-IP>:8000`。

`python deploy.py` 会打印方案，并在 `engine == sol_h3` 时提示「在 DGX Spark 部署 Sol-H3」的远程步骤。

## 三、任务映射（agent 输入 → Sol-H3 任务）

| agent 输入 | Sol-H3 任务 | 底层参数 |
|---|---|---|
| 仅 prompt | `t2va` | — |
| 首帧 image | `fl2va` | `--first-frame` |
| 参考图/视频 ref_images/ref_video | `ref2va` | `--reference image:/video:` |
| 首帧 + 参考 | `fl2va` + 附参考（best-effort） | — |

> Sol-H3 无 `prev_clip` 续写概念，引擎静默忽略（同 MMH3 行为）。
> 分辨率/帧数/帧率固定 1344×768/121/24，模型常驻；这些与 ComfyUI 版 H3 不同。

## 四、排障

- 服务未就绪：先看 DGX 上 `sol_h3_server.log`；`/generate` 报错会把 `infer.py` 的 STDERR 尾回传。
- `paths*.json` 缺失：说明 `prepare.py` 步骤未完成（需三环境就绪）。
- 延迟高：初版每请求重启 `infer.py`（模型重载）。后续可升级为 import `runtime.pipeline` 常驻复用模型。

## 五、风险

官方明确 "A clean installation has not yet been validated"。DGX Spark 是 aarch64，
CUDA 扩展（`fastvideo-kernel`、`all2all_cpp`）需源码编译，可能踩坑，预留数小时排错。
权重 + prompt cache 体量较大，需稳定网络与 >200GB 磁盘。
