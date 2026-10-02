import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeChat

from .config import ADMIN_IDS, BOT_TOKEN
from .db import init_db
from .engine import CompetitionEngine
from .handlers import admin, student


async def set_commands(bot: Bot) -> None:
    student_cmds = [
        BotCommand(command="start", description="البداية والانضمام لفريق"),
        BotCommand(command="myteam", description="بيانات فريقي"),
        BotCommand(command="leaderboard", description="ترتيب الفرق"),
        BotCommand(command="leave", description="مغادرة الفريق"),
        BotCommand(command="help", description="مساعدة"),
    ]
    await bot.set_my_commands(student_cmds)
    admin_cmds = student_cmds + [
        BotCommand(command="admin", description="لوحة التحكم"),
        BotCommand(command="newteam", description="إنشاء فريق"),
        BotCommand(command="questions", description="قائمة الأسئلة"),
        BotCommand(command="cancel", description="إلغاء"),
    ]
    for admin_id in ADMIN_IDS:
        try:
            await bot.set_my_commands(admin_cmds, scope=BotCommandScopeChat(chat_id=admin_id))
        except Exception:
            logging.warning("تعذّر ضبط أوامر المشرف %s (ربما لم يبدأ البوت بعد)", admin_id)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not BOT_TOKEN:
        sys.exit("❌ ضع BOT_TOKEN في ملف .env")
    if not ADMIN_IDS:
        logging.warning("ADMIN_IDS فارغ — لن يستطيع أحد استخدام لوحة التحكم!")

    migrated = await init_db()
    if migrated:
        logging.info("ترحيل القاعدة: أُضيفت الأعمدة %s", ", ".join(migrated))
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    engine = CompetitionEngine(bot)
    dp = Dispatcher(storage=MemoryStorage(), engine=engine)
    dp.include_router(admin.router)  # أولاً: حتى تسبق حالات FSM الخاصة بالمشرف
    dp.include_router(student.router)
    await set_commands(bot)
    logging.info("البوت يعمل…")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await engine.shutdown()  # حفظ الجولة وإرسال الإغلاق قبل إغلاق الجلسة
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
