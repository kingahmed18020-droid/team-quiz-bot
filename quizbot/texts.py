"""نصوص الرسائل وتنسيقها (HTML)."""
import html

from .models import Question, Team
from .scoring import TeamOutcome

LETTERS = ["أ", "ب", "ج", "د", "هـ", "و", "ز", "ح", "ط", "ي"]
MEDALS = ["🥇", "🥈", "🥉"]


def esc(s) -> str:
    return html.escape(str(s))


def fmt_num(x: float) -> str:
    return f"{x:g}"


def opt_text(q: Question, idx: int) -> str:
    return esc(q.options[idx])


def correct_answer_text(q: Question) -> str:
    if q.qtype == "mcq":
        return opt_text(q, q.correct_option)
    tol = f" (± {fmt_num(q.tolerance)})" if q.tolerance else ""
    return f"{fmt_num(q.correct_value)}{tol}"


def question_text(q: Question, n: int, total: int, perm: list[int] | None = None, remaining: int | None = None) -> str:
    """perm[pos] = الفهرس الأصلي للخيار المعروض في الموضع pos (لخلط الخيارات لكل طالب)."""
    time_line = (
        f"⏳ المتبقي: <b>{remaining}</b> ثانية" if remaining is not None else f"⏱ الوقت: <b>{q.time_limit}</b> ثانية"
    )
    head = (
        f"❓ <b>السؤال {n} من {total}</b> • 📚 {esc(q.subject)} • 💎 {q.points} نقطة\n"
        f"{time_line}\n\n{esc(q.question_text)}"
    )
    if q.qtype == "mcq":
        order = perm if perm is not None else list(range(len(q.options)))
        opts = "\n".join(f"{LETTERS[pos]}) {esc(q.options[orig])}" for pos, orig in enumerate(order))
        return f"{head}\n\n{opts}\n\n👇 اختر إجابتك (يمكنك تغييرها قبل انتهاء الوقت)"
    return f"{head}\n\n🔢 أرسل إجابتك <b>كرقم</b> في رسالة (يمكنك تغييرها قبل انتهاء الوقت)"


def leaderboard_text(rows: list[tuple[Team, int]], highlight: int | None = None, title: str = "🏆 ترتيب الفرق") -> str:
    if not rows:
        return "لا توجد فرق بعد."
    lines = [f"<b>{title}</b>", ""]
    for i, (t, n) in enumerate(rows):
        medal = MEDALS[i] if i < 3 else f"{i + 1}."
        mark = " ⬅️" if t.id == highlight else ""
        lines.append(f"{medal} {esc(t.name)} — <b>{t.score}</b> نقطة ({n} عضو){mark}")
    return "\n".join(lines)


def team_result_text(q: Question, n: int, total: int, o: TeamOutcome, team: Team, min_pct: int) -> str:
    lines = [f"📊 <b>نتيجة السؤال {n} من {total}</b>", f"✔️ الإجابة الصحيحة: <b>{correct_answer_text(q)}</b>"]
    if q.explanation:
        lines.append(f"💡 {esc(q.explanation)}")
    lines.append("")
    if o.answered == 0:
        lines.append("😴 لم يُجب أي عضو من فريقك.")
    else:
        if q.qtype == "mcq":
            votes = " • ".join(f"{opt_text(q, i)}: {c}" for i, c in sorted(o.votes.items()))
            lines.append(f"🗳 الأصوات: {votes}")
            lines.append(f"إجابة الفريق (بالأغلبية): {opt_text(q, o.answer)}")
        else:
            lines.append(f"إجابة الفريق (المتوسط): <b>{o.answer:.4g}</b>")
        lines.append(f"👥 شارك {o.answered} من {o.total}")
        if not o.qualified:
            lines.append(f"⚠️ لم يبلغ الفريق الحد الأدنى للمشاركة ({min_pct}%) — لم تُحتسب نقاط.")
        elif o.is_correct:
            extra = f" (منها ⚡ بونص سرعة +{o.bonus})" if o.bonus else ""
            lines.append(f"✅ إجابة صحيحة! +{o.points} نقطة{extra}")
        else:
            lines.append("❌ إجابة خاطئة")
    lines.append(f"\n🏅 مجموع نقاط {esc(team.name)}: <b>{team.score}</b>")
    return "\n".join(lines)
