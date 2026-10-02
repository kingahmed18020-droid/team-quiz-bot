"""إعدادات المسابقة التي يتحكم بها الأدمن (تُحفظ في قاعدة البيانات)."""
from dataclasses import dataclass

from sqlalchemy import select

from .models import Setting

DEFAULTS = {
    "shuffle_questions": "0",
    "shuffle_options": "1",
    "manual_next": "0",
    "min_participation": "50",
    "speed_bonus": "1",
}
MIN_STEPS = [0, 25, 50, 75, 100]


@dataclass(frozen=True)
class RoundConfig:
    shuffle_questions: bool = False
    shuffle_options: bool = True
    manual_next: bool = False
    min_participation: int = 50  # نسبة مئوية من أعضاء الفريق
    speed_bonus: bool = True


async def get_all(s) -> dict[str, str]:
    rows = await s.scalars(select(Setting))
    return {**DEFAULTS, **{r.key: r.value for r in rows}}


def to_config(d: dict[str, str]) -> RoundConfig:
    return RoundConfig(
        shuffle_questions=d["shuffle_questions"] == "1",
        shuffle_options=d["shuffle_options"] == "1",
        manual_next=d["manual_next"] == "1",
        min_participation=int(d["min_participation"]),
        speed_bonus=d["speed_bonus"] == "1",
    )


async def load(s) -> RoundConfig:
    return to_config(await get_all(s))


async def set_value(s, key: str, value: str) -> None:
    obj = await s.get(Setting, key)
    if obj:
        obj.value = value
    else:
        s.add(Setting(key=key, value=value))
    await s.commit()


async def toggle(s, key: str) -> None:
    cur = (await get_all(s))[key]
    await set_value(s, key, "0" if cur == "1" else "1")


async def cycle_min(s) -> None:
    cur = int((await get_all(s))["min_participation"])
    nxt = MIN_STEPS[(MIN_STEPS.index(cur) + 1) % len(MIN_STEPS)] if cur in MIN_STEPS else 50
    await set_value(s, "min_participation", str(nxt))
