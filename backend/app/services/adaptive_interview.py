"""渐进式自适应面试服务（M1：规则式选题 + LLM 对照评分）。

第一性原理：每一题都应基于「当前已知信息」做最优选择——
- 答得好（4-5）→ 同分支难度 +1（探上限）
- 一般（3）     → 同分支换考点（再验证）
- 差（1-2）     → 标记薄弱，换下一分支（止损）
- 预算/回合上限 → 收口

账本即真源：``interview_turns`` 每题一行（题目/回答/评分/证据/决策），
分支状态全部由 turns 推导，不另存会话级中间状态。

升级位：
- M2 音频：``submit_answer`` 增加音频入口，Celery 转写后回填 answer_text
- M3 CAT：``decide_next_action`` 替换为能力估计 + Fisher 信息选题
  （rating_evidence JSONB 已留能力快照位）
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.llm import LLMError, LLMResponse, LLMRouter, LLMSchemaError, Message
from app.core.logging import get_logger
from app.core.middleware.error_handler import NotFoundError
from app.core.middleware.error_handler import ValidationError as AppValidationError
from app.models.candidate import Candidate
from app.models.interview import InterviewSession, InterviewTurn
from app.models.job import Job
from app.models.question_bank import QuestionBankItem, QuestionCategory
from app.schemas.adaptive import (
    AdaptiveAnswerOut,
    AdaptiveNextOut,
    AdaptiveStartOut,
    AdaptiveStateOut,
    BranchState,
    SignalItem,
    TurnOut,
    TurnRating,
)
from app.services.question_bank import (
    QuestionBankService,
    _signal_match_strength,
    compute_dynamic_quotas,
)

logger = get_logger(__name__)

# ============================================================================
# 常量（策略旋钮）
# ============================================================================

_MAX_TURNS: int = 12
"""单场最大回合数（硬上限）。"""

_BRANCH_BUDGET: int = 3
"""每分支最大题数（深挖预算）。"""

_DEFAULT_DIFFICULTY: int = 3
"""分支起始难度。"""

_INTERVIEW_SCOPE: str = "interview"
_RATING_TEMPERATURE: float = 0.1
"""评分 temperature：低随机性，保证同答案评分稳定。"""

_MAX_ANSWER_CHARS: int = 6000
"""送入评分 prompt 的回答截断上限。"""


class AdaptiveInterviewError(Exception):
    """自适应面试可重试失败（LLM 不可用等）。"""


# ============================================================================
# 规则引擎（纯函数，单测友好）
# ============================================================================


_PRIOR_WEIGHT: float = 5.0
"""能力估计的先验强度（相当于一次 perf=0.5、难度和为 5 的伪观测）。"""


def estimate_branch_ability(ratings: list[tuple[int, int]]) -> float | None:
    """CAT 能力估计：难度加权的表现均值 θ ∈ [0,1]（带先验收缩）。

    Args:
        ratings: [(rating 1-5, difficulty 1-5)] —— 该分支全部已评分回合

    数学：perf = rating/5；高难度回合权重更高（d4 答好比 d2 更能证明能力）；
    加先验（perf=0.5、权重 5）防单回合小样本跳变：
        θ = (Σ perf·d + 0.5·PRIOR) / (Σ d + PRIOR)
    例：5@d3 → (1.0·3+2.5)/8 = 0.69（目标难度 4，稳健提升而非直冲 5）。
    M3 如需完整 IRT 可在此替换为 Newton-Raphson 求 MLE，接口不变。
    """
    if not ratings:
        return None
    num = sum((r / 5.0) * d for r, d in ratings)
    den = sum(d for _, d in ratings)
    if den <= 0:
        return None
    return round((num + 0.5 * _PRIOR_WEIGHT) / (den + _PRIOR_WEIGHT), 3)


def target_difficulty(theta: float | None) -> int:
    """CAT 选题难度：Fisher 信息在答对率 P≈0.5 时最大 → 题目难度 ≈ 能力水平。

    θ ∈ [0,1] 线性映射难度 b = 1 + θ·4，夹 [1,5]；无估计时默认 3。
    """
    if theta is None:
        return _DEFAULT_DIFFICULTY
    return max(1, min(5, round(1 + theta * 4)))


def decide_next_action(
    *,
    rating: int,
    branch_turns: int,
    last_difficulty: int,
    branch_has_items: bool,
    total_turns: int,
    branch_theta: float | None = None,
) -> dict:
    """根据最新回合评分决定下一步。

    Args:
        rating: 最新回合评分 1-5
        branch_turns: 当前分支已问题数（含最新）
        last_difficulty: 当前分支最后一题难度（1-5）
        branch_has_items: 当前分支是否还有未问过的题
        total_turns: 总回合数（含最新）
        branch_theta: 分支能力估计（CAT）；提供时 deepen/retry 的目标难度
            改由 ``target_difficulty(θ)`` 决定（信息增益最大），否则回退旧规则

    Returns:
        ``{"action": deepen|retry|switch|complete, "reason": str,
           "difficulty": int, "weak"?: bool, "theta"?: float}``。
    """
    if total_turns >= _MAX_TURNS:
        return {
            "action": "complete",
            "reason": f"已达最大回合数（{_MAX_TURNS}）",
            "difficulty": 0,
        }

    theta_d = target_difficulty(branch_theta) if branch_theta is not None else None

    if rating >= 4:
        if branch_turns < _BRANCH_BUDGET and last_difficulty < 5 and branch_has_items:
            diff = theta_d if theta_d is not None else last_difficulty + 1
            return {
                "action": "deepen",
                "reason": f"回答优秀（{rating}/5），按能力估计（θ={branch_theta}）选难度 {diff} 深挖",
                "difficulty": diff,
                **({"theta": branch_theta} if branch_theta is not None else {}),
            }
        return {
            "action": "switch",
            "reason": "当前分支已充分验证，切换下一分支",
            "difficulty": 0,
        }

    if rating == 3:
        if branch_turns < _BRANCH_BUDGET and branch_has_items:
            diff = theta_d if theta_d is not None else last_difficulty
            return {
                "action": "retry",
                "reason": f"回答一般（3/5），同分支换考点再验证（目标难度 {diff}）",
                "difficulty": diff,
                **({"theta": branch_theta} if branch_theta is not None else {}),
            }
        return {
            "action": "switch",
            "reason": "回答一般且分支预算用尽，切换下一分支",
            "difficulty": 0,
        }

    # rating <= 2：标记薄弱止损
    return {
        "action": "switch",
        "reason": f"回答较弱（{rating}/5），标记薄弱分支，切换下一分支",
        "difficulty": 0,
        "weak": True,
    }


# ============================================================================
# Service
# ============================================================================


class AdaptiveInterviewService:
    """渐进式自适应面试：启动 / 状态 / 答题评分 / 下一题。"""

    def __init__(self, db: AsyncSession, *, router: LLMRouter | None = None) -> None:
        self._db = db
        self._router = router
        self._qbs = QuestionBankService(db)

    def _get_router(self) -> LLMRouter:
        if self._router is not None:
            return self._router
        from app.adapters.llm import build_default_router

        self._router = build_default_router()
        return self._router

    # ----- 会话与权限 -----

    async def _load_session(
        self, *, team_id: uuid.UUID, session_id: uuid.UUID
    ) -> InterviewSession:
        session = await self._db.get(InterviewSession, session_id)
        if session is None:
            raise NotFoundError(
                f"interview session {session_id} not found", resource="interview_session"
            )
        candidate = await self._db.get(Candidate, session.candidate_id)
        if candidate is None or candidate.team_id != team_id:
            raise NotFoundError(
                f"interview session {session_id} not found", resource="interview_session"
            )
        return session

    async def _list_turns(self, session_id: uuid.UUID) -> list[InterviewTurn]:
        result = await self._db.execute(
            select(InterviewTurn)
            .where(InterviewTurn.session_id == session_id)
            .order_by(InterviewTurn.seq.asc())
        )
        return list(result.scalars().all())

    async def _team_of_session(self, session: InterviewSession) -> uuid.UUID:
        candidate = await self._db.get(Candidate, session.candidate_id)
        if candidate is None or candidate.team_id is None:
            raise AppValidationError("候选人无团队")
        return candidate.team_id

    # ----- 启动 -----

    async def start(
        self,
        *,
        team_id: uuid.UUID,
        session_id: uuid.UUID,
        started_by: uuid.UUID,
    ) -> AdaptiveStartOut:
        """初始化 adaptive 会话：信号 → 分支计划 → 首题。

        幂等：已有 turns 视为已启动，返回当前状态（首题 = 待回答回合）。
        """
        session = await self._load_session(team_id=team_id, session_id=session_id)
        turns = await self._list_turns(session_id)
        if turns:
            pending = next((t for t in turns if t.answer_text is None), None)
            state = await self.state(team_id=team_id, session_id=session_id)
            return AdaptiveStartOut(
                session_id=session_id,
                mode=session.mode,
                signals=state.plan_signals,
                branches=state.branches,
                first_turn=TurnOut.model_validate(pending if pending else turns[-1]),
            )

        # 1) 信号（v2：JD 硬性 2.0 > JD 正文 1.5 > 简历 skills 1.0 > work_history 0.8）
        signals = await self._qbs.build_candidate_signals(
            team_id=team_id,
            candidate_id=session.candidate_id,
            job_id=session.job_id,
        )
        signal_pairs = [(s, w) for s, w in signals]

        # 2) 分支 = 题库分类，按亲和度排序
        categories = await self._qbs.list_categories(team_id=team_id, active_only=True)
        cat_tuples = [(c.id, c.slug, c.name, c.target_points) for c in categories]
        items_tags: dict[uuid.UUID, list[list[str]]] = {}
        for c in categories:
            cat_items = await self._qbs.list_items(
                team_id=team_id, category_id=c.id, active_only=True
            )
            items_tags[c.id] = [it.tags or [] for it in cat_items]
        _quotas, scores = compute_dynamic_quotas(cat_tuples, items_tags, signal_pairs)
        ordered = sorted(
            categories,
            key=lambda c: (-scores.get(c.id, 0.0), -c.target_points, c.sort_order),
        )

        session.mode = "adaptive"
        session.adaptive_plan = {
            "signals": [{"signal": s, "weight": w} for s, w in signal_pairs],
            "branches": [
                {
                    "category_id": str(c.id),
                    "category_name": c.name,
                    "score": scores.get(c.id, 0.0),
                }
                for c in ordered
            ],
            "started_by": str(started_by),
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        if session.interviewer_id is None:
            session.interviewer_id = started_by

        # 3) 首题：首个有题可出的分支
        first = await self._create_turn(
            session=session, turns=[], team_id=team_id,
            category_id=None, ordered=ordered,
            difficulty=_DEFAULT_DIFFICULTY, signals=signal_pairs,
        )
        if first is None:
            raise AppValidationError("题库无可用题目，无法启动自适应面试")
        await self._db.flush()

        logger.info(
            "adaptive_interview_started",
            session_id=str(session_id),
            branches=len(ordered),
            signals=len(signal_pairs),
            first_branch=first.category_name,
        )
        plan = session.adaptive_plan or {}
        return AdaptiveStartOut(
            session_id=session_id,
            mode="adaptive",
            signals=[SignalItem(**s) for s in plan.get("signals", [])],
            branches=[
                BranchState(
                    category_id=uuid.UUID(b["category_id"]),
                    category_name=b["category_name"],
                    score=b.get("score", 0.0),
                    status=(
                        "active"
                        if first.category_id and b["category_id"] == str(first.category_id)
                        else "pending"
                    ),
                )
                for b in plan.get("branches", [])
            ],
            first_turn=TurnOut.model_validate(first),
        )

    # ----- 状态 -----

    async def state_full(
        self, *, team_id: uuid.UUID, session_id: uuid.UUID
    ) -> AdaptiveStateOut:
        session = await self._load_session(team_id=team_id, session_id=session_id)
        turns = await self._list_turns(session_id)
        plan = session.adaptive_plan or {}
        signals = [SignalItem(**s) for s in plan.get("signals", [])]

        branches: list[BranchState] = []
        ability: dict[str, float] = {}
        diff_by_item = await self._difficulties_of_turns(turns)
        for b in plan.get("branches", []):
            cid = uuid.UUID(b["category_id"])
            b_turns = [t for t in turns if t.category_id == cid]
            theta = estimate_branch_ability([
                (
                    t.rating,
                    diff_by_item.get(
                        t.question_item_id, _DEFAULT_DIFFICULTY
                    ) if t.question_item_id else _DEFAULT_DIFFICULTY,
                )
                for t in b_turns
                if t.rating
            ])
            if theta is not None:
                ability[b["category_name"]] = theta
            rated = [t.rating for t in b_turns if t.rating is not None]
            avg = round(sum(rated) / len(rated), 2) if rated else None
            branches.append(
                BranchState(
                    category_id=cid,
                    category_name=b["category_name"],
                    score=b.get("score", 0.0),
                    status=self._branch_status(
                        b_turns,
                        is_current=bool(turns) and turns[-1].category_id == cid,
                    ),
                    turns_count=len(b_turns),
                    avg_rating=avg,
                )
            )

        done, done_reason = self._done_state(session, turns)
        return AdaptiveStateOut(
            session_id=session_id,
            mode=session.mode,
            status=session.status,
            total_turns=len(turns),
            answered_turns=sum(1 for t in turns if t.answer_text is not None),
            plan_signals=signals,
            branches=branches,
            turns=[TurnOut.model_validate(t) for t in turns],
            ability=ability,
            done=done,
            done_reason=done_reason,
        )

    @staticmethod
    def _branch_status(b_turns: list[InterviewTurn], *, is_current: bool) -> str:
        if not b_turns:
            return "active" if is_current else "pending"
        decision = b_turns[-1].next_decision or {}
        if decision.get("weak"):
            return "weak"
        if is_current and b_turns[-1].answer_text is None:
            return "active"
        if len(b_turns) >= _BRANCH_BUDGET:
            return "done"
        return "active" if is_current else "done"

    @staticmethod
    def _done_state(
        session: InterviewSession, turns: list[InterviewTurn]
    ) -> tuple[bool, str | None]:
        if session.status == "completed":
            return True, "会话已完成"
        if not turns:
            return False, None
        decision = turns[-1].next_decision or {}
        if decision.get("action") == "complete":
            return True, decision.get("reason", "面试完成")
        return False, None

    # state 的公开入口
    async def state(self, *, team_id: uuid.UUID, session_id: uuid.UUID) -> AdaptiveStateOut:
        return await self.state_full(team_id=team_id, session_id=session_id)

    # ----- 答题 + 评分 -----

    async def submit_answer(
        self,
        *,
        team_id: uuid.UUID,
        session_id: uuid.UUID,
        turn_id: uuid.UUID,
        answer_text: str,
    ) -> AdaptiveAnswerOut:
        session = await self._load_session(team_id=team_id, session_id=session_id)
        turn = await self._db.get(InterviewTurn, turn_id)
        if turn is None or turn.session_id != session_id:
            raise NotFoundError(f"turn {turn_id} not found", resource="interview_turn")
        if turn.answer_text is not None:
            raise AppValidationError("该回合已有回答，不可重复提交")

        turn.answer_text = answer_text.strip()
        turn.answered_at = datetime.now(timezone.utc)
        if session.status == "scheduled":
            session.status = "in_progress"

        rating_error: str | None = None
        try:
            await self._rate_and_decide(session=session, turn=turn)
        except AdaptiveInterviewError as exc:
            # 回答已保存；评分由 /next 自动重试
            rating_error = str(exc)[:300]
        await self._db.flush()
        return AdaptiveAnswerOut(turn=TurnOut.model_validate(turn), rating_error=rating_error)

    async def _rate_and_decide(
        self, *, session: InterviewSession, turn: InterviewTurn
    ) -> None:
        """LLM 对照 reference_answer 评分 → 规则引擎决策。失败抛 AdaptiveInterviewError。"""
        evidence = await self._llm_rate(session=session, turn=turn)
        turn.rating = evidence["rating"]
        turn.rating_evidence = {k: v for k, v in evidence.items() if k != "model"}
        turn.rating_model = evidence.get("model")

        team_id = await self._team_of_session(session)
        all_turns = await self._list_turns(session.id)
        b_turns = [t for t in all_turns if t.category_id == turn.category_id]

        last_difficulty = await self._turn_difficulty(turn)
        asked = {t.question_item_id for t in all_turns if t.question_item_id is not None}
        branch_has_items = await self._branch_has_unasked(
            team_id=team_id, category_id=turn.category_id, asked=asked
        )

        decision = decide_next_action(
            rating=int(turn.rating or 0) if turn.rating is not None else 0,
            branch_turns=len(b_turns),
            last_difficulty=last_difficulty,
            branch_has_items=branch_has_items,
            total_turns=len(all_turns),
            branch_theta=await self._branch_theta(b_turns),
        )
        if decision.get("weak") and turn.category_id is not None:
            decision["weak_category_id"] = str(turn.category_id)
        turn.next_decision = decision

    async def _branch_theta(self, b_turns: list[InterviewTurn]) -> float | None:
        """分支能力估计：拉取各回合题目难度 → estimate_branch_ability。"""
        pairs: list[tuple[int, int]] = []
        item_ids = [t.question_item_id for t in b_turns if t.question_item_id and t.rating]
        if not item_ids:
            return None
        diff_by_item: dict[uuid.UUID, int] = {}
        result = await self._db.execute(
            select(QuestionBankItem.id, QuestionBankItem.difficulty).where(
                QuestionBankItem.id.in_(item_ids)
            )
        )
        for iid, d in result.all():
            diff_by_item[iid] = d or _DEFAULT_DIFFICULTY
        for t in b_turns:
            if t.rating and t.question_item_id and t.question_item_id in diff_by_item:
                pairs.append((t.rating, diff_by_item[t.question_item_id]))
        return estimate_branch_ability(pairs)

    async def _difficulties_of_turns(
        self, turns: list[InterviewTurn]
    ) -> dict[uuid.UUID, int]:
        item_ids = [t.question_item_id for t in turns if t.question_item_id]
        if not item_ids:
            return {}
        result = await self._db.execute(
            select(QuestionBankItem.id, QuestionBankItem.difficulty).where(
                QuestionBankItem.id.in_(item_ids)
            )
        )
        return {iid: (d or _DEFAULT_DIFFICULTY) for iid, d in result.all()}

    async def _turn_difficulty(self, turn: InterviewTurn) -> int:
        if turn.question_item_id is None:
            return _DEFAULT_DIFFICULTY
        item = await self._db.get(QuestionBankItem, turn.question_item_id)
        if item is None or item.difficulty is None:
            return _DEFAULT_DIFFICULTY
        return item.difficulty

    async def _branch_has_unasked(
        self, *, team_id: uuid.UUID, category_id: uuid.UUID | None, asked: set[uuid.UUID]
    ) -> bool:
        if category_id is None:
            return False
        items = await self._qbs.list_items(
            team_id=team_id, category_id=category_id, active_only=True
        )
        return any(it.id not in asked for it in items)

    async def _llm_rate(self, *, session: InterviewSession, turn: InterviewTurn) -> dict:
        reference = ""
        if turn.question_item_id is not None:
            item = await self._db.get(QuestionBankItem, turn.question_item_id)
            reference = (item.reference_answer or "") if item else ""
        job = await self._db.get(Job, session.job_id)
        job_title = job.title if job else "-"

        messages = self._build_rating_messages(
            job_title=job_title,
            question=turn.question_text,
            reference=reference or "（无参考答案：按技术面试通用标准评估正确性与深度）",
            answer=(turn.answer_text or "")[:_MAX_ANSWER_CHARS],
        )
        router = self._get_router()
        try:
            response = await router.chat(
                messages=messages,
                response_schema=TurnRating,
                temperature=_RATING_TEMPERATURE,
                scope=_INTERVIEW_SCOPE,
            )
        except (LLMError, LLMSchemaError) as exc:
            logger.warning("adaptive_rating_llm_failed", error=str(exc)[:200])
            raise AdaptiveInterviewError(f"评分服务暂不可用：{str(exc)[:120]}") from exc

        parsed = self._safe_parsed(response)
        if parsed is None:
            raise AdaptiveInterviewError("评分输出无法解析为结构化结果")
        data = parsed.model_dump()
        data["model"] = response.model
        return data

    @staticmethod
    def _build_rating_messages(
        *, job_title: str, question: str, reference: str, answer: str
    ) -> list[Message]:
        system = (
            "你是严格的技术面试评分官。根据面试题、参考答案要点与候选人回答，输出纯 JSON 评分。\n"
            "评分标准（1-5）：\n"
            "5 = 覆盖绝大多数要点且有深度洞察/实战细节\n"
            "4 = 覆盖主要要点，有少量遗漏\n"
            "3 = 答对部分要点，深度一般\n"
            "2 = 仅触及边缘，存在明显错误\n"
            "1 = 未答到要点或完全错误\n"
            "注意：口语转写文本可能有冗余/重复，关注实质内容。\n"
            "JSON schema：{\"rating\": int, \"key_points_hit\": [str], \"key_points_missed\": [str], "
            "\"strengths\": [str], \"flaws\": [str], \"follow_up_suggestion\": str}"
        )
        user = (
            f"# 职位\n{job_title}\n\n# 面试题\n{question}\n\n"
            f"# 参考答案要点\n{reference}\n\n# 候选人回答（转写文本）\n{answer}\n\n请输出 JSON。"
        )
        return [Message(role="system", content=system), Message(role="user", content=user)]

    @staticmethod
    def _safe_parsed(response: LLMResponse) -> TurnRating | None:
        if isinstance(response.parsed, TurnRating):
            return response.parsed
        try:
            return TurnRating.model_validate_json(response.content)
        except ValidationError:
            return None

    # ----- 下一题 -----

    async def next_question(
        self, *, team_id: uuid.UUID, session_id: uuid.UUID
    ) -> AdaptiveNextOut:
        session = await self._load_session(team_id=team_id, session_id=session_id)
        turns = await self._list_turns(session_id)
        if not turns:
            raise AppValidationError("adaptive 会话未启动，请先调用 /adaptive/start")

        pending = next((t for t in turns if t.answer_text is None), None)
        if pending is not None:
            # 幂等：未回答的题即当前题
            return AdaptiveNextOut(turn=TurnOut.model_validate(pending))

        last = turns[-1]
        if last.rating is None:
            # submit 时评分失败的回合：此处自动重试
            await self._rate_and_decide(session=session, turn=last)
            await self._db.flush()

        decision = last.next_decision or {}
        if decision.get("action") == "complete":
            if session.status != "completed":
                session.status = "completed"
                await self._db.flush()
            return AdaptiveNextOut(
                done=True, done_reason=decision.get("reason"), decision=decision
            )

        plan = session.adaptive_plan or {}
        signals = [(s["signal"], float(s["weight"])) for s in plan.get("signals", [])]

        if decision.get("action") in ("deepen", "retry"):
            next_turn = await self._create_turn(
                session=session, turns=turns, team_id=team_id,
                category_id=last.category_id, ordered=None,
                difficulty=int(decision.get("difficulty") or _DEFAULT_DIFFICULTY),
                signals=signals,
            )
        else:  # switch
            ordered = await self._ordered_categories(plan)
            next_turn = await self._create_turn(
                session=session, turns=turns, team_id=team_id,
                category_id=None, ordered=ordered,
                difficulty=_DEFAULT_DIFFICULTY, signals=signals,
            )

        if next_turn is None:
            if session.status != "completed":
                session.status = "completed"
                await self._db.flush()
            return AdaptiveNextOut(
                done=True,
                done_reason="所有分支题目已用尽，面试完成",
                decision=decision,
            )
        await self._db.flush()
        return AdaptiveNextOut(turn=TurnOut.model_validate(next_turn), decision=decision)

    async def _ordered_categories(self, plan: dict) -> list[QuestionCategory]:
        cats: list[QuestionCategory] = []
        for b in plan.get("branches", []):
            c = await self._db.get(QuestionCategory, uuid.UUID(b["category_id"]))
            if c is not None and c.is_active:
                cats.append(c)
        return cats

    # ----- 选题 -----

    async def _create_turn(
        self,
        *,
        session: InterviewSession,
        turns: list[InterviewTurn],
        team_id: uuid.UUID,
        category_id: uuid.UUID | None,
        ordered: list[QuestionCategory] | None,
        difficulty: int,
        signals: list[tuple[str, float]],
    ) -> InterviewTurn | None:
        """创建下一回合。

        category_id 给定 → 指定分支出题（deepen/retry）；
        否则按 ordered 顺序找首个有题分支（switch/start）。
        """
        asked = {t.question_item_id for t in turns if t.question_item_id is not None}

        if category_id is not None:
            picked = await self._pick_item(
                team_id=team_id, category_id=category_id,
                difficulty=difficulty, asked=asked, signals=signals,
            )
            found: tuple[uuid.UUID, QuestionBankItem] | None = (
                (category_id, picked) if picked is not None else None
            )
        else:
            # switch/start：优先「从未问过」的分支；无 pending 时回访非薄弱且预算未用尽的分支
            visited = {t.category_id for t in turns if t.category_id is not None}
            weak = {
                uuid.UUID(d["weak_category_id"])
                for t in turns
                if (d := t.next_decision or {}).get("weak_category_id")
            }
            found = None
            for c in ordered or []:
                if c.id in weak:
                    continue
                b_turns = sum(1 for t in turns if t.category_id == c.id)
                if c.id in visited and b_turns >= _BRANCH_BUDGET:
                    continue
                picked = await self._pick_item(
                    team_id=team_id, category_id=c.id,
                    difficulty=difficulty, asked=asked, signals=signals,
                )
                if picked is not None:
                    found = (c.id, picked)
                    break

        if found is None:
            return None
        cid, picked = found

        cat = await self._db.get(QuestionCategory, cid)
        seq = (turns[-1].seq + 1) if turns else 1
        turn = InterviewTurn(
            session_id=session.id,
            seq=seq,
            question_item_id=picked.id,
            question_text=picked.question,
            dimension=picked.dimension,
            category_id=cid,
            category_name=cat.name if cat else "",
        )
        self._db.add(turn)
        return turn

    async def _pick_item(
        self,
        *,
        team_id: uuid.UUID,
        category_id: uuid.UUID,
        difficulty: int,
        asked: set[uuid.UUID],
        signals: list[tuple[str, float]],
    ) -> QuestionBankItem | None:
        """分支内选题：难度最接近 → 信号相关优先 → 5 分题优先（同分题多）。"""
        items = await self._qbs.list_items(
            team_id=team_id, category_id=category_id, active_only=True
        )
        cands = [it for it in items if it.id not in asked]
        if not cands:
            return None

        def relevance(it: QuestionBankItem) -> int:
            return sum(
                1
                for t in it.tags or []
                if any(_signal_match_strength(s, t) > 0 for s, _w in signals)
            )

        cands.sort(
            key=lambda it: (
                abs((it.difficulty or _DEFAULT_DIFFICULTY) - difficulty),
                -relevance(it),
                it.points,
            )
        )
        return cands[0]


__all__ = ["AdaptiveInterviewError", "AdaptiveInterviewService", "decide_next_action"]
