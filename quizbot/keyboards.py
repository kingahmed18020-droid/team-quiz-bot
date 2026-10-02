from aiogram.types import InlineKeyboardButton as Btn
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .models import Question, Team
from .settings import RoundConfig
from .texts import LETTERS


def answer_kb(round_id: int, q: Question, selected: int | None = None) -> InlineKeyboardMarkup:
    """الأزرار حسب الموضع المعروض (pos) وليس الفهرس الأصلي، لأن الخيارات تُخلط لكل طالب."""
    b = InlineKeyboardBuilder()
    for pos in range(len(q.options)):
        label = f"✅ {LETTERS[pos]}" if pos == selected else LETTERS[pos]
        b.button(text=label, callback_data=f"a:{round_id}:{q.id}:{pos}")
    b.adjust(5)
    return b.as_markup()


def admin_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="➕ إنشاء فريق", callback_data="adm:newteam")
    b.button(text="👥 الفرق والأعضاء", callback_data="adm:teams")
    b.button(text="📚 بنك الأسئلة", callback_data="adm:qbank")
    b.button(text="⚙️ إعدادات المسابقة", callback_data="adm:settings")
    b.button(text="▶️ بدء مسابقة", callback_data="adm:run")
    b.button(text="🎛 التحكم في المسابقة", callback_data="adm:ctl")
    b.button(text="🏆 الترتيب", callback_data="adm:board")
    b.button(text="🔄 تصفير النقاط", callback_data="adm:reset")
    b.adjust(2, 2, 2, 2)
    return b.as_markup()


def back_btn() -> list[Btn]:
    return [Btn(text="🔙 القائمة", callback_data="adm:menu")]


def confirm_kb(yes_data: str, no_data: str = "adm:menu") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[Btn(text="✅ تأكيد", callback_data=yes_data), Btn(text="❌ إلغاء", callback_data=no_data)]]
    )


def teams_kb(rows: list[tuple[Team, int]]) -> InlineKeyboardMarkup:
    kb = [[Btn(text=f"{t.name} ({n}) — {t.score}", callback_data=f"adm:team:{t.id}")] for t, n in rows]
    kb.append(back_btn())
    return InlineKeyboardMarkup(inline_keyboard=kb)


def team_detail_kb(team_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [Btn(text="🗑 حذف الفريق", callback_data=f"adm:delteam:{team_id}")],
            [Btn(text="🔙 الفرق", callback_data="adm:teams")],
        ]
    )


def subjects_kb(subjects: list[tuple[str, int]], prefix: str) -> InlineKeyboardMarkup:
    total = sum(n for _, n in subjects)
    kb = [[Btn(text=f"📚 كل الأسئلة ({total})", callback_data=f"{prefix}:all")]]
    for i, (sub, n) in enumerate(subjects):
        kb.append([Btn(text=f"{sub} ({n})", callback_data=f"{prefix}:{i}")])
    kb.append(back_btn())
    return InlineKeyboardMarkup(inline_keyboard=kb)


def qbank_kb(has_questions: bool) -> InlineKeyboardMarkup:
    kb = []
    if has_questions:
        kb.append([Btn(text="🗑 مسح كل الأسئلة", callback_data="adm:clearq")])
    kb.append(back_btn())
    return InlineKeyboardMarkup(inline_keyboard=kb)


def settings_kb(c: RoundConfig) -> InlineKeyboardMarkup:
    def onoff(v: bool) -> str:
        return "✅" if v else "❌"

    rows = [
        [Btn(text=f"🔀 خلط ترتيب الأسئلة {onoff(c.shuffle_questions)}", callback_data="adm:set:shuffle_questions")],
        [Btn(text=f"🔀 خلط الخيارات لكل طالب {onoff(c.shuffle_options)}", callback_data="adm:set:shuffle_options")],
        [Btn(text=f"✋ التقدم يدوياً بين الأسئلة {onoff(c.manual_next)}", callback_data="adm:set:manual_next")],
        [Btn(text=f"⚡ بونص السرعة {onoff(c.speed_bonus)}", callback_data="adm:set:speed_bonus")],
        [Btn(text=f"👥 الحد الأدنى للمشاركة: {c.min_participation}% (اضغط للتغيير)", callback_data="adm:set:min_participation")],
        back_btn(),
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def control_kb(running: bool, paused: bool, waiting_next: bool) -> InlineKeyboardMarkup:
    if not running:
        return InlineKeyboardMarkup(inline_keyboard=[[Btn(text="🔄 تحديث", callback_data="adm:ctl")], back_btn()])
    rows = [
        [
            Btn(text="▶️ استئناف", callback_data="adm:c:resume")
            if paused
            else Btn(text="⏸ إيقاف مؤقت", callback_data="adm:c:pause"),
            Btn(text="⏭ تخطي السؤال", callback_data="adm:c:skip"),
        ]
    ]
    if waiting_next:
        rows.append([Btn(text="⏩ السؤال التالي", callback_data="adm:c:next")])
    rows.append([Btn(text="🔄 تحديث", callback_data="adm:ctl"), Btn(text="⏹ إيقاف نهائي", callback_data="adm:c:stop")])
    rows.append(back_btn())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def next_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[Btn(text="⏩ السؤال التالي", callback_data="adm:c:next")]])
