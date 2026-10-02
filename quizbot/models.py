import enum
from datetime import datetime, timezone

from sqlalchemy import (JSON, BigInteger, DateTime, Enum, Float, ForeignKey,
                        Integer, String, Text, UniqueConstraint, text)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import expression


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class UserRole(str, enum.Enum):
    student = "student"
    admin = "admin"


class Team(Base):
    __tablename__ = "teams"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    score: Mapped[int] = mapped_column(Integer, default=0)


class User(Base):
    __tablename__ = "users"
    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    full_name: Mapped[str] = mapped_column(String(200))
    username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    team_id: Mapped[int | None] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True
    )
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.student)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Question(Base):
    __tablename__ = "questions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subject: Mapped[str] = mapped_column(String(100), default="عام", index=True)
    qtype: Mapped[str] = mapped_column(String(10), default="mcq")  # mcq | numeric
    question_text: Mapped[str] = mapped_column(Text)
    options: Mapped[list | None] = mapped_column(JSON, nullable=True)
    correct_option: Mapped[int | None] = mapped_column(Integer, nullable=True)
    correct_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    tolerance: Mapped[float] = mapped_column(Float, default=0.0)
    time_limit: Mapped[int] = mapped_column(Integer, default=30)
    # --- أعمدة أُضيفت لاحقاً (الترحيل التلقائي يضيفها للقواعد القديمة) ---
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    image: Mapped[str | None] = mapped_column(String(1000), nullable=True)  # رابط أو file_id
    points: Mapped[int] = mapped_column(Integer, default=10, server_default=text("10"))
    shuffle_options: Mapped[bool] = mapped_column(default=True, server_default=expression.true())


class Round(Base):
    __tablename__ = "rounds"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(20), default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Answer(Base):
    __tablename__ = "answers"
    __table_args__ = (UniqueConstraint("round_id", "question_id", "user_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"))
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"))
    selected_option: Mapped[int | None] = mapped_column(Integer, nullable=True)  # الفهرس الأصلي قبل الخلط
    numeric_value: Mapped[float | None] = mapped_column(Float, nullable=True)


class TeamResult(Base):
    __tablename__ = "team_results"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id", ondelete="CASCADE"))
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"))
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))
    team_answer: Mapped[float | None] = mapped_column(Float, nullable=True)
    answered_count: Mapped[int] = mapped_column(Integer, default=0)
    is_correct: Mapped[bool] = mapped_column(default=False)
    points: Mapped[int] = mapped_column(Integer, default=0)  # الإجمالي شاملاً البونص
    bonus: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    qualified: Mapped[bool] = mapped_column(default=True, server_default=expression.true())


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(String(100))
