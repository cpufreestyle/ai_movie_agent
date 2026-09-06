# Windows deployment

This project has been installed for native Windows use. It does not require WSL.

## Start

1. Start `F:\MinimaxH3-v260808\start_no_pause.bat` and wait until ComfyUI is
   reachable at `http://127.0.0.1:8188`.
2. Run `start_webui_windows.bat` in this directory.
3. Open `http://127.0.0.1:8000`.

## Ollama

Ollama is configured at `http://127.0.0.1:11434/v1`, but is disabled because
no model is installed. To enable local writing, run:

```bat
ollama pull qwen2.5:7b
```

Then set `llm.disabled` to `false` in `config.yaml`. Do not run MinimaxH3 and
an Ollama model at the same time on a 16GB GPU unless you have confirmed enough
free VRAM.

## MinimaxH3 integration

MinimaxH3's ComfyUI server can supply the D-stage keyframes. In its ComfyUI
page, save a text-to-image workflow in API format, then enter that JSON file's
absolute Windows path in `image_prompt.comfyui.workflow` in `config.yaml`.

The repository's G-stage engine calls SkyReels-V2's
`generate_video_df.py`. MinimaxH3 is not a drop-in SkyReels engine, so `run`
and `pipeline` will not create final generated video until the engine is
adapted to a specific MinimaxH3 ComfyUI video workflow. The WebUI,
world-building, storyboard, prompt generation, and concept-demo rendering work
without SkyReels.

## Useful commands

```bat
.venv-win\Scripts\python.exe cli.py init
.venv-win\Scripts\python.exe cli.py enrich-bible
.venv-win\Scripts\python.exe cli.py status
```
