# legacy/ — 已归档的历史脚本

这里放的是**已被主线取代**的一次性脚本，保留仅为查证历史实现，不再维护。
当前主力是 **MiniMax H3（默认）+ LTX-2.5** 两条路线；根目录只保留与之相关的脚本。

## 归档清单与替代关系

| 归档脚本 | 用途 | 现主线替代 |
|---|---|---|
| `run_wan22_multishot.py` | Wan2.2-TI2V-5B 多镜头 + 尾帧续写 + xfade 拼接 | `run_series.py`（H3 多集出片） |
| `run_wan22_scifi.py` | Wan2.2 科幻比赛片（1280x704） | `run_series.py` / `cli.py pipeline` |
| `run_wan22_test.py` | Wan2.2 单镜冒烟（832x480x49） | `python make_mmh3_workflow.py` + ComfyUI |
| `run_ltx23.py` | LTX-2.3（GGUF Q3_K_M）单镜 | `agent/ltx_engine.py`（LTX-2.5） |
| `run_ltx_gguf_test.py` | LTX GGUF 冒烟/排障 | 同上 |
| `verify_i2v.py` | I2V 产物校验（对比首帧与出片） | `diag_face_consistency.py` |
| `verify_ltx_graph.py` | 校验 LTX 工作流连线 | `verify_ltx_graph.py` 的逻辑已并入 `ltx_engine._build_workflow` |
| `download_ltx23_q3.py` | 下载 LTX-2.3 Q3_K_M 量化 | `download_ltx_models.py`（LTX-2.5 NVFP4） |
| `download_gguf_mt.py` | 单文件 GGUF 下载（写死 D:\ComfyUI） | `download_hf_mt.py`（通用、可配镜像/目录） |

## 仍在根目录、**没有**归档的 LTX 脚本

`run_ltx23_multishot.py` / `run_ltx25_multishot.py` 被 `webui.py` 直接调用
（`run_script(...)` 与 `importlib.import_module("run_ltx23_multishot").SHOTS`），属于 WebUI 上的可用功能，
故保留在根目录。若要下线 LTX-2.3 那条 WebUI 路线，需同时改 `webui.py` 的相应入口。

## 怎么运行这里的脚本

脚本里普遍 `import agent...` / `import tools...`，且大量路径写死 `D:/ComfyUI`
（如输出目录、日志路径）。移动后必须**在仓库根目录**执行，并把根目录加进模块搜索路径：

```bash
cd <仓库根目录>
PYTHONPATH=. python legacy/run_wan22_test.py          # Linux / macOS
$env:PYTHONPATH='.'; python legacy\run_wan22_test.py  # PowerShell
```

Wan2.2 / LTX-2.3 的权重与节点若已清理，这些脚本会因找不到模型而失败——属预期。
