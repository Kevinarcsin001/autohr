FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# faster-whisper(CTranslate2 int8 CPU) + fastapi + requests(模型下载用)
RUN pip install --no-cache-dir \
    faster-whisper==1.0.3 \
    fastapi \
    uvicorn \
    python-multipart \
    pydantic \
    requests

# 模型启动时按需下载到 /app/models(首次拉取 small ≈ 461MB)
ENV ASR_MODEL=small \
    ASR_DEVICE=cpu \
    ASR_COMPUTE_TYPE=int8 \
    ASR_MODEL_DIR=/app/models \
    ASR_VAD_ENABLED=true

RUN mkdir -p /app/models

COPY server.py /app/server.py

EXPOSE 8010

HEALTHCHECK --interval=30s --timeout=10s --start-period=180s --retries=3 \
    CMD curl -sf http://localhost:8010/health || exit 1

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8010"]
