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
from app.core.config import settings as _settings
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

# ============================================================================
# 常量薄壳：运行时从 settings 读取（全部可 .env 覆盖）；此处的字面量仅作缺省
# ============================================================================


def _max_turns() -> int:
    return _settings.ADAPTIVE_MAX_TURNS


def _branch_budget(avg: float | None = None) -> int:
    base = _settings.ADAPTIVE_BRANCH_BUDGET
    return base + (_settings.ADAPTIVE_STRONG_EXTRA if (avg or 0) >= 4.0 else 0)


_DEFAULT_DIFFICULTY: int = 3
"""分支起始难度。"""

_INTERVIEW_SCOPE: str = "interview"
_RATING_TEMPERATURE: float = 0.1
"""评分 temperature：低随机性，保证同答案评分稳定。"""

_MAX_ANSWER_CHARS: int = 6000
"""送入评分 prompt 的回答截断上限。"""


def utc_now_iso() -> str:
    pass


def _is_skipped(turn: InterviewTurn) -> bool:
    """回合是否被面试官跳过（不计分、不再视为待答）。"""
    return bool((turn.rating_evidence or {}).get("skipped"))

    return datetime.now(timezone.utc).isoformat()


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
    branch_avg: float | None = None,
    branch_consecutive: int = 0,
    branch_followups: int = 0,
    followup_anchor: str | None = None,
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
    if total_turns >= _max_turns():
        return {
            "action": "complete",
            "reason": f"已达最大回合数（{_max_turns()}）",
            "difficulty": 0,
        }

    theta_d = target_difficulty(branch_theta) if branch_theta is not None else None
    budget = _branch_budget(branch_avg)

    # 广度守卫：同分支连问达阈值 → 强制 switch（可回访），防单分支黑洞
    if branch_consecutive >= _settings.ADAPTIVE_BREADTH_FORCE_SWITCH:
        return {
            "action": "switch",
            "reason": (
                f"广度守卫：同分支已连问 {branch_consecutive} 题，"
                f"强制切换分支保持覆盖面"
            ),
            "difficulty": 0,
        }

    if rating >= 4:
        # 内容追问优先（锚定候选人原话，比题库模板更深）
        if followup_anchor and branch_followups < _settings.ADAPTIVE_FOLLOWUP_QUOTA:
            return {
                "action": "followup",
                "reason": (
                    f"回答优秀（{rating}/5）且证据有可追问点"
                    f"（分支追问 {branch_followups}/{_settings.ADAPTIVE_FOLLOWUP_QUOTA}）"
                ),
                "difficulty": (theta_d or _DEFAULT_DIFFICULTY) + 1,
                "followup": True,
                **({"theta": branch_theta} if branch_theta is not None else {}),
            }
        if branch_turns < budget and last_difficulty < 5 and branch_has_items:
            diff = theta_d if theta_d is not None else last_difficulty + 1
            return {
                "action": "deepen",
                "reason": f"回答优秀（{rating}/5），按能力估计（θ={branch_theta}）选难度 {diff} 深挖",
                "difficulty": diff,
                **({"theta": branch_theta} if branch_theta is not None else {}),
            }
        return {
            "action": "switch",
            "reason": (
                f"分支预算用尽（{budget} 题，均分 {branch_avg}），切换下一分支"
                if branch_turns >= budget
                else "当前分支已充分验证，切换下一分支"
            ),
            "difficulty": 0,
        }

    if rating == 3:
        if branch_turns < budget and branch_has_items:
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
            pending = next(
                (t for t in turns if t.answer_text is None and not _is_skipped(t)), None
            )
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
        visited = {t.category_id for t in turns if t.category_id is not None}
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
            coverage=f"{len(visited)}/{len(plan.get('branches', [])) or 1}",
            followup_turns=sum(1 for t in turns if (t.rating_evidence or {}).get("is_followup")),
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
        if len(b_turns) >= _branch_budget():
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
        # 追问回合标记（计追问配额 + 前端样式区分）
        if turn.question_item_id is None:
            turn.rating_evidence = {**turn.rating_evidence, "is_followup": True}
        turn.rating_model = evidence.get("model")

        team_id = await self._team_of_session(session)
        all_turns = await self._list_turns(session.id)
        b_turns = [t for t in all_turns if t.category_id == turn.category_id]

        last_difficulty = await self._turn_difficulty(turn)
        asked = {t.question_item_id for t in all_turns if t.question_item_id is not None}
        branch_has_items = await self._branch_has_unasked(
            team_id=team_id, category_id=turn.category_id, asked=asked
        )

        # v2 决策输入：连问数(广度守卫) + 追问配额 + 追问锚点(内容感知)
        consecutive = 0
        for t in reversed(all_turns):
            if t.category_id == turn.category_id:
                consecutive += 1
            else:
                break
        branch_followups = sum(
            1 for t in b_turns if (t.rating_evidence or {}).get("is_followup")
        )
        followup_anchor = (turn.rating_evidence or {}).get("follow_up_suggestion") or (
            "；".join(((turn.rating_evidence or {}).get("strengths") or [])[:2]) or None
        )

        decision = decide_next_action(
            rating=int(turn.rating or 0) if turn.rating is not None else 0,
            branch_turns=len(b_turns),
            last_difficulty=last_difficulty,
            branch_has_items=branch_has_items,
            total_turns=len(all_turns),
            branch_theta=await self._branch_theta(b_turns),
            branch_avg=(
                round(sum(t.rating for t in b_turns if t.rating) / max(1, len([t for t in b_turns if t.rating])), 2)
                if any(t.rating for t in b_turns) else None
            ),
            branch_consecutive=consecutive,
            branch_followups=branch_followups,
            followup_anchor=followup_anchor,
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

    # ------------------------------------------------------------------
    # 内容追问生成（v2：锚定候选人原话，LLM 现场出题；失败回退题库）
    # ------------------------------------------------------------------

    async def _generate_followup(
        self, *, session: InterviewSession, turn: InterviewTurn
    ) -> str | None:
        """基于评分证据(follow_up_suggestion/亮点) + 候选人原话，生成一道追问。

        Returns:
            追问文本;失败返回 None(调用方回退题库选题，面试不中断)。
        """
        ev = turn.rating_evidence or {}
        anchor = (
            (ev.get("follow_up_suggestion") or "").strip()
            or "；".join((ev.get("strengths") or [])[:2])
        )
        if not anchor:
            return None
        answer = (turn.answer_text or "")[:3000]

        system = (
            "你是资深技术面试官。候选人刚答完一题且表现出色，请基于他的回答内容出一道"
            "更深的追问。要求：1) 必须锚定他回答中的具体表述(引用他的原话或提到的技术点)；"
            "2) 比原题深一档(追问原理/权衡/踩坑/badcase)；3) 一道题，口语化，40字以内；"
            "4) 只输出题干本身，不要任何前缀或解释。"
        )
        user = (
            f"# 原题\n{turn.question_text}\n\n# 候选人回答(节选)\n{answer}\n\n"
            f"# 评分证据中的可追问点\n{anchor}\n\n请输出追问："
        )
        router = self._get_router()
        try:
            resp = await router.chat(
                messages=[Message(role="system", content=system), Message(role="user", content=user)],
                temperature=_settings.ADAPTIVE_FOLLOWUP_TEMPERATURE,
                scope=_INTERVIEW_SCOPE,
                timeout=20.0,
            )
        except (LLMError, LLMSchemaError) as exc:
            logger.warning("followup_generate_failed", error=str(exc)[:150])
            return None
        text = (resp.content or "").strip().strip('"“”').replace("\n", " ")
        if not (6 <= len(text) <= 200):
            return None
        return text

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
        self,
        *,
        team_id: uuid.UUID,
        session_id: uuid.UUID,
        force_category_id: uuid.UUID | None = None,
        skip_current: bool = False,
    ) -> AdaptiveNextOut:
        """获取下一题。

        面试官控制权（副驾驶模式）：
        - ``skip_current``：跳过当前待答题（同分支换考点重新出题）
        - ``force_category_id``：无视系统决策，强制从指定分支出题
          （decision 记录 override 供回放审计）
        """
        session = await self._load_session(team_id=team_id, session_id=session_id)
        turns = await self._list_turns(session_id)
        if not turns:
            raise AppValidationError("adaptive 会话未启动，请先调用 /adaptive/start")

        plan = session.adaptive_plan or {}
        signals = [(s["signal"], float(s["weight"])) for s in plan.get("signals", [])]

        pending = next(
            (t for t in turns if t.answer_text is None and not _is_skipped(t)), None
        )
        if pending is not None:
            if skip_current:
                # 面试官跳过：当前题作废（不计分，标记 skipped），同分支换考点重新出题
                pending.rating = None
                pending.rating_evidence = {"skipped": True, "skipped_at": utc_now_iso()}
                pending.answer_text = None
                pending.next_decision = {
                    "action": "switch",
                    "reason": "面试官跳过本题",
                    "difficulty": 0,
                    "skipped": True,
                }
                await self._db.flush()
                turns = await self._list_turns(session_id)
            else:
                # 幂等：未回答的题即当前题
                return AdaptiveNextOut(turn=TurnOut.model_validate(pending))

        if force_category_id is not None:
            # 面试官手动指定分支（无视上一题决策；仅受回合上限约束）

            if len(turns) >= _max_turns():
                if session.status != "completed":
                    session.status = "completed"
                    await self._db.flush()
                return AdaptiveNextOut(
                    done=True,
                    done_reason=f"已达最大回合数（{_max_turns()}）",
                    decision={"action": "switch", "reason": "面试官指定分支出题", "override": True},
                )
            next_turn = await self._create_turn(
                session=session, turns=turns, team_id=team_id,
                category_id=force_category_id, ordered=None,
                difficulty=_DEFAULT_DIFFICULTY, signals=signals,
            )
            if session.status == "completed":
                session.status = "in_progress"  # 手动出题重新打开
                await self._db.flush()
            if next_turn is None:
                raise AppValidationError("该分支没有可出的题目")
            await self._db.flush()
            return AdaptiveNextOut(
                turn=TurnOut.model_validate(next_turn),
                decision={"action": "switch", "reason": "面试官指定分支出题", "override": True},
            )

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

        if decision.get("action") == "followup":
            # v2 内容追问：LLM 锚定原话生成；失败无感回退题库同分支深挖
            q_text = await self._generate_followup(session=session, turn=last)
            if q_text:
                seq = turns[-1].seq + 1
                # 锚点存证：取评分建议/首个亮点（前端展示"基于你的回答"）
                anchor = (
                    (last.rating_evidence or {}).get("follow_up_suggestion")
                    or "；".join(((last.rating_evidence or {}).get("strengths") or [])[:1])
                )
                ft = InterviewTurn(
                    session_id=session.id,
                    seq=seq,
                    question_item_id=None,
                    question_text=q_text,
                    dimension=last.dimension,
                    category_id=last.category_id,
                    category_name=last.category_name,
                    rating_evidence={"is_followup": True, "anchor_quote": (anchor or "")[:200]},
                )
                self._db.add(ft)
                await self._db.flush()
                return AdaptiveNextOut(turn=TurnOut.model_validate(ft), decision=decision)
            # 生成失败 → 回退 deepen 选题
            decision = {**decision, "action": "deepen",
                        "reason": decision["reason"] + "（追问生成失败，回退题库深挖）"}

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
                if c.id in visited and b_turns >= _branch_budget():
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
