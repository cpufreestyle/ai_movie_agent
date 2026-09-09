# AI 电影 Agent · WebUI 镜像
# 轻量：纯 Flask，无 torch；视频走外部 ComfyUI，LLM 走外部 Ollama。
FROM python:3.11-slim

WORKDIR /app

# 只装 Agent 自身依赖（不含 SkyReels 推理栈 / torch，视频与 LLM 都在容器外）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# 默认指向同 compose 的 ollama / comfyui 服务；可用环境变量覆盖（见 .env.example）
ENV ENGINE_BACKEND=comfyui_mmH3 \
    OLLAMA_URL=http://ollama:11434 \
    COMFYUI_API=http://comfyui:8188

CMD ["python", "webui.py", "--host", "0.0.0.0", "--port", "8000"]
