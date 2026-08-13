"""题库 API 路由：分类 CRUD + 题目 CRUD + 按分类配额凑 100 分组卷。

端点（base: /api/question-bank）：
- GET    /categories                列出团队分类
- POST   /categories                新建分类
- PATCH  /categories/{id}           更新分类
- DELETE /categories/{id}           删除分类（CASCADE 删该分类下题目）
- GET    /categories/{id}/items     列出该分类下题目
- POST   /items                     新建题目
- PATCH  /items/{id}                更新题目
- DELETE /items/{id}                删除题目
- POST   /assemble                  按分类配额凑分（返回选中题 + 缺口）

权限：所有端点要求 team_id 非空；资源跨 team 访问返回 404。
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status

from app.core.deps import CurrentUser, DbSession
from app.core.middleware.error_handler import ForbiddenError
from app.schemas.question_bank import (
    AssembleRequest,
    AssembleResponse,
    CategoryCreate,
    CategoryDeficit,
    CategoryOut,
    CategoryUpdate,
    ItemCreate,
    ItemOut,
    ItemUpdate,
)
from app.services.question_bank import QuestionBankService

router = APIRouter(prefix="/question-bank", tags=["question-bank"])


def _require_team(user) -> UUID:
    if user.team_id is None:
        raise ForbiddenError("当前用户未加入任何团队")
    return UUID(str(user.team_id))


# ============================================================================
# 分类 CRUD
# ============================================================================


@router.get("/categories", response_model=list[CategoryOut])
async def list_categories(user: CurrentUser, db: DbSession) -> list[CategoryOut]:
    team_id = _require_team(user)
    cats = await QuestionBankService(db).list_categories(team_id=team_id)
    return [CategoryOut.model_validate(c) for c in cats]


@router.post("/categories", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
async def create_category(
    user: CurrentUser, db: DbSession, payload: CategoryCreate
) -> CategoryOut:
    team_id = _require_team(user)
    cat = await QuestionBankService(db).create_category(
        team_id=team_id, payload=payload.model_dump()
    )
    return CategoryOut.model_validate(cat)


@router.patch("/categories/{category_id}", response_model=CategoryOut)
async def update_category(
    user: CurrentUser, db: DbSession, category_id: UUID, payload: CategoryUpdate
) -> CategoryOut:
    team_id = _require_team(user)
    cat = await QuestionBankService(db).update_category(
        team_id=team_id,
        category_id=category_id,
        payload=payload.model_dump(exclude_unset=True),
    )
    return CategoryOut.model_validate(cat)


@router.delete("/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(user: CurrentUser, db: DbSession, category_id: UUID) -> None:
    team_id = _require_team(user)
    await QuestionBankService(db).delete_category(team_id=team_id, category_id=category_id)


# ============================================================================
# 题目 CRUD
# ============================================================================


@router.get("/categories/{category_id}/items", response_model=list[ItemOut])
async def list_items_by_category(
    user: CurrentUser, db: DbSession, category_id: UUID
) -> list[ItemOut]:
    team_id = _require_team(user)
    # 校验 category 属本 team（不存在则 404）
    svc = QuestionBankService(db)
    await svc.get_category(team_id=team_id, category_id=category_id)
    items = await svc.list_items(team_id=team_id, category_id=category_id, active_only=False)
    return [ItemOut.model_validate(it) for it in items]


@router.post("/items", response_model=ItemOut, status_code=status.HTTP_201_CREATED)
async def create_item(
    user: CurrentUser, db: DbSession, payload: ItemCreate
) -> ItemOut:
    team_id = _require_team(user)
    item = await QuestionBankService(db).create_item(
        team_id=team_id, payload=payload.model_dump()
    )
    return ItemOut.model_validate(item)


@router.patch("/items/{item_id}", response_model=ItemOut)
async def update_item(
    user: CurrentUser, db: DbSession, item_id: UUID, payload: ItemUpdate
) -> ItemOut:
    team_id = _require_team(user)
    item = await QuestionBankService(db).update_item(
        team_id=team_id,
        item_id=item_id,
        payload=payload.model_dump(exclude_unset=True),
    )
    return ItemOut.model_validate(item)


@router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_item(user: CurrentUser, db: DbSession, item_id: UUID) -> None:
    team_id = _require_team(user)
    await QuestionBankService(db).delete_item(team_id=team_id, item_id=item_id)


# ============================================================================
# 组卷（assemble）
# ============================================================================


@router.post("/assemble", response_model=AssembleResponse)
async def assemble(
    user: CurrentUser, db: DbSession, payload: AssembleRequest
) -> AssembleResponse:
    """按分类配额凑 100 分。返回选中题 + 实际总分 + 各分类缺口。"""
    team_id = _require_team(user)
    items, total, deficits = await QuestionBankService(db).assemble(
        team_id=team_id,
        quotas=payload.quotas,
        tolerance=payload.tolerance,
        exclude_question_ids=payload.exclude_question_ids,
    )
    return AssembleResponse(
        items=[ItemOut.model_validate(it) for it in items],
        actual_total=total,
        target_total=sum(d["target"] for d in deficits) + total
        if deficits
        else total,
        deficits=[CategoryDeficit(**d) for d in deficits],
    )
