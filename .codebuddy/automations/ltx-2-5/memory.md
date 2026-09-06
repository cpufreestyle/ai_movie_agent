# 自动化：LTX-2.5 端到端生成 — 执行记录

## 最新执行（2026-09-01）
- **状态**: 权重就位 + 代码修复完成，但端到端生成因硬件显存不足未产出视频。
- **已完成**:
  1. 四个权重下载落盘（transformer 18.72GB / text_encoder 13.17GB / video_vae 1.47GB / audio_vae 0.36GB）。
  2. 修复 `agent/ltx_engine.py` 的 VAE 注入（两个独立 VAE 权重必须分别注入，否则 `LTXVEmptyLatentAudio` 抛 `latent_frequency_bins` 缺失）。
  3. config/launch_comfy 显存调优参数就绪。
- **阻塞**: RTX 5070 Ti 16GB 显存装不下 22B NVFP4（staged 17.8GB），采样 OOM；32GB 内存下 novram 也 CPU OOM。已穷尽所有显存优化组合。
- **结论**: 代码/配置层面已正确，瓶颈是硬件。需更小量化权重（int4/GGUF）或更大显存/内存。
- **见**: 长期记忆 `c:/Users/michael/CodeBuddy/ai_movie_agent/.codebuddy/memory/MEMORY.md`。

## 失败时的排查清单（如复跑）
- OOM 在 `SamplerCustomAdvanced` → 显存不足，不是工作流错误。
- 若报 `latent_frequency_bins` → VAE 未注入，检查 `config.comfyui_ltx.nodes` 或 `_resolve_vae_nodes` 连线推断。
- 跑 `cli.py ltx` 须注意 prompt 空格（用整体 ArgumentList 串）。
