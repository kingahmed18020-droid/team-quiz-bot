import math

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message
from aiogram.exceptions import TelegramBadRequest

from .. import keyboards, services, texts
from ..config import ADMIN_IDS
from ..db import SessionMaker
from ..engine import CompetitionEngine

router = Router()

_NUM_TABLE = str.maketrans({
    "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4",
    "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9",
    "٫": ".", ",": ".", "٬": None, " ": None,
})


def parse_number(raw: str) -> float | None:
    try:
        v = float(raw.strip().translate(_NUM_TABLE))
    except ValueError:
        return None
    return v if math.isfinite(v) else None


@router.message(CommandStart())
async def cmd_start(message: Message):
    async with SessionMaker() as s:
        user = await services.upsert_user(s, message.from_user)
        team = await services.get_team(s, user.team_id) if user.team_id else None
    extra = "\n\n🛠 أنت مشرف — افتح لوحة التحكم بالأمر /admin" if message.from_user.id in ADMIN_IDS else ""
    if team:
        await message.answer(f"أهلاً {texts.esc(message.from_user.full_name)} 👋\nأنت في فريق <b>{texts.esc(team.name)}</b> 🎓\nانتظر بدء المسابقة من المشرف.{extra}")
    else:
        await message.answer("مرحباً بك في بوت المسابقات الدراسية! 🎓\nأرسل <b>كود الفريق</b> الخاص بك للانضمام لفريقك." + extra)


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "📖 <b>الأوامر</b>\n"
        "/start — البداية والانضمام لفريق\n"
        "/myteam — بيانات فريقك وترتيبه\n"
        "/leaderboard — ترتيب الفرق\n"
        "/leave — مغادرة فريقك الحالي\n\n"
        "أثناء المسابقة: اضغط على الحرف الذي تراه صحيحاً (ترتيب الخيارات يختلف من طالب لآخر). إجابة الفريق تُحسب بأغلبية الأعضاء، ويلزم أن يشارك عدد كافٍ من الفريق."
    )


@router.message(Command("myteam"))
async def cmd_myteam(message: Message):
    async with SessionMaker() as s:
        user = await services.upsert_user(s, message.from_user)
        if not user.team_id:
            await message.answer("لم تنضم لفريق بعد. أرسل كود الفريق.")
            return
        board = await services.leaderboard(s)
    rank = next((i for i, (t, _) in enumerate(board, 1) if t.id == user.team_id), None)
    team, n = next((t, n) for t, n in board if t.id == user.team_id)
    await message.answer(
        f"👥 <b>{texts.esc(team.name)}</b>\n"
        f"🏅 النقاط: <b>{team.score}</b>\n"
        f"📍 الترتيب: <b>{rank}</b> من {len(board)}\n"
        f"👤 عدد الأعضاء: {n}"
    )


@router.message(Command("leaderboard"))
async def cmd_board(message: Message):
    async with SessionMaker() as s:
        user = await services.upsert_user(s, message.from_user)
        board = await services.leaderboard(s)
    await message.answer(texts.leaderboard_text(board, highlight=user.team_id))


@router.message(Command("leave"))
async def cmd_leave(message: Message, engine: CompetitionEngine):
    if engine.running:
        await message.answer("⚠️ لا يمكن مغادرة الفريق أثناء المسابقة.")
        return
    async with SessionMaker() as s:
        user = await services.upsert_user(s, message.from_user)
        if not user.team_id:
            await message.answer("أنت لست في أي فريق.")
            return
        await services.leave_team(s, user)
    await message.answer("تمت مغادرة الفريق. أرسل كود فريق جديد للانضمام.")


@router.callback_query(F.data.startswith("a:"))
async def on_answer(cb: CallbackQuery, engine: CompetitionEngine):
    try:
        _, rid, qid, pos = cb.data.split(":")
        rid, qid, pos = int(rid), int(qid), int(pos)
    except ValueError:
        await cb.answer()
        return
    status = engine.submit_choice(cb.from_user.id, pos, rid, qid)
    if status == "ok":
        await cb.answer(f"✅ تم تسجيل إجابتك: {texts.LETTERS[pos]}")
        try:
            await cb.message.edit_reply_markup(reply_markup=keyboards.answer_kb(rid, engine.active.question, selected=pos))
        except (TelegramBadRequest, AttributeError):
            pass
    elif status == "paused":
        await cb.answer("⏸ المسابقة متوقفة مؤقتاً — انتظر الاستئناف", show_alert=True)
    elif status == "not_member":
        await cb.answer("أنت لست ضمن فريق مشارك.", show_alert=True)
    else:
        await cb.answer("⏰ انتهى وقت هذا السؤال", show_alert=True)


@router.message(F.text & ~F.text.startswith("/"))
async def on_text(message: Message, engine: CompetitionEngine):
    uid = message.from_user.id
    aq = engine.active
    # 1) إجابة رقمية أثناء سؤال رقمي
    if aq and aq.question.qtype == "numeric" and uid in aq.recipients:
        value = parse_number(message.text)
        if value is None:
            await message.answer("⚠️ أرسل رقماً صحيحاً فقط (مثال: 9.8)")
            return
        status = engine.submit(uid, value, aq.round_id, aq.question.id)
        replies = {
            "ok": f"✅ تم تسجيل إجابتك: {value:g}",
            "paused": "⏸ المسابقة متوقفة مؤقتاً — أعد إرسال إجابتك بعد الاستئناف.",
        }
        await message.answer(replies.get(status, "⏰ انتهى وقت السؤال"))
        return
    # 2) كود الانضمام لفريق
    async with SessionMaker() as s:
        user = await services.upsert_user(s, message.from_user)
        if user.team_id:
            await message.answer("أنت منضم لفريق بالفعل. استخدم /leave لمغادرته أولاً.")
            return
        team = await services.join_team(s, user, message.text)
    if team:
        await message.answer(f"🎉 تم انضمامك لفريق <b>{texts.esc(team.name)}</b>!\nانتظر بدء المسابقة.")
    else:
        await message.answer("❌ الكود غير صحيح. تأكد منه وحاول مرة أخرى.")
