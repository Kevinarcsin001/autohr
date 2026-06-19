"""DedupService（任务 15）：候选人去重 / 合并。

算法（design.md ``### 6. DedupService``）：
- ``dedup_key = sha1(normalize(name) + last4(phone) + prefix(email, 4))``
- 命中 1 条：合并新简历到旧候选人；结构化字段按 confidence 取胜。
- 命中 ≥ 2 条：写 ``dedup_matches`` 标记 ``pending_review``，不自动合并。

归一化规则：
- name：去空白 + 全半角统一为半角 + 小写（拼音转换留待扩展，避免引入 pypinyin 依赖）
- phone：仅保留数字；取后 4 位
- email：小写 + 取 ``@`` 前缀前 4 字符

约束：
- 合并不删除原 candidate 行（设置 ``merged_into`` 实现软合并）
- ``sources`` / ``resumes`` / ``parsed_structures`` 的 ``candidate_id`` 全部转移到 dst
- 多对一冲突绝不自动合并（需求 12.3）
- 不修改候选人时返回原状（create_new 路径）
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.middleware.error_handler import NotFoundError, ValidationError
from app.models.candidate import (
    Candidate,
    CandidateResume,
    CandidateSource,
    ParsedStructure,
)
from app.models.dedup import DedupMatch
from app.schemas.candidate_structure import CandidateStructure

logger = get_logger(__name__)


# ============================================================================
# 常量
# ============================================================================


_PHONE_DIGITS_RE = re.compile(r"\d")
_NAME_KEEP_RE = re.compile(r"[\s　]+")  # 半角/全角空白


# ============================================================================
# 结果数据类
# ============================================================================


@dataclass(frozen=True)
class DedupResolution:
    """``resolve_new_candidate`` 的结果。

    - ``action='create_new'``：无匹配，调用方应新建候选人
    - ``action='merge_into'``：唯一精确匹配，调用方应把新 source/resume 挂到 ``primary``
    - ``action='flag_for_review'``：多对一疑似冲突，调用方应新建候选人 +
      调用 ``flag_for_review`` 写 dedup_match（service 内已写）
    """

    action: str
    primary: Candidate | None = None
    suspects: list[Candidate] | None = None
    dedup_key: str | None = None
    dedup_match_id: uuid.UUID | None = None


# ============================================================================
# DedupService
# ============================================================================


class DedupService:
    """候选人去重 / 合并。"""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ----- dedup_key 计算（纯函数，便于单测） -----

    @staticmethod
    def compute_dedup_key(
        name: str | None,
        phone: str | None,
        email: str | None,
    ) -> str:
        """计算 dedup_key = sha1(normalize(name) + last4(phone) + prefix(email, 4))。

        三字段全部缺失 → 抛 ValueError（必须至少一个标识符，否则无法去重）。
        """
        n = DedupService.normalize_name(name)
        p = DedupService.last4_phone(phone)
        e = DedupService.prefix_email(email, length=4)

        if not n and not p and not e:
            raise ValueError(
                "compute_dedup_key: 至少需要一个非空标识符（name/phone/email）"
            )

        raw = f"{n}|{p}|{e}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def normalize_name(name: str | None) -> str:
        """姓名归一化：NFKC 全半角统一 + 去所有空白 + 小写。

        NFKC 把全角字母数字 + 全角空格统一为半角；中文姓名不受影响。
        """
        if not name:
            return ""
        s = unicodedata.normalize("NFKC", str(name))
        s = _NAME_KEEP_RE.sub("", s)
        return s.lower()

    @staticmethod
    def last4_phone(phone: str | None) -> str:
        """提取手机号后 4 位数字。"""
        if not phone:
            return ""
        digits = "".join(_PHONE_DIGITS_RE.findall(str(phone)))
        return digits[-4:] if len(digits) >= 4 else digits

    @staticmethod
    def prefix_email(email: str | None, *, length: int = 4) -> str:
        """提取 email ``@`` 前缀的前 N 字符（小写）。"""
        if not email:
            return ""
        local = str(email).split("@", 1)[0]
        local = unicodedata.normalize("NFKC", local).lower()
        return local[:length]

    # ----- 查询 -----

    async def find_by_dedup_key(
        self, *, team_id: uuid.UUID, dedup_key: str
    ) -> Candidate | None:
        """按精确 dedup_key 查询同 team 内未合并的候选人。"""
        result = await self._db.execute(
            select(Candidate).where(
                Candidate.team_id == team_id,
                Candidate.dedup_key == dedup_key,
                Candidate.merged_into.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def find_similar(
        self,
        *,
        team_id: uuid.UUID,
        name: str | None,
        phone: str | None,
        email: str | None,
        exclude_id: uuid.UUID | None = None,
        limit: int = 10,
    ) -> list[Candidate]:
        """查找疑似同人的候选人（dedup_key 不同但部分标识符匹配）。

        匹配条件（name 归一化相等 + (phone 后 4 位 OR email prefix) 命中）：
        - 完全没 phone/email → 仅靠 name 归一化（弱匹配，给 HR 复核）
        - 有 phone/email → name + phone 或 name + email 任一命中
        """
        norm_name = self.normalize_name(name)
        if not norm_name:
            return []

        stmt = select(Candidate).where(
            Candidate.team_id == team_id,
            Candidate.merged_into.is_(None),
        )
        if exclude_id is not None:
            stmt = stmt.where(Candidate.id != exclude_id)

        result = await self._db.execute(stmt.limit(limit * 5))
        candidates = list(result.scalars().all())

        last4 = self.last4_phone(phone)
        email_prefix = self.prefix_email(email)

        suspects: list[Candidate] = []
        for c in candidates:
            # name 归一化不等 → 跳过（name 是首要门槛）
            if self.normalize_name(c.name) != norm_name:
                continue

            # 有 phone/email 标识符时，至少一个要命中
            if last4 or email_prefix:
                c_last4 = self.last4_phone(c.phone)
                c_prefix = self.prefix_email(c.email)
                if last4 and c_last4 == last4 and last4:
                    suspects.append(c)
                    continue
                if email_prefix and c_prefix == email_prefix and email_prefix:
                    suspects.append(c)
                    continue
            else:
                # 都没标识符，仅靠 name 弱匹配（标 pending 让 HR 判断）
                suspects.append(c)

        return suspects[:limit]

    # ----- 主入口：新候选人入库前的决议 -----

    async def resolve_new_candidate(
        self,
        *,
        team_id: uuid.UUID,
        name: str | None,
        phone: str | None,
        email: str | None,
    ) -> DedupResolution:
        """新候选人入库前的去重决议。

        返回三种 action：
        - ``create_new``：无匹配，调用方新建
        - ``merge_into``：唯一精确匹配，调用方挂到 primary
        - ``flag_for_review``：多对一疑似冲突；service 已写 dedup_match，
          调用方应新建独立 candidate（避免自动合并错配）
        """
        try:
            key = self.compute_dedup_key(name, phone, email)
        except ValueError:
            # 全部缺失 → 直接 create_new（如纯上传场景占位）
            return DedupResolution(action="create_new")

        primary = await self.find_by_dedup_key(team_id=team_id, dedup_key=key)
        if primary is not None:
            return DedupResolution(
                action="merge_into", primary=primary, dedup_key=key
            )

        # 精确未命中 → 查相似（避免同名不同 dedup_key 的同人被误判）
        suspects = await self.find_similar(
            team_id=team_id, name=name, phone=phone, email=email
        )

        if not suspects:
            return DedupResolution(action="create_new", dedup_key=key)

        # 多对一 / 单一相似 → 标 pending_review（不自动合并）
        # 注意：candidate_a/b 此时可能尚未入库（调用方在 create 后再调
        # ``link_as_suspect`` 写入；这里仅返回信号）
        return DedupResolution(
            action="flag_for_review",
            suspects=suspects,
            dedup_key=key,
        )

    async def flag_for_review(
        self,
        *,
        candidate_a: uuid.UUID,
        candidate_b: uuid.UUID,
        similarity: dict,
    ) -> DedupMatch:
        """写一条 pending dedup_match（不合并；等待 HR 决定）。"""
        if candidate_a == candidate_b:
            raise ValidationError("candidate_a 不能等于 candidate_b")

        # 规范化顺序：小 id 在前避免重复存储 (a,b) 与 (b,a)
        a, b = (
            (candidate_a, candidate_b)
            if str(candidate_a) < str(candidate_b)
            else (candidate_b, candidate_a)
        )

        match = DedupMatch(
            candidate_a=a,
            candidate_b=b,
            similarity=similarity,
            status="pending",
        )
        self._db.add(match)
        await self._db.flush()

        logger.info(
            "dedup_match_flagged",
            match_id=str(match.id),
            candidate_a=str(a),
            candidate_b=str(b),
        )
        return match

    # ----- 合并 -----

    async def merge(
        self, *, src_id: uuid.UUID, dst_id: uuid.UUID
    ) -> tuple[int, int, list[str]]:
        """合并 src → dst。

        步骤：
        1. 校验：src/dst 存在 + 同 team + 都未 merged_into
        2. sources.candidate_id → dst
        3. resumes.candidate_id → dst
        4. parsed_structures（resume_id 不变，跟随 resume 自动归属）
        5. 主字段 confidence 比较：若 src 的某 ParsedStructure 字段 confidence
           高于 dst，则更新 dst.candidate 对应字段
        6. src.merged_into = dst

        Returns:
            ``(sources_moved, resumes_moved, fields_updated)``
        """
        if src_id == dst_id:
            raise ValidationError("src_id 不能等于 dst_id")

        src = await self._db.get(Candidate, src_id)
        dst = await self._db.get(Candidate, dst_id)
        if src is None:
            raise NotFoundError(
                f"src candidate {src_id} 不存在", resource="candidate"
            )
        if dst is None:
            raise NotFoundError(
                f"dst candidate {dst_id} 不存在", resource="candidate"
            )
        if src.team_id != dst.team_id:
            raise ValidationError("src 与 dst 必须属于同一 team")
        if src.merged_into is not None:
            raise ValidationError(
                f"src {src_id} 已合并到 {src.merged_into}，不能再次合并"
            )
        if dst.merged_into is not None:
            raise ValidationError(
                f"dst {dst_id} 已合并到 {dst.merged_into}，不能作为合并目标"
            )

        # 1) 在转移 resume 之前先读 ParsedStructure（resume 转走后 src 已无
        # resume，无法再查）
        fields_updated = await self._maybe_update_master_fields(src=src, dst=dst)

        # 2) 转移 sources
        sources_result = await self._db.execute(
            update(CandidateSource)
            .where(CandidateSource.candidate_id == src_id)
            .values(candidate_id=dst_id)
            .returning(CandidateSource.id)
        )
        sources_moved = len(list(sources_result.scalars().all()))

        # 3) 转移 resumes
        resumes_result = await self._db.execute(
            update(CandidateResume)
            .where(CandidateResume.candidate_id == src_id)
            .values(candidate_id=dst_id)
            .returning(CandidateResume.id)
        )
        resumes_moved_ids = list(resumes_result.scalars().all())
        resumes_moved = len(resumes_moved_ids)

        # 4) 标记 src 已合并
        src.merged_into = dst_id

        logger.info(
            "candidates_merged",
            src_id=str(src_id),
            dst_id=str(dst_id),
            sources_moved=sources_moved,
            resumes_moved=resumes_moved,
            fields_updated=fields_updated,
        )

        return sources_moved, resumes_moved, fields_updated

    async def _maybe_update_master_fields(
        self, *, src: Candidate, dst: Candidate
    ) -> list[str]:
        """按 confidence 比较：src 最新 ParsedStructure 的字段若 confidence 高于
        dst 最新 ParsedStructure 的同字段，则更新 dst.candidate 主字段。

        只对 name/phone/email 做（candidates 表主字段）。
        """
        src_ps = await self._latest_structure(src.id)
        dst_ps = await self._latest_structure(dst.id)
        if src_ps is None:
            return []

        src_struct = CandidateStructure.model_validate(
            src_ps.data.get("structure", {})
        )
        dst_struct: CandidateStructure | None = None
        if dst_ps is not None:
            try:
                dst_struct = CandidateStructure.model_validate(
                    dst_ps.data.get("structure", {})
                )
            except Exception:  # noqa: BLE001
                dst_struct = None

        updated: list[str] = []
        # name
        if src_struct.name and (
            dst_struct is None
            or dst_struct.name is None
            or src_struct.name_confidence > dst_struct.name_confidence
        ):
            if dst.name != src_struct.name:
                dst.name = src_struct.name
                updated.append("name")
        # phone
        if src_struct.phone and (
            dst_struct is None
            or dst_struct.phone is None
            or src_struct.phone_confidence > dst_struct.phone_confidence
        ):
            if dst.phone != src_struct.phone:
                dst.phone = src_struct.phone
                updated.append("phone")
        # email
        if src_struct.email and (
            dst_struct is None
            or dst_struct.email is None
            or src_struct.email_confidence > dst_struct.email_confidence
        ):
            if dst.email != src_struct.email:
                dst.email = src_struct.email
                updated.append("email")

        return updated

    async def _latest_structure(
        self, candidate_id: uuid.UUID
    ) -> ParsedStructure | None:
        """获取候选人最新简历的 ParsedStructure（按 uploaded_at 倒序）。"""
        stmt = (
            select(ParsedStructure)
            .join(
                CandidateResume,
                CandidateResume.id == ParsedStructure.resume_id,
            )
            .where(CandidateResume.candidate_id == candidate_id)
            .order_by(CandidateResume.uploaded_at.desc())
            .limit(1)
        )
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none()

    # ----- 列表 / 决议 -----

    async def list_pending_matches(
        self, *, team_id: uuid.UUID, limit: int = 50, offset: int = 0
    ) -> list[DedupMatch]:
        """列出 team 内 pending dedup_matches。"""
        stmt = (
            select(DedupMatch)
            .join(Candidate, Candidate.id == DedupMatch.candidate_a)
            .where(
                Candidate.team_id == team_id,
                DedupMatch.status == "pending",
            )
            .order_by(DedupMatch.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def decide_match(
        self,
        *,
        match_id: uuid.UUID,
        decision: Literal["merged", "rejected"],
        actor_id: uuid.UUID,
    ) -> DedupMatch:
        """HR 决议 dedup_match。

        - ``decision='merged'``：调 ``merge(candidate_b, candidate_a)``
          （小 id 作 src，大 id 作 dst；保持一致），match.status='merged'
        - ``decision='rejected'``：仅置 status='rejected'，不动候选人
        """
        match = await self._db.get(DedupMatch, match_id)
        if match is None:
            raise NotFoundError(
                f"dedup_match {match_id} 不存在", resource="dedup_match"
            )
        if match.status != "pending":
            raise ValidationError(
                f"dedup_match {match_id} 已决议（status={match.status}）"
            )

        if decision == "merged":
            # candidate_a < candidate_b（按 id 字符串），把 b 合并到 a
            await self.merge(src_id=match.candidate_b, dst_id=match.candidate_a)
            match.status = "merged"
        else:
            match.status = "rejected"

        match.decided_by = actor_id
        await self._db.flush()
        return match


__all__ = ["DedupService", "DedupResolution"]
