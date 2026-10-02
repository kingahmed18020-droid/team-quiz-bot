import os

from dotenv import load_dotenv

load_dotenv()


def _parse_ids(raw: str) -> frozenset[int]:
    return frozenset(int(x) for x in raw.replace(" ", "").split(",") if x)


BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = _parse_ids(os.getenv("ADMIN_IDS", ""))

# Railway containers have a writable /tmp directory but may retain an old
# DATABASE_URL such as sqlite+aiosqlite:///quiz.db in their variables.
# Normalize relative SQLite URLs so the app can start without PostgreSQL.
_database_url = os.getenv("DATABASE_URL", "").strip()
if not _database_url:
    DATABASE_URL = "sqlite+aiosqlite:////tmp/quiz.db"
elif _database_url.startswith("sqlite+aiosqlite:///") and not _database_url.startswith("sqlite+aiosqlite:////"):
    DATABASE_URL = "sqlite+aiosqlite:////tmp/quiz.db"
else:
    DATABASE_URL = _database_url

# النقاط الافتراضية للسؤال (إن لم يحدد الملف points أو difficulty)
POINTS_PER_QUESTION = int(os.getenv("POINTS_PER_QUESTION", "10"))
# نقاط مستويات الصعوبة
DIFFICULTY_POINTS = {"easy": 5, "medium": 10, "hard": 15}
# أقصى بونص للسرعة كنسبة من نقاط السؤال (0.5 = +50%)
SPEED_BONUS_MAX = float(os.getenv("SPEED_BONUS_MAX", "0.5"))

BREAK_SECONDS = int(os.getenv("BREAK_SECONDS", "5"))
# العدّاد التنازلي الحي: كل كم ثانية يُحدَّث؟ (0 = معطّل)
COUNTDOWN_INTERVAL = int(os.getenv("COUNTDOWN_INTERVAL", "10"))
# يتعطّل تلقائياً لو عدد الطلاب أكبر من هذا (حماية من حدود تليجرام)
COUNTDOWN_MAX_USERS = int(os.getenv("COUNTDOWN_MAX_USERS", "150"))

DEFAULT_TIME_LIMIT = 30
MIN_TIME_LIMIT = 5
MAX_TIME_LIMIT = 600
MAX_UPLOAD_BYTES = 2 * 1024 * 1024
