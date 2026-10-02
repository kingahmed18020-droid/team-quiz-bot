"""منطق احتساب نتيجة الفريق (دوال نقية قابلة للاختبار)."""
from collections import Counter
from dataclasses import dataclass, field


def mcq_team_answer(votes: list[tuple[int, float]]) -> int | None:
    """votes = [(option, elapsed_seconds)] — الإجابة الأكثر تكراراً (Mode).
    عند التعادل: يفوز الخيار الذي سبق إليه أول صوت."""
    if not votes:
        return None
    counts = Counter(o for o, _ in votes)
    top = max(counts.values())
    tied = [o for o, c in counts.items() if c == top]
    if len(tied) == 1:
        return tied[0]
    first: dict[int, float] = {}
    for o, ts in votes:
        first[o] = min(ts, first.get(o, ts))
    return min(tied, key=lambda o: first[o])


def numeric_team_answer(values: list[float]) -> float | None:
    """المتوسط الحسابي لإجابات الفريق."""
    return sum(values) / len(values) if values else None


def numeric_is_correct(avg: float, correct: float, tolerance: float) -> bool:
    return abs(avg - correct) <= tolerance + 1e-9


def is_qualified(answered: int, total: int, min_pct: int) -> bool:
    """هل بلغت مشاركة الفريق الحد الأدنى؟ (يلزم عضو واحد على الأقل دائماً)"""
    return total > 0 and answered > 0 and answered * 100 >= min_pct * total


def speed_bonus(points: int, avg_elapsed: float, time_limit: int, max_frac: float) -> int:
    """بونص يتناسب عكسياً مع متوسط زمن إجابة الفريق: فوري = max_frac × النقاط، وعند انتهاء الوقت = 0."""
    ratio = max(0.0, min(1.0, 1 - avg_elapsed / time_limit))
    return round(points * max_frac * ratio)


@dataclass
class TeamOutcome:
    team_id: int
    total: int  # عدد أعضاء الفريق
    answered: int = 0
    answer: float | int | None = None
    is_correct: bool = False
    qualified: bool = True
    bonus: int = 0
    points: int = 0  # الإجمالي (الأساسي + البونص)
    votes: dict[int, int] = field(default_factory=dict)
