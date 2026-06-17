"""Team 模型：多租户隔离的最小单位。"""
from __future__ import annotations

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin
from sqlalchemy.orm import Mapped


class Team(UUIDPKMixin, CreatedAtMixin, Base):
    """团队（多租户隔离边界）。"""

    __tablename__ = "teams"

    name: Mapped[str]

    def __repr__(self) -> str:
        return f"<Team {self.id} {self.name!r}>"


__all__ = ["Team"]
