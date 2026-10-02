import asyncio
import os
import sqlite3
import tempfile
from types import SimpleNamespace

import pytest

_tmp = tempfile.mkdtemp()
_db = f"{_tmp}/test.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_db}"
os.environ["BREAK_SECONDS"] = "0"
os.environ["COUNTDOWN_INTERVAL"] = "1"

# قاعدة "قديمة" بدون الأعمدة الجديدة، لاختبار الترحيل التلقائي
_c = sqlite3.connect(_db)
_c.execute(
    "CREATE TABLE questions (id INTEGER PRIMARY KEY, subject VARCHAR(100), qtype VARCHAR(10), "
    "question_text TEXT, options JSON, correct_option INTEGER, correct_value FLOAT, tolerance FLOAT, time_limit INTEGER)"
)
_c.commit()
_c.close()

from sqlalchemy import delete, text  # noqa: E402

from quizbot import services  # noqa: E402
from quizbot import settings as cfgmod  # noqa: E402
from quizbot.db import SessionMaker, init_db  # noqa: E402
from quizbot.engine import CompetitionEngine  # noqa: E402
from quizbot.handlers.student import parse_number  # noqa: E402
from quizbot.models import Answer, Question, Round, Team, TeamResult, User  # noqa: E402
from quizbot.parsers import parse_html_questions, parse_json_questions  # noqa: E402
from quizbot.scoring import (is_qualified, mcq_team_answer, numeric_is_correct,  # noqa: E402
                             numeric_team_answer, speed_bonus)

_loop = asyncio.new_event_loop()


def run(coro):
    return _loop.run_until_complete(coro)


# ---------- scoring ----------
def test_mcq_majority_and_tie():
    assert mcq_team_answer([(1, 1.0), (1, 2.0), (0, 3.0)]) == 1
    assert mcq_team_answer([]) is None
    assert mcq_team_answer([(2, 5.0), (0, 1.0), (2, 6.0), (0, 7.0)]) == 0


def test_numeric():
    assert numeric_team_answer([9, 10, 11]) == 10
    assert numeric_is_correct(9.7, 9.8, 0.3)
    assert not numeric_is_correct(10.5, 9.8, 0.3)


def test_participation_and_bonus():
    assert is_qualified(2, 4, 50) and not is_qualified(1, 4, 50)
    assert is_qualified(1, 4, 0) and not is_qualified(0, 4, 0)
    assert speed_bonus(10, 0, 30, 0.5) == 5
    assert speed_bonus(10, 30, 30, 0.5) == 0
    assert speed_bonus(10, 15, 30, 0.5) in (2, 3)


def test_parse_number():
    assert parse_number("٩٫٨") == 9.8
    assert parse_number("1,5") == 1.5
    assert parse_number("abc") is None and parse_number("nan") is None


# ---------- parsers ----------
def test_json_new_fields():
    qs = parse_json_questions(open("samples/questions.json", encoding="utf-8").read())
    assert len(qs) == 4
    assert qs[0]["points"] == 5 and qs[0]["explanation"]
    assert qs[1]["points"] == 15 and qs[1]["shuffle_options"] is False
    assert qs[2]["qtype"] == "numeric" and qs[2]["points"] == 20
    assert qs[3]["image"].startswith("https://")


def test_json_errors():
    bad = [
        '[{"question":"x","options":["a","b"],"correct_option":5}]',
        '[{"question":"x","options":["a","b"],"correct_option":0,"image":"file.png"}]',
        '[{"question":"x","options":["a","b"],"correct_option":0,"difficulty":"insane"}]',
        '[{"question":"x","options":["a","b"],"correct_option":0,"points":0}]',
        "not json",
    ]
    for b in bad:
        with pytest.raises(ValueError):
            parse_json_questions(b)


def test_numeric_rejects_non_finite_values():
    for value in ("NaN", "Infinity", "-Infinity"):
        content = (
            '[{"type":"numeric","question":"x",'
            f'"correct_value":"{value}","tolerance":0}}]'
        )
        with pytest.raises(ValueError):
            parse_json_questions(content)

    for tolerance in ("NaN", "Infinity"):
        content = (
            '[{"type":"numeric","question":"x",'
            f'"correct_value":1,"tolerance":"{tolerance}"}}]'
        )
        with pytest.raises(ValueError):
            parse_json_questions(content)


def test_html():
    qs = parse_html_questions(open("samples/questions.html", encoding="utf-8").read())
    assert len(qs) == 2
    assert qs[0]["explanation"] and qs[0]["points"] == 15 and qs[0]["image"]
    assert qs[1]["correct_value"] == 144 and qs[1]["points"] == 10


# ---------- ترحيل القاعدة ----------
def test_migration_adds_columns():
    added = run(init_db())
    assert "questions.explanation" in added and "questions.points" in added
    con = sqlite3.connect(_db)
    cols = {r[1] for r in con.execute("PRAGMA table_info(questions)")}
    con.close()
    assert {"explanation", "image", "points", "shuffle_options"} <= cols
    assert run(init_db()) == []  # idempotent


# ---------- المحرك ----------
class FakeBot:
    def __init__(self):
        self.sent, self.edits, self.photos = [], [], []
        self._mid = 0

    async def send_message(self, chat_id, text, reply_markup=None):
        self._mid += 1
        self.sent.append((chat_id, text))
        return SimpleNamespace(message_id=self._mid)

    async def send_photo(self, chat_id, photo, caption=None, reply_markup=None):
        self._mid += 1
        self.photos.append((chat_id, photo))
        self.sent.append((chat_id, caption))
        return SimpleNamespace(message_id=self._mid)

    async def edit_message_text(self, **kw):
        self.edits.append(kw)
        return True

    async def edit_message_caption(self, **kw):
        self.edits.append(kw)
        return True

    async def edit_message_reply_markup(self, **kw):
        return True


async def clean(**settings):
    async with SessionMaker() as s:
        for m in (TeamResult, Answer, Round, Question, User, Team):
            await s.execute(delete(m))
        await s.execute(text("DELETE FROM settings"))
        await s.commit()
        base = {"speed_bonus": "0", "shuffle_options": "0", **settings}
        for k, v in base.items():
            await cfgmod.set_value(s, k, v)


async def make_teams(layout: dict[str, list[int]]):
    async with SessionMaker() as s:
        teams = {}
        for name, uids in layout.items():
            t = await services.create_team(s, name)
            teams[name] = t
            for uid in uids:
                s.add(User(telegram_id=uid, full_name=f"u{uid}", team_id=t.id))
        await s.commit()
    return teams


async def add_questions(*qs):
    async with SessionMaker() as s:
        for q in qs:
            s.add(Question(**q))
        await s.commit()
        return await services.question_ids(s)


async def until(cond, timeout=10.0):
    end = _loop.time() + timeout
    while _loop.time() < end:
        if cond():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("timeout waiting for condition")


async def scores():
    async with SessionMaker() as s:
        return {t.name: t.score for t, _ in await services.leaderboard(s)}


MCQ = dict(subject="x", qtype="mcq", question_text="q1", options=["a", "b", "c"], correct_option=1, time_limit=2, points=10,
           explanation="لأن ب هي الصحيحة", shuffle_options=True)
NUM = dict(subject="x", qtype="numeric", question_text="q2", correct_value=10.0, tolerance=1.0, time_limit=2, points=20, shuffle_options=True)


def test_full_round_scores_and_explanation():
    async def scenario():
        await clean()
        await make_teams({"الصقور": [1, 2, 3], "النسور": [4, 5]})
        ids = await add_questions(MCQ, NUM)
        bot = FakeBot()
        eng = CompetitionEngine(bot)
        ok, _ = await eng.start(999, ids)
        assert ok
        await until(lambda: eng.active and eng.active.deadline != float("inf"))
        aq = eng.active
        for uid, opt in [(1, 1), (2, 1), (3, 0), (4, 0), (5, 0)]:
            assert eng.submit(uid, opt, aq.round_id, aq.question.id) == "ok"
        assert eng.submit(777, 1) == "not_member"
        await until(lambda: eng.active.question.qtype == "numeric" and eng.active.deadline != float("inf"))
        for uid, v in [(1, 9.5), (2, 10.5), (3, 10.0), (4, 20.0), (5, 22.0)]:
            assert eng.submit(uid, v) == "ok"
        await asyncio.wait_for(eng._task, timeout=15)
        return await scores(), bot.sent

    sc, sent = run(scenario())
    assert sc == {"الصقور": 30, "النسور": 0}  # 10 (mcq) + 20 (numeric)
    texts_ = [t for _, t in sent]
    assert any("لأن ب هي الصحيحة" in t for t in texts_)
    assert any("النتيجة النهائية" in t for t in texts_)


def test_min_participation_blocks_points():
    async def scenario():
        await clean(min_participation="50")
        await make_teams({"A": [1, 2, 3, 4]})
        ids = await add_questions(MCQ)
        eng = CompetitionEngine(FakeBot())
        await eng.start(999, ids)
        await until(lambda: eng.active and eng.active.deadline != float("inf"))
        assert eng.submit(1, 1) == "ok"  # 1 من 4 = 25% < 50%
        await asyncio.wait_for(eng._task, timeout=15)
        return await scores()

    assert run(scenario()) == {"A": 0}


def test_speed_bonus_and_shuffle_mapping():
    async def scenario():
        await clean(speed_bonus="1", shuffle_options="1")
        await make_teams({"A": [1, 2], "B": [3, 4]})
        q = dict(MCQ, time_limit=6, options=["a", "b", "c", "d"], correct_option=2)
        ids = await add_questions(q)
        bot = FakeBot()
        eng = CompetitionEngine(bot)
        await eng.start(999, ids)
        await until(lambda: eng.active and eng.active.deadline != float("inf"))
        aq = eng.active
        # كل طالب يضغط على الموضع الذي يقابل الخيار الصحيح (الأصلي = 2) رغم اختلاف الخلط
        perms = {aq.perms[u].index(2) for u in (1, 2, 3, 4)}
        for u in (1, 2, 3, 4):
            pos = aq.perms[u].index(2)
            assert eng.submit_choice(u, pos, aq.round_id, aq.question.id) == "ok"
        assert aq.answers[1][0] == 2
        await asyncio.wait_for(eng._task, timeout=20)
        return await scores(), bot, perms

    sc, bot, _ = run(scenario())
    assert sc["A"] > 10 and sc["B"] > 10  # أساسي 10 + بونص سرعة
    assert any("بونص سرعة" in t for _, t in bot.sent)
    assert bot.edits  # العدّاد الحي عدّل الرسائل


def test_image_sent_as_photo():
    async def scenario():
        await clean()
        await make_teams({"A": [1]})
        ids = await add_questions(dict(MCQ, image="https://example.com/a.png"))
        bot = FakeBot()
        eng = CompetitionEngine(bot)
        await eng.start(999, ids)
        await asyncio.wait_for(eng._task, timeout=15)
        return bot

    bot = run(scenario())
    assert bot.photos and bot.photos[0][1] == "https://example.com/a.png"


def test_pause_skip_manual_next():
    async def scenario():
        await clean(manual_next="1")
        await make_teams({"A": [1, 2]})
        ids = await add_questions(MCQ, dict(MCQ, question_text="q-second"))
        eng = CompetitionEngine(FakeBot())
        await eng.start(999, ids)
        await until(lambda: eng.active and eng.active.deadline != float("inf"))
        assert eng.pause() and eng.submit(1, 1) == "paused"
        await asyncio.sleep(2.6)  # أطول من وقت السؤال (2ث) — يجب ألا ينتهي أثناء الإيقاف
        assert eng.active.deadline != 0.0
        assert eng.resume() and eng.submit(1, 1) == "ok" and eng.submit(2, 1) == "ok"
        await until(lambda: eng.waiting_next)  # الوضع اليدوي: ينتظر المشرف
        assert eng.next()
        await until(lambda: eng.active.number == 2 and eng.active.deadline != float("inf"))
        assert eng.skip()  # تخطي السؤال الثاني دون احتساب
        await asyncio.wait_for(eng._task, timeout=15)
        return await scores()

    assert run(scenario()) == {"A": 10}
