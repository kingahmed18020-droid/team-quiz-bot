import re

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import BaseFilter, Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, TelegramObject

from .. import keyboards, services, texts
from .. import settings as cfgmod
from ..config import ADMIN_IDS, MAX_UPLOAD_BYTES
from ..db import SessionMaker
from ..engine import CompetitionEngine
from ..parsers import parse_questions_file


class IsAdmin(BaseFilter):
    async def __call__(self, event: TelegramObject) -> bool:
        user = getattr(event, "from_user", None)
        return bool(user and user.id in ADMIN_IDS)


class AdminStates(StatesGroup):
    new_team_name = State()


router = Router()
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


async def show(event: Message | CallbackQuery, text: str, kb=None) -> None:
    """يعدّل الرسالة إن كان الحدث زراً، أو يرسل رسالة جديدة."""
    if isinstance(event, CallbackQuery):
        try:
            await event.message.edit_text(text, reply_markup=kb)
        except TelegramBadRequest:
            pass
        await event.answer()
    else:
        await event.answer(text, reply_markup=kb)


MENU_TEXT = "🛠 <b>لوحة تحكم المشرف</b>\nاختر من القائمة:"


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    await state.clear()
    await show(message, MENU_TEXT, keyboards.admin_menu())


@router.callback_query(F.data == "adm:menu")
async def cb_menu(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(cb, MENU_TEXT, keyboards.admin_menu())


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("تم الإلغاء.", reply_markup=keyboards.admin_menu())


# ---------------- الفرق ----------------
async def _create_team(message: Message, name: str):
    async with SessionMaker() as s:
        team = await services.create_team(s, name)
    if team is None:
        await message.answer("⚠️ الاسم فارغ أو مستخدم من قبل. جرّب اسماً آخر.")
        return False
    await message.answer(
        f"✅ تم إنشاء الفريق <b>{texts.esc(team.name)}</b>\n"
        f"🔑 كود الانضمام: <code>{team.code}</code>\n\nأرسل هذا الكود لأعضاء الفريق.",
        reply_markup=keyboards.admin_menu(),
    )
    return True


@router.message(Command("newteam"))
async def cmd_newteam(message: Message, state: FSMContext):
    name = (message.text or "").partition(" ")[2]
    if not name.strip():
        await state.set_state(AdminStates.new_team_name)
        await message.answer("اكتب اسم الفريق (أو /cancel):")
        return
    await _create_team(message, name)


@router.callback_query(F.data == "adm:newteam")
async def cb_newteam(cb: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.new_team_name)
    await show(cb, "اكتب اسم الفريق الجديد (أو /cancel):")


@router.message(StateFilter(AdminStates.new_team_name), F.text)
async def got_team_name(message: Message, state: FSMContext):
    if await _create_team(message, message.text):
        await state.clear()


@router.callback_query(F.data == "adm:teams")
async def cb_teams(cb: CallbackQuery):
    async with SessionMaker() as s:
        rows = await services.leaderboard(s)
    if not rows:
        await show(cb, "لا توجد فرق بعد. أنشئ فريقاً أولاً.", keyboards.InlineKeyboardMarkup(inline_keyboard=[keyboards.back_btn()]))
        return
    await show(cb, "👥 <b>الفرق</b> (اضغط على فريق لعرض أعضائه وكوده):", keyboards.teams_kb(rows))


@router.callback_query(F.data.startswith("adm:team:"))
async def cb_team(cb: CallbackQuery):
    team_id = int(cb.data.split(":")[2])
    async with SessionMaker() as s:
        team = await services.get_team(s, team_id)
        members = await services.team_members(s, team_id) if team else []
    if not team:
        await show(cb, "الفريق غير موجود.", keyboards.teams_kb([]))
        return
    lines = [
        f"👥 <b>{texts.esc(team.name)}</b>",
        f"🔑 الكود: <code>{team.code}</code>",
        f"🏅 النقاط: <b>{team.score}</b>",
        f"👤 الأعضاء ({len(members)}):",
    ]
    for m in members:
        uname = f" @{m.username}" if m.username else ""
        lines.append(f"• {texts.esc(m.full_name)}{texts.esc(uname)} — <code>{m.telegram_id}</code>")
    if not members:
        lines.append("— لا يوجد أعضاء بعد —")
    await show(cb, "\n".join(lines), keyboards.team_detail_kb(team_id))


@router.callback_query(F.data.startswith("adm:delteam:"))
async def cb_delteam(cb: CallbackQuery):
    team_id = cb.data.split(":")[2]
    await show(cb, "⚠️ هل تريد حذف هذا الفريق؟ سيُفصل أعضاؤه عنه.", keyboards.confirm_kb(f"adm:delteam_ok:{team_id}", "adm:teams"))


@router.callback_query(F.data.startswith("adm:delteam_ok:"))
async def cb_delteam_ok(cb: CallbackQuery, engine: CompetitionEngine):
    if engine.running:
        await cb.answer("لا يمكن الحذف أثناء المسابقة", show_alert=True)
        return
    async with SessionMaker() as s:
        await services.delete_team(s, int(cb.data.split(":")[2]))
    await show(cb, "🗑 تم حذف الفريق.", keyboards.admin_menu())


# ---------------- بنك الأسئلة ----------------
@router.callback_query(F.data == "adm:qbank")
async def cb_qbank(cb: CallbackQuery):
    async with SessionMaker() as s:
        subjects = await services.subjects_with_counts(s)
    total = sum(n for _, n in subjects)
    body = "\n".join(f"• {texts.esc(sub)}: {n}" for sub, n in subjects) or "— فارغ —"
    await show(
        cb,
        f"📚 <b>بنك الأسئلة</b> ({total} سؤال)\n{body}\n\n"
        "📎 لإضافة أسئلة: أرسل ملف <b>JSON</b> أو <b>HTML</b> هنا.\n"
        "لاستبدال البنك بالكامل اكتب في تعليق الملف كلمة: <code>استبدال</code>\n"
        "🖼 لربط صورة بسؤال: أرسل الصورة وفي تعليقها <code>صورة 12</code> (رقم السؤال من /questions)",
        keyboards.qbank_kb(total > 0),
    )


@router.callback_query(F.data == "adm:clearq")
async def cb_clearq(cb: CallbackQuery):
    await show(cb, "⚠️ سيتم مسح كل الأسئلة نهائياً. متأكد؟", keyboards.confirm_kb("adm:clearq_ok", "adm:qbank"))


@router.callback_query(F.data == "adm:clearq_ok")
async def cb_clearq_ok(cb: CallbackQuery, engine: CompetitionEngine):
    if engine.running:
        await cb.answer("لا يمكن المسح أثناء المسابقة", show_alert=True)
        return
    async with SessionMaker() as s:
        await services.clear_questions(s)
    await show(cb, "🗑 تم مسح بنك الأسئلة.", keyboards.admin_menu())


@router.message(F.document)
async def on_upload(message: Message, bot: Bot, engine: CompetitionEngine):
    doc = message.document
    name = doc.file_name or ""
    if not name.lower().endswith((".json", ".html", ".htm")):
        await message.answer("❌ يرجى رفع ملف بصيغة JSON أو HTML فقط.")
        return
    if doc.file_size and doc.file_size > MAX_UPLOAD_BYTES:
        await message.answer("❌ الملف كبير جداً (الحد الأقصى 2MB).")
        return
    caption = (message.caption or "").lower()
    replace = "استبدال" in caption or "replace" in caption
    if replace and engine.running:
        await message.answer("⚠️ لا يمكن استبدال البنك أثناء المسابقة.")
        return
    try:
        buf = await bot.download(doc)
        questions = parse_questions_file(name, buf.read().decode("utf-8-sig"))
    except UnicodeDecodeError:
        await message.answer("⚠️ الملف يجب أن يكون بترميز UTF-8.")
        return
    except ValueError as e:
        await message.answer(f"⚠️ خطأ في الملف:\n{texts.esc(e)}")
        return
    async with SessionMaker() as s:
        n = await services.save_questions(s, questions, replace=replace)
    mode = "استُبدل البنك بـ" if replace else "أُضيف"
    await message.answer(f"✅ {mode} <b>{n}</b> سؤال بنجاح!", reply_markup=keyboards.admin_menu())


# ---------------- تشغيل المسابقة ----------------
async def _subject_from_key(s, key: str) -> tuple[str | None, bool]:
    """يرجع (المادة أو None للكل، صالح؟)"""
    if key == "all":
        return None, True
    subjects = await services.subjects_with_counts(s)
    idx = int(key)
    if 0 <= idx < len(subjects):
        return subjects[idx][0], True
    return None, False


@router.callback_query(F.data == "adm:run")
async def cb_run(cb: CallbackQuery, engine: CompetitionEngine):
    if engine.running:
        await cb.answer("توجد مسابقة تعمل بالفعل", show_alert=True)
        return
    async with SessionMaker() as s:
        subjects = await services.subjects_with_counts(s)
    if not subjects:
        await show(cb, "⚠️ بنك الأسئلة فارغ. ارفع ملف أسئلة أولاً.", keyboards.admin_menu())
        return
    await show(cb, "▶️ اختر المادة التي ستُبث أسئلتها:", keyboards.subjects_kb(subjects, "adm:runs"))


@router.callback_query(F.data.startswith("adm:runs:"))
async def cb_run_confirm(cb: CallbackQuery):
    key = cb.data.split(":")[2]
    async with SessionMaker() as s:
        subject, ok = await _subject_from_key(s, key)
        ids = await services.question_ids(s, subject) if ok else []
        board = await services.leaderboard(s)
    students = sum(n for _, n in board)
    if not ids:
        await show(cb, "⚠️ لا توجد أسئلة.", keyboards.admin_menu())
        return
    label = texts.esc(subject) if subject else "كل المواد"
    await show(
        cb,
        f"سيتم بث <b>{len(ids)}</b> سؤال ({label}) على <b>{len(board)}</b> فريق ({students} طالب).\nبدء الآن؟",
        keyboards.confirm_kb(f"adm:go:{key}", "adm:run"),
    )


@router.callback_query(F.data.startswith("adm:go:"))
async def cb_go(cb: CallbackQuery, engine: CompetitionEngine):
    key = cb.data.split(":")[2]
    async with SessionMaker() as s:
        subject, ok = await _subject_from_key(s, key)
        ids = await services.question_ids(s, subject) if ok else []
    started, msg = await engine.start(cb.from_user.id, ids)
    if started:
        await show(cb, engine.status_text(note=msg), _control_kb(engine))
    else:
        await show(cb, msg, keyboards.admin_menu())


# ---------------- لوحة التحكم أثناء المسابقة ----------------
def _control_kb(engine: CompetitionEngine):
    return keyboards.control_kb(engine.running, engine.paused, engine.waiting_next)


@router.callback_query(F.data == "adm:ctl")
async def cb_ctl(cb: CallbackQuery, engine: CompetitionEngine):
    await show(cb, engine.status_text(), _control_kb(engine))


@router.callback_query(F.data.startswith("adm:c:"))
async def cb_control(cb: CallbackQuery, engine: CompetitionEngine):
    action = cb.data.split(":")[2]
    actions = {
        "pause": (engine.pause, "⏸ تم الإيقاف المؤقت."),
        "resume": (engine.resume, "▶️ تم الاستئناف."),
        "skip": (engine.skip, "⏭ جارٍ تخطي السؤال…"),
        "next": (engine.next, "⏩ جارٍ إرسال السؤال التالي…"),
        "stop": (engine.stop, "⏹ جارٍ إيقاف المسابقة…"),
    }
    fn, ok_note = actions.get(action, (lambda: False, ""))
    note = ok_note if fn() else "⚠️ لا يمكن تنفيذ هذا الإجراء الآن."
    await show(cb, engine.status_text(note=note), _control_kb(engine))


# ---------------- إعدادات المسابقة ----------------
SETTINGS_TEXT = (
    "⚙️ <b>إعدادات المسابقة</b>\n"
    "تُطبَّق على المسابقة القادمة (المسابقة الجارية تحتفظ بإعداداتها).\n\n"
    "• <b>خلط الخيارات</b>: كل طالب يرى ترتيباً مختلفاً (والأسئلة التي فيها «كل ما سبق» "
    "يمكن استثناؤها بـ <code>\"shuffle\": false</code> في الملف).\n"
    "• <b>بونص السرعة</b>: حتى +50% من نقاط السؤال حسب سرعة إجابة الفريق.\n"
    "• <b>الحد الأدنى للمشاركة</b>: نسبة أعضاء الفريق التي يجب أن تجيب ليُحتسب فوز الفريق."
)


@router.callback_query(F.data == "adm:settings")
async def cb_settings(cb: CallbackQuery):
    async with SessionMaker() as s:
        cfg = await cfgmod.load(s)
    await show(cb, SETTINGS_TEXT, keyboards.settings_kb(cfg))


@router.callback_query(F.data.startswith("adm:set:"))
async def cb_set(cb: CallbackQuery):
    key = cb.data.split(":")[2]
    async with SessionMaker() as s:
        if key == "min_participation":
            await cfgmod.cycle_min(s)
        elif key in cfgmod.DEFAULTS:
            await cfgmod.toggle(s, key)
        cfg = await cfgmod.load(s)
    await show(cb, SETTINGS_TEXT, keyboards.settings_kb(cfg))


# ---------------- قائمة الأسئلة وربط الصور ----------------
@router.message(Command("questions"))
async def cmd_questions(message: Message):
    arg = (message.text or "").partition(" ")[2].strip()
    page = max(int(arg) - 1, 0) if arg.isdigit() else 0
    async with SessionMaker() as s:
        rows, total = await services.list_questions(s, page)
    if not rows:
        await message.answer("لا توجد أسئلة في هذه الصفحة.")
        return
    lines = [f"📚 <b>الأسئلة</b> ({total}) — صفحة {page + 1}"]
    for q in rows:
        kind = "🔢" if q.qtype == "numeric" else "❓"
        img = " 🖼" if q.image else ""
        lines.append(f"<code>{q.id}</code> {kind} {texts.esc(q.subject)} • {q.points}ن{img} — {texts.esc(q.question_text[:40])}")
    if total > (page + 1) * 20:
        lines.append(f"\nالصفحة التالية: /questions {page + 2}")
    await message.answer("\n".join(lines))


@router.message(F.photo)
async def on_photo(message: Message):
    m = re.fullmatch(r"(?:صورة|img|image)\s+(\d+)", (message.caption or "").strip(), re.IGNORECASE)
    if not m:
        await message.answer("لربط صورة بسؤال أرسلها مع التعليق: <code>صورة 12</code> (رقم السؤال من /questions)")
        return
    qid = int(m.group(1))
    async with SessionMaker() as s:
        ok = await services.set_question_image(s, qid, message.photo[-1].file_id)
    await message.answer(f"🖼 تم ربط الصورة بالسؤال {qid}." if ok else f"❌ لا يوجد سؤال برقم {qid}.")


@router.callback_query(F.data == "adm:board")
async def cb_board(cb: CallbackQuery):
    async with SessionMaker() as s:
        board = await services.leaderboard(s)
    await show(cb, texts.leaderboard_text(board), keyboards.admin_menu())


@router.callback_query(F.data == "adm:reset")
async def cb_reset(cb: CallbackQuery):
    await show(cb, "⚠️ سيتم تصفير نقاط كل الفرق ومسح سجل الجولات. متأكد؟", keyboards.confirm_kb("adm:reset_ok"))


@router.callback_query(F.data == "adm:reset_ok")
async def cb_reset_ok(cb: CallbackQuery, engine: CompetitionEngine):
    if engine.running:
        await cb.answer("لا يمكن التصفير أثناء المسابقة", show_alert=True)
        return
    async with SessionMaker() as s:
        await services.reset_all(s)
    await show(cb, "🔄 تم تصفير كل النقاط.", keyboards.admin_menu())
