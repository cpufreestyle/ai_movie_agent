# Sol-H3-Spark 部署（DGX Spark 远程视频引擎）

把 NVIDIA 官方 [Sol-H3-Spark](https://nvlabs.github.io/Sana/Sol-Engine/Sol-H3-Spark/)
（Speed-of-Light MiniMax H3 on Single DGX Spark）封装为常驻 HTTP 服务，供本机
`ai_movie_agent` 通过 `engine.backend: sol_h3` 远程出视频。

## 架构

```
本机 ai_movie_agent (Windows/macOS/Linux)
  └─ agent/sol_h3_engine.py  ──HTTP──►  DGX Spark (GB10)
                                      └─ sol_h3_server.py  ──subprocess──►  infer.py (Sana sol-engine)
                                                                            └─ 输出 mp4 (1344x768 / 121帧 / 24fps)
```

- 模型常驻在 DGX Spark；本机 agent 不带显卡也能出片（与「远程 ComfyUI」同解耦思路）。
- 任务自动判定：首帧→fl2va；参考图/视频→ref2va；两者皆有→fl2va+附参考；都无→t2va。

## 在 DGX Spark 上部署

```bash
# 1) 把本目录整体拷到 DGX Spark（或 git 同步 ai_movie_agent 后直接用仓库内路径）
scp -r deploy/sol_h3_spark/ user@<DGX-IP>:~/sol_h3_deploy

# 2) SSH 上去执行
ssh user@<DGX-IP>
cd ~/sol_h3_deploy
export HF_TOKEN=hf_xxx                       # 已接受 LTX-2.5 条款的 token
export REPO_DIR=$HOME/Sol-H3-Spark SERVER_PORT=8000
bash deploy_sol_h3_spark.sh
```

脚本按 `docs/setup.md` 编排：克隆 Sana(sol-engine) → 建三环境（Stage1/Stage2/Qwen，
**未验证步骤，可能需手动微调**）→ `prepare.py` 生成 `paths*.json` → `cache_builder`
→ `download_checkpoints.py` 下权重 → 后台启动 `sol_h3_server.py`。

> ⚠️ NVIDIA 声明 "A clean installation has not yet been validated"。权重 + prompt cache
> 体量较大，需稳定网络与充足磁盘（建议 >200GB）。

## 本机 agent 接入

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

或用环境变量：`ENGINE_BACKEND=sol_h3` + `SOL_H3_API=http://<DGX-IP>:8000`。

`python deploy.py` 会打印方案（含「在 DGX Spark 部署 Sol-H3」的远程步骤）。

## 服务接口

- `GET /` 与 `GET /health` → `{"status":"ok","service":"sol_h3_spark"}`
- `POST /generate`（JSON）:
  ```json
  {"prompt":"...", "seed":0, "task":"auto",
   "first_frame":"<base64 png>",
   "references":[{"type":"image","data":"<base64>"},{"type":"video","data":"<base64>"}]}
  ```
  → `{"ok":true,"video_b64":"..."}` 或 `{"ok":false,"error":"..."}`

## 排障

- 服务未就绪：`curl http://<DGX-IP>:8000/health` 应返回 200；否则查 `sol_h3_server.log`。
- `paths*.json` 缺失：说明 `prepare.py` 步骤未完成（需三环境就绪）。
- 推理报错：日志会回传 `infer.py` 的 STDERR 尾；多为环境/权重未齐。
- 延迟高：初版每请求重启 `infer.py`（模型重载）。后续可升级为 import `runtime.pipeline`
  常驻复用模型。
