"""认证服务：注册 / 登录 / refresh / 邀请 / 接受邀请。

业务规则（design.md 任务 5）：
- ``register``：首个用户自动 admin + 创建默认 team；后续用户必须有 invite_token
  才能注册（本任务 5 暂只实现「首位 admin」与「普通 register」两条路径，
  invite 路径由 ``accept_invite`` 单独提供）
- ``authenticate``：CITEXT 大小写不敏感；密码 bcrypt 校验
- ``refresh_token``：refresh token JWT 校验后签发新 access token（不轮换 refresh）
- ``invite_member``：48h 过期，同 team + email + status=pending 唯一（DB 部分索引）
- ``accept_invite``：token 一次性；accept 后置 status=accepted，再次使用拒绝

事务策略：service 接收 session，不自行 commit；由调用方（API 依赖 ``get_db``）控制。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from time import time as _time
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.middleware.error_handler import (
    ConflictError,
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_token,
    hash_password,
    verify_password,
)
from app.models.invite import TeamInvite
from app.models.team import Team
from app.models.user import User

# ============================================================================
# 常量
# ============================================================================

INVITE_EXPIRES_HOURS = 48
DEFAULT_TEAM_NAME = "我的团队"

logger = get_logger(__name__)


# ============================================================================
# 内部工具
# ============================================================================


def _make_token_pair(user: User) -> tuple[str, str]:
    """为 user 签发 access + refresh token，返回 (access, refresh)。"""
    access = create_access_token(
        subject=user.id,
        extra_claims={
            "team_id": str(user.team_id) if user.team_id else None,
            "role": user.role,
            "email": user.email,
        },
    )
    refresh = create_refresh_token(subject=user.id)
    return access, refresh


async def _get_user_by_email(db: AsyncSession, email: str) -> User | None:
    """按 email 查 user（lower 比较：PG CITEXT 与 SQLite 语义对齐）。"""
    result = await db.execute(
        select(User).where(func.lower(User.email) == email.lower().strip())
    )
    return result.scalar_one_or_none()


async def _is_first_user(db: AsyncSession) -> bool:
    """是否尚无任何用户注册（首位用户自动 admin）。"""
    result = await db.execute(select(User.id).limit(1))
    return result.first() is None


# ============================================================================
# 公共服务接口
# ============================================================================


async def register(
    db: AsyncSession,
    *,
    email: str,
    password: str,
    name: str,
) -> User:
    """注册新用户。

    - 首位用户：自动 admin + 创建默认 team
    - 后续用户：role=member，无 team（需通过 invite 加入团队）
    - 邮箱重复：抛 ConflictError

    Returns:
        已创建并 flush 的 User 对象（含 id、team_id）

    Raises:
        ConflictError: 邮箱已注册
    """
    existing = await _get_user_by_email(db, email)
    if existing is not None:
        # 邮箱枚举防护：对外文案不透露"该邮箱已存在"，仅记日志供运维排查。
        # 状态码仍为 409（浏览器客户端据此引导去登录），配合注册端点限流
        # 把批量枚举压到不可实用
        logger.warning(
            "register_conflict_email_taken",
            email_hint=str(email)[:3] + "***",
        )
        raise ConflictError("无法完成注册，该邮箱可能已被使用，请直接登录或联系管理员")

    is_first = await _is_first_user(db)

    team_id: UUID | None = None
    role = "member"
    if is_first:
        # 首位用户：自动 admin + 创建默认 team
        team = Team(name=DEFAULT_TEAM_NAME)
        db.add(team)
        await db.flush()
        team_id = team.id
        role = "admin"

    user = User(
        email=email,
        password_hash=hash_password(password),
        name=name,
        role=role,
        team_id=team_id,
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError as exc:
        # 并发场景下另一个事务抢先注册同 email
        raise ConflictError("邮箱已注册", email=email) from exc

    return user


async def authenticate(
    db: AsyncSession,
    *,
    email: str,
    password: str,
) -> tuple[User, str, str]:
    """邮箱密码登录。

    Returns:
        (user, access_token, refresh_token)

    Raises:
        UnauthorizedError: 邮箱不存在 / 密码错误
    """
    user = await _get_user_by_email(db, email)
    if user is None:
        # 故意与密码错误返回相同错误码，避免账号枚举
        raise UnauthorizedError("邮箱或密码错误")
    if not verify_password(password, user.password_hash):
        raise UnauthorizedError("邮箱或密码错误")

    access, refresh = _make_token_pair(user)
    return user, access, refresh


async def refresh_access_token(
    db: AsyncSession,
    *,
    refresh_token: str,
) -> tuple[User, str, str]:
    """refresh token 轮换：校验旧 token → 吊销旧 jti → 签发全新 (access, refresh)。

    轮换 + denylist 使泄漏的 refresh token 在下一次合法刷新后立即作废
    （重放检测）；旧的长期凭据不再"7 天内持续有效"。

    Returns:
        (user, access_token, new_refresh_token)

    Raises:
        UnauthorizedError: refresh token 无效/过期/类型错误/已吊销/用户不存在
    """
    from app.core.token_denylist import is_revoked, revoke

    try:
        payload = decode_token(refresh_token, expected_type="refresh")
    except Exception as exc:
        raise UnauthorizedError("Refresh token 无效或已过期") from exc

    jti = payload.get("jti")
    if jti and await is_revoked(str(jti)):
        raise UnauthorizedError("Refresh token 已吊销")

    user_id = payload.get("sub")
    if not user_id:
        raise UnauthorizedError("Refresh token 缺少 sub")

    try:
        # 与 core/deps.get_current_user 一致：sub 为 str，需转 UUID 再作主键绑定
        user_pk = UUID(str(user_id))
    except ValueError as exc:
        raise UnauthorizedError("Refresh token 无效或已过期") from exc
    result = await db.execute(select(User).where(User.id == user_pk))
    user = result.scalar_one_or_none()
    if user is None:
        raise UnauthorizedError("用户不存在")

    # 旧 refresh 立即吊销（TTL = 剩余有效期；校验通过后执行，先查后吊防竞态窗口放大）
    exp = payload.get("exp")
    if jti and isinstance(exp, (int, float)):
        remaining = int(float(exp) - _time())
        await revoke(str(jti), remaining)

    access = create_access_token(
        subject=user.id,
        extra_claims={
            "team_id": str(user.team_id) if user.team_id else None,
            "role": user.role,
            "email": user.email,
        },
    )
    new_refresh = create_refresh_token(subject=user.id)
    return user, access, new_refresh


async def invite_member(
    db: AsyncSession,
    *,
    team_id: UUID,
    email: str,
    role: str,
    name: str,
    invited_by: UUID,
) -> TeamInvite:
    """team admin 发起邀请。

    - 同 team + email 已有 pending 邀请：抛 ConflictError（避免 token 泛滥）
    - email 已是本 team 成员：抛 ConflictError
    - token 一次性，48h 过期

    Returns:
        已创建的 TeamInvite（含 invite_token）

    Raises:
        ConflictError: 已有 pending 邀请或已是成员
        NotFoundError: team 不存在（外键保护已部分覆盖，显式校验更友好）
    """
    # 是否已是成员
    existing_user = await _get_user_by_email(db, email)
    if existing_user is not None and existing_user.team_id == team_id:
        raise ConflictError("该用户已是团队成员", email=email)

    # 是否已有 pending 邀请（lower 比较：与 CITEXT 语义在两个方言下保持一致）
    result = await db.execute(
        select(TeamInvite)
        .where(
            TeamInvite.team_id == team_id,
            func.lower(TeamInvite.email) == email.lower().strip(),
            TeamInvite.status == "pending",
        )
        .limit(1)
    )
    if result.scalar_one_or_none() is not None:
        raise ConflictError("该邮箱已有待接受的邀请", email=email)

    invite = TeamInvite(
        team_id=team_id,
        email=email,
        name=name,
        role=role,
        invite_token=generate_token(32),
        status="pending",
        invited_by=invited_by,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=INVITE_EXPIRES_HOURS),
    )
    db.add(invite)
    try:
        await db.flush()
    except IntegrityError as exc:
        # 部分唯一索引并发场景兜底
        raise ConflictError("该邮箱已有待接受的邀请", email=email) from exc

    return invite


async def accept_invite(
    db: AsyncSession,
    *,
    invite_token: str,
    name: str,
    password: str,
) -> tuple[User, str, str]:
    """通过邀请链接注册新用户并加入团队。

    Returns:
        (user, access_token, refresh_token)

    Raises:
        NotFoundError: invite_token 不存在
        UnauthorizedError: 邀请已过期 / 已被使用 / 已被撤销
        ConflictError: 邮箱已注册为其他账户
    """
    result = await db.execute(
        select(TeamInvite).where(TeamInvite.invite_token == invite_token)
    )
    invite = result.scalar_one_or_none()
    if invite is None:
        raise NotFoundError("邀请链接无效", resource="invite")

    if invite.status == "accepted":
        raise UnauthorizedError("该邀请链接已被使用")
    if invite.status == "revoked":
        raise UnauthorizedError("该邀请已被撤销")
    # 双方言兼容：asyncpg(PG) 返回 tz-aware，SQLite/aiosqlite 返回 naive；
    # naive 一律按 UTC 补齐后再比较（写入侧本就存 UTC）
    expires_at = invite.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        raise UnauthorizedError("该邀请已过期")

    # email 已注册为其他账户？允许复用（用户已有账号加入新团队）—— 本任务 5
    # 选择「禁止」路径，避免歧义；后续 team 切换由独立 endpoint 处理
    existing = await _get_user_by_email(db, invite.email)
    if existing is not None:
        raise ConflictError(
            "该邮箱已注册，请直接登录后通过「切换团队」加入",
            email=invite.email,
        )

    user = User(
        email=invite.email,
        password_hash=hash_password(password),
        name=name,
        role=invite.role,
        team_id=invite.team_id,
    )
    db.add(user)
    await db.flush()

    invite.status = "accepted"
    invite.accepted_by = user.id
    invite.accepted_at = datetime.now(timezone.utc)

    access, refresh = _make_token_pair(user)
    return user, access, refresh


async def revoke_invite(
    db: AsyncSession,
    *,
    invite_id: UUID,
    team_id: UUID,
) -> None:
    """撤销待接受邀请（admin 取消）。"""
    result = await db.execute(
        select(TeamInvite).where(
            TeamInvite.id == invite_id,
            TeamInvite.team_id == team_id,
        )
    )
    invite = result.scalar_one_or_none()
    if invite is None:
        raise NotFoundError("邀请不存在", resource="invite", invite_id=str(invite_id))
    if invite.status != "pending":
        raise ValidationError("仅可撤销待接受的邀请", current_status=invite.status)
    invite.status = "revoked"


__all__ = [
    "INVITE_EXPIRES_HOURS",
    "register",
    "authenticate",
    "refresh_access_token",
    "invite_member",
    "accept_invite",
    "revoke_invite",
]
