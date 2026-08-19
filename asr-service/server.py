"""ASR 微服务：faster-whisper + silero VAD(容器内,对齐 mineru 模式)。

职责：
- POST /transcribe: multipart 音频(webm/wav/mp3/m4a…) → 转写 {text, segments}
- GET  /health: 就绪探针(模型加载完成后才 healthy)

关键工程细节(Whisper 的坑)：
1. VAD 前置——静音段会诱发幻觉复读,silero-vad 是 faster-whisper 内建参数
2. initial_prompt 注入领域词——把「职位+技能关键词」作前缀,专有名词准确率显著提升
3. segments 保留时间戳——供 M2b 会后回捞按区间切题
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

MODEL_NAME = os.environ.get("ASR_MODEL", "small")
DEVICE = os.environ.get("ASR_DEVICE", "cpu")
COMPUTE_TYPE = os.environ.get("ASR_COMPUTE_TYPE", "int8")
MODEL_DIR = os.environ.get("ASR_MODEL_DIR", "/app/models")
VAD_ENABLED = os.environ.get("ASR_VAD_ENABLED", "true").lower() == "true"

MAX_AUDIO_MB = 50
MAX_INITIAL_PROMPT = 500

app = FastAPI(title="autohr-asr", version="1.0.0")
_model = None  # lazy 单例


def get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

        _model = WhisperModel(
            MODEL_NAME,
            device=DEVICE,
            compute_type=COMPUTE_TYPE,
            download_root=MODEL_DIR,
        )
    return _model


class Segment(BaseModel):
    start: float
    end: float
    text: str


class RangeResult(BaseModel):
    ok: bool
    text: str = ""
    segments: list[Segment] = []
    error: str | None = None


class TranscribeSegmentsResponse(BaseModel):
    """整段录制文件 + 多个时间区间 → 每区间一个转写结果（会后回捞 M2b）。"""

    ranges: list[RangeResult]
    language: str
    duration: float
    model: str


class TranscribeResponse(BaseModel):
    text: str
    segments: list[Segment]
    language: str
    duration: float
    model: str


@app.post("/transcribe-segments", response_model=TranscribeSegmentsResponse)
async def transcribe_segments(
    audio: UploadFile = File(...),
    ranges_json: str = Form(default="[]"),
    initial_prompt: str | None = Form(default=None),
) -> TranscribeSegmentsResponse:
    """上传整场录制，按 [[start_s, end_s], ...] 区间逐段转写（ffmpeg -ss/-to 切片）。

    用于 M2b 会后回捞：一次上传避免大文件重复传输；
    每段独立 VAD+转写，单段失败不影响其他段（ok=False + error）。
    """
    import json as _json

    data = await audio.read()
    if len(data) > MAX_AUDIO_MB * 1024 * 1024:
        raise HTTPException(413, f"audio too large (> {MAX_AUDIO_MB}MB)")
    try:
        ranges = [(float(s), float(e)) for s, e in _json.loads(ranges_json)][:100]
    except (ValueError, TypeError, _json.JSONDecodeError):
        raise HTTPException(400, "ranges_json must be [[start,end],...]")
    if not ranges:
        raise HTTPException(400, "empty ranges")
    prompt = (initial_prompt or "").strip()[:MAX_INITIAL_PROMPT] or None

    suffix = Path(audio.filename or "rec.mp4").suffix or ".mp4"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(data)
        tmp = f.name

    model = get_model()
    results: list[RangeResult] = []
    language = ""
    duration = 0.0
    try:
        import subprocess

        for start, end in ranges:
            if end <= start or end - start > 3600:
                results.append(RangeResult(ok=False, error=f"invalid range {start}-{end}"))
                continue
            seg_tmp = tempfile.mktemp(suffix=".wav")
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", tmp, "-ss", str(start), "-to", str(end),
                     "-ac", "1", "-ar", "16000", seg_tmp],
                    check=True, capture_output=True, timeout=300,
                )
                seg_iter, info = model.transcribe(
                    seg_tmp,
                    vad_filter=VAD_ENABLED,
                    vad_parameters={"min_silence_duration_ms": 500},
                    initial_prompt=prompt,
                    beam_size=5,
                )
                segs = [
                    Segment(start=float(s.start), end=float(s.end), text=s.text.strip())
                    for s in seg_iter
                    if s.text.strip()
                ]
                language = language or info.language
                duration = max(duration, float(info.duration or 0))
                results.append(
                    RangeResult(
                        ok=True,
                        text="".join(s.text + " " for s in segs).strip(),
                        segments=segs,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                results.append(RangeResult(ok=False, error=str(exc)[:200]))
            finally:
                try:
                    os.unlink(seg_tmp)
                except OSError:
                    pass
        return TranscribeSegmentsResponse(
            ranges=results, language=language, duration=duration, model=MODEL_NAME
        )
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "model": MODEL_NAME,
        "device": DEVICE,
        "compute_type": COMPUTE_TYPE,
        "vad": VAD_ENABLED,
        "loaded": _model is not None,
    }


@app.post("/transcribe", response_model=TranscribeResponse)
async def transcribe(
    audio: UploadFile = File(...),
    initial_prompt: str | None = Form(default=None),
) -> TranscribeResponse:
    data = await audio.read()
    if len(data) > MAX_AUDIO_MB * 1024 * 1024:
        raise HTTPException(413, f"audio too large (> {MAX_AUDIO_MB}MB)")
    if not data:
        raise HTTPException(400, "empty audio")
    prompt = (initial_prompt or "").strip()[:MAX_INITIAL_PROMPT] or None

    suffix = Path(audio.filename or "audio.webm").suffix or ".webm"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(data)
        tmp = f.name

    try:
        model = get_model()
        segments_iter, info = model.transcribe(
            tmp,
            vad_filter=VAD_ENABLED,
            vad_parameters={"min_silence_duration_ms": 500},
            initial_prompt=prompt,
            beam_size=5,
            word_timestamps=False,
        )
        segments = [
            Segment(start=float(s.start), end=float(s.end), text=s.text.strip())
            for s in segments_iter
            if s.text.strip()
        ]
        return TranscribeResponse(
            text="".join(s.text + " " for s in segments).strip(),
            segments=segments,
            language=info.language,
            duration=float(info.duration),
            model=MODEL_NAME,
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"transcribe failed: {exc}") from exc
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
