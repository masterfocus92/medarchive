"""Семья, члены семьи и учётные записи.

Доменная модель — OVERVIEW.MD §5. Ключевое разделение: член семьи —
это пациент (у ребёнка учётки может НЕ быть — это норма, а не særcase),
а учётная запись — способность действовать: логиниться, быть автором
записей, администрировать состав семьи.
"""

from datetime import date, datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Family(Base):
    """Корень агрегата — семейное пространство."""

    __tablename__ = "families"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Продуктовое решение владельца (13.07.2026): название обязательно,
    # дефолт «Семья» — семью можно создать, не придумывая имя,
    # но безымянной она не бывает.
    name: Mapped[str] = mapped_column(server_default="Семья")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    members: Mapped[list["FamilyMember"]] = relationship(back_populates="family")


class FamilyMember(Base):
    """Член семьи — всегда пациент; наличие учётки не требуется.

    Состав семьи меняют админы (учётки с is_admin) — сам член семьи
    для существования в схеме ни учётки, ни чего-либо ещё не требует:
    дочь заводится админом как строка в этой таблице.
    """

    __tablename__ = "family_members"
    __table_args__ = (
        # Пол нужен будущему reasoning (нормы зависят от пола) —
        # свободная строка здесь дала бы мусор, которым нельзя пользоваться.
        CheckConstraint("sex IN ('male', 'female')", name="sex_allowed"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id"), index=True)
    full_name: Mapped[str]
    # Возраст на момент записи важен для младенца и будущего reasoning.
    birth_date: Mapped[date]
    sex: Mapped[str]

    family: Mapped[Family] = relationship(back_populates="members")
    # Учётка — строго 0..1: uselist=False поверх unique FK со стороны accounts.
    account: Mapped["Account | None"] = relationship(back_populates="member")


class Account(Base):
    """Учётная запись оператора.

    Авторство записей и админство — свойства учётки, а не члена семьи:
    FK автора и флаг is_admin живут здесь, поэтому «ребёнок без учётки —
    автор» и «админ без учётки» невыразимы в схеме в принципе (ADR-006).
    """

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    # unique: одна учётка на человека — вторая означала бы два «я» одного автора.
    family_member_id: Mapped[int] = mapped_column(ForeignKey("family_members.id"), unique=True)
    email: Mapped[str] = mapped_column(unique=True)
    password_hash: Mapped[str]
    # Админ управляет составом семьи (в POC оба взрослых — админы, ставит seed).
    is_admin: Mapped[bool] = mapped_column(server_default=text("false"))

    member: Mapped[FamilyMember] = relationship(back_populates="account")
