# Windows deployment

This project has been installed for native Windows use. It does not require WSL.

## Start

1. Start `F:\MinimaxH3-v260808\start_no_pause.bat` and wait until ComfyUI is
   reachable at `http://127.0.0.1:8188`.
2. Run `start_webui_windows.bat` in this directory.
3. Open `http://127.0.0.1:8000`.

## Ollama

The default LLM profile points at `http://127.0.0.1:11434/v1` with model
`qwen2.5:3b` and `llm.disabled: false`. To use a larger local model, run:

```bat
ollama pull qwen2.5:7b
```

then set `llm.model` in `config.yaml` (or add a profile in the WebUI's
「接口与模型设置」 tab). Do not run MiniMax H3 and an Ollama model at the same
time on a 16GB GPU unless you have confirmed enough free VRAM.

## Engine (G stage)

The video engine is selected by `engine.backend`; the default is
`comfyui_mmH3` — MiniMax H3 running on ComfyUI at `http://127.0.0.1:8188` —
so `run` and `pipeline` produce final generated video out of the box.
`comfyui_ltx` (LTX-2.5) and `skyreels` (SkyReels-V2) are also available.

H3 parameters and model filenames live under `engine.comfyui_mmH3` in
`config.yaml`; fetch the weights with `python download_mmh3_models.py`
(`download_ltx_models.py` for LTX-2.5). `python make_mmh3_workflow.py`
regenerates the ComfyUI workflow JSON (`workflows/mmh3_turbo_4v8a_ui.json`).

D-stage keyframes come from the same ComfyUI server: save a text-to-image
workflow in API format and point `image_prompt.comfyui.workflow` at its
absolute Windows path.

## Blender white-model (blocking) pipeline

`blender.enabled` is `true` by default and wires the Blender blocking renderer
(MCP on `127.0.0.1:9876`) into the video engine: preview / line / depth /
normal control images plus a blocking walk sequence are fed to H3's Fun Control
to lock character blocking. See `docs/h3_blocking_guide.md`; drive it from the
WebUI 「白模模块」 tab, `python cli.py blender`, or `python cli.py mmh3`.

## Useful commands

```bat
.venv-win\Scripts\python.exe cli.py init
.venv-win\Scripts\python.exe cli.py enrich-bible
.venv-win\Scripts\python.exe cli.py status
```
