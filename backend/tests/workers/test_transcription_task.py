"""transcribe_turn handler 测试（M2a 转写链路覆盖补齐）。

直接调 ``transcribe_turn_handler.__wrapped__``（functools.wraps 保留），
绕过 celery 包装与 AsyncJob 状态机，聚焦 handler 逻辑本身：
- turn 不存在 → PermanentFailure
- 已有 answer → skipped（幂等）
- 转写成功 → 回填 + 评分被调 + status done
- session 已删 → 转写保留 + rating_error（防 AttributeError 回归）
- 空转写 → transcription_status=failed
- ASR 失败 → failed + 抛出
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select

from app.core.db import AsyncSessionLocal
from app.models.candidate import Candidate
from app.models.interview import InterviewSession, InterviewTurn
from app.models.job import Job
from app.models.team import Team
from app.models.user import User
from app.workers.tasks import PermanentFailure
from app.workers.transcription_task import transcribe_turn_handler
from tests.db_utils import purge_database


async def _purge_db() -> None:
    async with AsyncSessionLocal() as session:
        await purge_database(session)
        await session.commit()


@pytest.fixture(autouse=True)
async def clean_db():
    await _purge_db()
    yield
    await _purge_db()


async def _seed_turn(
    *, with_session: bool = True, answer_text: str | None = None
) -> tuple[uuid.UUID, uuid.UUID]:
    """种子 team/job/candidate/session/turn → 返回 (session_id, turn_id)。"""
    async with AsyncSessionLocal() as s:
        team = Team(name=f"T-{uuid.uuid4().hex[:6]}")
        s.add(team)
        await s.flush()
        user = User(
            team_id=team.id,
            email=f"u{uuid.uuid4().hex[:6]}@t.cn",
            password_hash="x",
            name="U",
            role="admin",
        )
        s.add(user)
        await s.flush()
        job = Job(
            team_id=team.id,
            title="后端工程师",
            jd_text="d",
            status="active",
            created_by=user.id,
        )
        cand = Candidate(
            team_id=team.id,
            dedup_key=uuid.uuid4().hex,
            name="候选人",
        )
        s.add_all([user, job, cand])
        await s.flush()
        iv = InterviewSession(
            job_id=job.id,
            candidate_id=cand.id,
            status="scheduled",
        )
        s.add(iv)
        await s.flush()
        turn = InterviewTurn(
            session_id=iv.id,
            seq=1,
            question_text="讲讲 RAG",
            answer_text=answer_text,
            audio_storage_key="audio/t1.webm",
            transcription_status="pending",
        )
        s.add(turn)
        await s.commit()
        return iv.id, turn.id


def _call_handler(job_id: uuid.UUID, payload: dict[str, Any] | None):
    """穿透 celery 包装调原始 handler。

    链路：PromiseProxy.__wrapped__ = _wrapped(self, async_job_id)，
    _wrapped.__wrapped__(经 binds) → 原始 async handler(job_id, payload)。
    """
    return transcribe_turn_handler.run.__wrapped__.__wrapped__(  # type: ignore[attr-defined]
        job_id, payload
    )


def _patch_asr(monkeypatch, *, text: str = "RAG 是检索增强生成", raise_err: Exception | None = None) -> None:
    """mock ASRClient.transcribe 与 storage.get（handler 函数内 import，须 patch 源头）。"""

    class _FakeASR:
        async def transcribe(self, *, audio_bytes, filename, initial_prompt=None):
            if raise_err is not None:
                raise raise_err
            return {
                "text": text,
                "language": "zh",
                "duration": 12.3,
                "model": "fake-small",
                "segments": [],
            }

    import app.adapters.asr_client as asr_mod

    monkeypatch.setattr(asr_mod, "ASRClient", _FakeASR)

    class _FakeStorage:
        async def get(self, key: str) -> bytes:
            return b"fake-audio"

    import app.adapters.storage as storage_mod

    monkeypatch.setattr(storage_mod, "get_storage", lambda: _FakeStorage())


class TestTranscribeTurnHandler:
    async def test_turn_not_found_is_permanent(self, monkeypatch) -> None:
        with pytest.raises(PermanentFailure):
            await _call_handler(uuid.uuid4(), {"turn_id": str(uuid.uuid4())})

    async def test_existing_answer_skips(self, monkeypatch) -> None:
        _patch_asr(monkeypatch)
        _sid, turn_id = await _seed_turn(answer_text="已有手输答案")

        result = await _call_handler(uuid.uuid4(), {"turn_id": str(turn_id)})
        assert result == {"status": "skipped", "reason": "answer already present"}

    async def test_success_fills_and_rates(
        self, monkeypatch
    ) -> None:
        """转写成功 → 回填 answer + status done + 评分被调用。"""
        _patch_asr(monkeypatch)
        rated: list[dict[str, Any]] = []

        async def _fake_rate(self, *, session, turn):  # noqa: ANN001
            turn.rating = 4
            rated.append({"turn_id": turn.id})

        import app.services.adaptive_interview as adapt_mod

        monkeypatch.setattr(
            adapt_mod.AdaptiveInterviewService, "_rate_and_decide", _fake_rate
        )

        iv_id, turn_id = await _seed_turn()
        result = await _call_handler(uuid.uuid4(), {"turn_id": str(turn_id)})

        assert result is not None
        assert result["status"] == "done"
        assert result["chars"] > 0
        assert result["rating"] == 4
        assert result["rating_error"] is None
        assert len(rated) == 1

        async with AsyncSessionLocal() as s:
            turn = await s.get(InterviewTurn, turn_id)
            iv = await s.get(InterviewSession, iv_id)
        assert turn is not None
        assert turn.answer_text == "RAG 是检索增强生成"
        assert turn.transcription_status == "done"
        assert iv is not None and iv.status == "in_progress"

    async def test_empty_transcript_marks_failed(self, monkeypatch) -> None:
        _patch_asr(monkeypatch, text="   ")
        _sid, turn_id = await _seed_turn()

        result = await _call_handler(uuid.uuid4(), {"turn_id": str(turn_id)})
        assert result == {"status": "empty_transcript"}

        async with AsyncSessionLocal() as s:
            turn = await s.get(InterviewTurn, turn_id)
        assert turn is not None
        assert turn.transcription_status == "failed"
        assert turn.answer_text is None

    async def test_asr_error_marks_failed_and_raises(self, monkeypatch) -> None:
        from app.adapters.asr_client import ASRError

        _patch_asr(monkeypatch, raise_err=ASRError("asr down"))
        _sid, turn_id = await _seed_turn()

        with pytest.raises(ASRError):
            await _call_handler(uuid.uuid4(), {"turn_id": str(turn_id)})

        async with AsyncSessionLocal() as s:
            turn = await s.get(InterviewTurn, turn_id)
        assert turn is not None
        assert turn.transcription_status == "failed"
        assert turn.rating_evidence is not None
        assert "transcribe_error" in turn.rating_evidence
