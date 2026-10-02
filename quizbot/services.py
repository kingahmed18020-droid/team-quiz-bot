"""عمليات قاعدة البيانات (الفرق، الطلاب، الأسئلة)."""
import secrets

from sqlalchemy import delete, func, select, update

from .config import ADMIN_IDS
from .models import Answer, Question, Round, Team, TeamResult, User, UserRole

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # بدون حروف ملتبسة (0/O, 1/I)


def _gen_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))


# ---------------- المستخدمون ----------------
async def upsert_user(s, tg_user) -> User:
    role = UserRole.admin if tg_user.id in ADMIN_IDS else UserRole.student
    user = await s.get(User, tg_user.id)
    if user is None:
        user = User(
            telegram_id=tg_user.id,
            full_name=tg_user.full_name,
            username=tg_user.username,
            role=role,
        )
        s.add(user)
    else:
        user.full_name = tg_user.full_name
        user.username = tg_user.username
        user.role = role
    await s.commit()
    return user


# ---------------- الفرق ----------------
async def create_team(s, name: str) -> Team | None:
    name = name.strip()[:100]
    if not name:
        return None
    exists = await s.scalar(select(Team.id).where(func.lower(Team.name) == name.lower()))
    if exists:
        return None
    while True:
        code = _gen_code()
        if not await s.scalar(select(Team.id).where(Team.code == code)):
            break
    team = Team(name=name, code=code, score=0)
    s.add(team)
    await s.commit()
    return team


async def join_team(s, user: User, code: str) -> Team | None:
    team = await s.scalar(select(Team).where(Team.code == code.strip().upper()))
    if team is None:
        return None
    user.team_id = team.id
    s.add(user)
    await s.commit()
    return team


async def leave_team(s, user: User) -> None:
    user.team_id = None
    s.add(user)
    await s.commit()


async def get_team(s, team_id: int) -> Team | None:
    return await s.get(Team, team_id)


async def team_members(s, team_id: int) -> list[User]:
    res = await s.scalars(select(User).where(User.team_id == team_id).order_by(User.full_name))
    return list(res)


async def leaderboard(s) -> list[tuple[Team, int]]:
    cnt = (
        select(User.team_id, func.count().label("n"))
        .where(User.team_id.is_not(None))
        .group_by(User.team_id)
        .subquery()
    )
    stmt = (
        select(Team, func.coalesce(cnt.c.n, 0))
        .outerjoin(cnt, cnt.c.team_id == Team.id)
        .order_by(Team.score.desc(), Team.name)
    )
    return [(t, n) for t, n in (await s.execute(stmt)).all()]


async def delete_team(s, team_id: int) -> None:
    await s.execute(update(User).where(User.team_id == team_id).values(team_id=None))
    await s.execute(delete(Team).where(Team.id == team_id))
    await s.commit()


# ---------------- الأسئلة ----------------
async def save_questions(s, items: list[dict], replace: bool = False) -> int:
    if replace:
        await s.execute(delete(Question))
    for it in items:
        s.add(
            Question(
                subject=it["subject"],
                qtype=it["qtype"],
                question_text=it["question"],
                options=it["options"],
                correct_option=it["correct_option"],
                correct_value=it["correct_value"],
                tolerance=it["tolerance"],
                time_limit=it["time_limit"],
                explanation=it["explanation"],
                image=it["image"],
                points=it["points"],
                shuffle_options=it["shuffle_options"],
            )
        )
    await s.commit()
    return len(items)


async def subjects_with_counts(s) -> list[tuple[str, int]]:
    rows = await s.execute(
        select(Question.subject, func.count()).group_by(Question.subject).order_by(Question.subject)
    )
    return [(sub, n) for sub, n in rows.all()]


async def question_ids(s, subject: str | None = None) -> list[int]:
    stmt = select(Question.id).order_by(Question.id)
    if subject:
        stmt = stmt.where(Question.subject == subject)
    return list(await s.scalars(stmt))


async def clear_questions(s) -> None:
    await s.execute(delete(Question))
    await s.commit()


async def reset_all(s) -> None:
    """تصفير النقاط ومسح سجل الجولات."""
    await s.execute(delete(TeamResult))
    await s.execute(delete(Answer))
    await s.execute(delete(Round))
    await s.execute(update(Team).values(score=0))
    await s.commit()


async def list_questions(s, page: int = 0, per: int = 20) -> tuple[list[Question], int]:
    total = await s.scalar(select(func.count()).select_from(Question)) or 0
    rows = await s.scalars(select(Question).order_by(Question.id).offset(page * per).limit(per))
    return list(rows), total


async def set_question_image(s, question_id: int, image: str) -> bool:
    q = await s.get(Question, question_id)
    if q is None:
        return False
    q.image = image
    await s.commit()
    return True
