"""محرك المسابقة: البث، الخلط، العدّاد الحي، الإيقاف المؤقت/التخطي، واحتساب النقاط."""
import asyncio
import logging
import random
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from sqlalchemy import select, update

from . import keyboards, services, texts
from . import settings as cfgmod
from .config import BREAK_SECONDS, COUNTDOWN_INTERVAL, COUNTDOWN_MAX_USERS, SPEED_BONUS_MAX
from .db import SessionMaker
from .models import Answer, Question, Round, Team, TeamResult, User
from .scoring import (TeamOutcome, is_qualified, mcq_team_answer,
                      numeric_is_correct, numeric_team_answer, speed_bonus)
from .settings import RoundConfig

log = logging.getLogger(__name__)
INF = float("inf")


@dataclass
class ActiveQuestion:
    round_id: int
    question: Question
    number: int
    total: int
    recipients: dict[int, int]  # user_id -> team_id
    cfg: RoundConfig
    deadline: float = INF  # يُضبط بعد اكتمال البث؛ 0 = مُغلق
    started: float = INF
    paused_total: float = 0.0
    message_ids: dict[int, int] = field(default_factory=dict)
    caption_mode: set[int] = field(default_factory=set)  # رسائل صور (تُعدَّل بـ caption)
    perms: dict[int, list[int]] = field(default_factory=dict)  # user -> [pos -> الفهرس الأصلي]
    chosen_pos: dict[int, int] = field(default_factory=dict)  # user -> الموضع المعروض المختار
    answers: dict[int, tuple[float, float]] = field(default_factory=dict)  # user -> (value, elapsed)


class CompetitionEngine:
    def __init__(self, bot: Bot):
        self.bot = bot
        self.active: ActiveQuestion | None = None
        self.waiting_next = False
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._skip = asyncio.Event()
        self._next = asyncio.Event()
        self._paused = False
        self._pause_started = 0.0
        self._admin_id: int | None = None
        self._cfg = RoundConfig()
        self._last_recipients: dict[int, int] = {}
        self._bg: set[asyncio.Task] = set()
        self._send_rate_lock = asyncio.Lock()
        self._next_send_at = 0.0

    # ------------------------------------------------------------ حالة
    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def paused(self) -> bool:
        return self._paused

    def status_text(self, note: str = "") -> str:
        lines = ["🎛 <b>التحكم في المسابقة</b>"]
        if note:
            lines.append(note)
        if not self.running:
            lines.append("لا توجد مسابقة تعمل حالياً.")
            return "\n".join(lines)
        if self._paused:
            state = "⏸ متوقفة مؤقتاً"
        elif self.waiting_next:
            state = "⏳ بانتظار الضغط على «السؤال التالي»"
        else:
            state = "▶️ شغالة"
        lines.append(f"الحالة: {state}")
        aq = self.active
        if aq:
            lines.append(f"السؤال: {aq.number} من {aq.total} — {texts.esc(aq.question.subject)}")
            if aq.deadline == 0.0:
                lines.append("(انتهى — النتيجة أُرسلت)")
            else:
                lines.append(f"أجاب {len(aq.answers)} من {len(aq.recipients)} طالب")
                if aq.deadline != INF and not self._paused:
                    lines.append(f"⏱ المتبقي: {max(0, int(aq.deadline - time.monotonic()))} ثانية")
        return "\n".join(lines)

    # ------------------------------------------------------------ استقبال الإجابات
    def submit(self, user_id: int, value: float, round_id: int | None = None, question_id: int | None = None) -> str:
        """تسجيل إجابة في الذاكرة. يرجع: ok / closed / paused / not_member"""
        aq = self.active
        now = time.monotonic()
        if aq is None or now > aq.deadline:
            return "closed"
        if (round_id is not None and round_id != aq.round_id) or (
            question_id is not None and question_id != aq.question.id
        ):
            return "closed"
        if self._paused:
            return "paused"
        if user_id not in aq.recipients:
            return "not_member"
        elapsed = max(0.0, now - aq.started - aq.paused_total)
        aq.answers[user_id] = (value, elapsed)
        return "ok"

    def submit_choice(self, user_id: int, pos: int, round_id: int, question_id: int) -> str:
        """إجابة اختيار من متعدد: يحوّل الموضع المعروض إلى الفهرس الأصلي (بسبب خلط الخيارات)."""
        aq = self.active
        if aq is None or aq.question.qtype != "mcq":
            return "closed"
        perm = aq.perms.get(user_id)
        if perm is None:
            return "not_member" if user_id not in aq.recipients else "closed"
        if not 0 <= pos < len(perm):
            return "closed"
        status = self.submit(user_id, perm[pos], round_id, question_id)
        if status == "ok":
            aq.chosen_pos[user_id] = pos
        return status

    # ------------------------------------------------------------ تحكم المشرف
    async def start(self, admin_id: int, question_ids: list[int]) -> tuple[bool, str]:
        if self.running:
            return False, "⚠️ توجد مسابقة تعمل بالفعل."
        if not question_ids:
            return False, "⚠️ لا توجد أسئلة في البنك."
        recipients = await self._load_recipients()
        if not recipients:
            return False, "⚠️ لا يوجد طلاب منضمّون لأي فريق."
        async with SessionMaker() as s:
            self._cfg = await cfgmod.load(s)  # لقطة من الإعدادات ثابتة طوال الجولة
            rnd = Round()
            s.add(rnd)
            await s.commit()
        self._admin_id = admin_id
        self._last_recipients = recipients
        for ev in (self._stop, self._skip, self._next):
            ev.clear()
        self._paused = False
        self.waiting_next = False
        ids = list(question_ids)
        self._task = asyncio.create_task(self._run(rnd.id, ids))
        return True, f"🚀 بدأت المسابقة: {len(ids)} سؤال لـ {len(recipients)} طالب."

    def stop(self) -> bool:
        if not self.running:
            return False
        self._stop.set()
        return True

    async def shutdown(self, timeout: float = 15.0) -> None:
        """Stop the round and wait for persistence/final notifications to finish."""
        if not self.running:
            return
        self._stop.set()
        try:
            await asyncio.wait_for(self._task, timeout=timeout)
        except asyncio.TimeoutError:
            log.warning("Competition shutdown timed out")

    def skip(self) -> bool:
        if not self.running:
            return False
        self._skip.set()
        return True

    def next(self) -> bool:
        if not (self.running and self.waiting_next):
            return False
        self._next.set()
        return True

    def pause(self) -> bool:
        if not self.running or self._paused:
            return False
        self._paused = True
        self._pause_started = time.monotonic()
        self._spawn(self._broadcast_users(list(self._last_recipients), "⏸ توقفت المسابقة مؤقتاً بقرار المشرف… انتظروا الاستئناف."))
        return True

    def resume(self) -> bool:
        if not self.running or not self._paused:
            return False
        gap = time.monotonic() - self._pause_started
        aq = self.active
        msg = "▶️ استُؤنفت المسابقة!"
        if aq and aq.deadline not in (INF, 0.0):
            aq.deadline += gap  # الوقت المتوقف لا يُحتسب على الطلاب
            aq.paused_total += gap
            msg += f" الوقت المتبقي: {max(0, int(aq.deadline - time.monotonic()))} ثانية."
        self._paused = False
        self._spawn(self._broadcast_users(list(self._last_recipients), msg))
        return True

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    # ------------------------------------------------------------ الحلقة الرئيسية
    async def _run(self, round_id: int, qids: list[int]) -> None:
        stopped = False
        try:
            async with SessionMaker() as s:
                rows = await s.scalars(select(Question).where(Question.id.in_(qids)))
                by_id = {q.id: q for q in rows}
            questions = [by_id[i] for i in qids if i in by_id]
            if self._cfg.shuffle_questions:
                random.shuffle(questions)
            total = len(questions)

            await self._broadcast_users(
                list(self._last_recipients),
                "🎬 <b>بدأت المسابقة!</b> استعدّوا… السؤال الأول بعد لحظات ⏳",
            )
            await asyncio.sleep(3)

            for n, q in enumerate(questions, 1):
                if self._stop.is_set():
                    stopped = True
                    break
                # Freeze the participant list for the entire round. New joins
                # are eligible starting with the next round, not mid-round.
                aq = ActiveQuestion(round_id, q, n, total, dict(self._last_recipients), self._cfg)
                self.active = aq
                await self._ask(aq)
                outcome = await self._wait_question(aq)
                await self._close_question(aq, outcome)
                if outcome == "stopped":
                    stopped = True
                    break
                if n < total and await self._between(n + 1) == "stopped":
                    stopped = True
                    break
        except Exception:
            log.exception("خطأ أثناء تشغيل المسابقة")
            await self._safe(lambda: self.bot.send_message(self._admin_id, "❌ حدث خطأ داخلي أوقف المسابقة. راجع السجلات."))
            stopped = True
        finally:
            self.active = None
            self._paused = False
            self.waiting_next = False
            await self._finish(round_id, stopped)

    # ------------------------------------------------------------ إرسال السؤال
    async def _ask(self, aq: ActiveQuestion) -> None:
        q = aq.question
        if q.qtype == "mcq":
            for uid in aq.recipients:
                perm = list(range(len(q.options)))
                if aq.cfg.shuffle_options and q.shuffle_options:
                    random.shuffle(perm)  # ترتيب مختلف لكل طالب
                aq.perms[uid] = perm

        async def send(uid: int):
            text = texts.question_text(q, aq.number, aq.total, perm=aq.perms.get(uid))
            kb = keyboards.answer_kb(aq.round_id, q) if q.qtype == "mcq" else None
            if q.image:
                try:
                    if len(text) <= 1024:  # حد التعليق على الصورة
                        msg = await self.bot.send_photo(uid, q.image, caption=text, reply_markup=kb)
                        aq.caption_mode.add(uid)
                        return msg.message_id
                    await self.bot.send_photo(uid, q.image)
                except TelegramBadRequest:
                    text += "\n\n⚠️ تعذّر تحميل صورة السؤال."
            return (await self.bot.send_message(uid, text, reply_markup=kb)).message_id

        aq.message_ids = await self._broadcast(list(aq.recipients), send)
        # يبدأ العدّ بعد اكتمال البث ليحصل الجميع على نفس الوقت تقريباً
        aq.started = time.monotonic()
        aq.paused_total = 0.0
        aq.deadline = aq.started + q.time_limit

    async def _wait_question(self, aq: ActiveQuestion) -> str:
        """ينتظر انتهاء السؤال. يرجع: timeout / skipped / stopped"""
        countdown = None
        if COUNTDOWN_INTERVAL > 0 and len(aq.recipients) <= COUNTDOWN_MAX_USERS:
            countdown = asyncio.create_task(self._countdown(aq))
        try:
            while True:
                if self._stop.is_set():
                    return "stopped"
                if self._skip.is_set():
                    self._skip.clear()
                    return "skipped"
                if not self._paused and time.monotonic() >= aq.deadline:
                    return "timeout"
                await asyncio.sleep(0.2)
        finally:
            if countdown:
                countdown.cancel()
                await asyncio.gather(countdown, return_exceptions=True)

    async def _countdown(self, aq: ActiveQuestion) -> None:
        q = aq.question
        while True:
            await asyncio.sleep(COUNTDOWN_INTERVAL)
            if self._paused:
                continue
            remaining = int(aq.deadline - time.monotonic())
            if remaining < 3:
                return

            async def edit(uid: int, remaining=remaining):
                text = texts.question_text(q, aq.number, aq.total, perm=aq.perms.get(uid), remaining=remaining)
                kb = keyboards.answer_kb(aq.round_id, q, aq.chosen_pos.get(uid)) if q.qtype == "mcq" else None
                mid = aq.message_ids[uid]
                if uid in aq.caption_mode:
                    await self.bot.edit_message_caption(chat_id=uid, message_id=mid, caption=text, reply_markup=kb)
                else:
                    await self.bot.edit_message_text(text=text, chat_id=uid, message_id=mid, reply_markup=kb)
                return True

            await self._broadcast(list(aq.message_ids), edit)

    async def _between(self, next_number: int) -> str:
        """الفاصل بين الأسئلة: مؤقت عادي أو انتظار ضغط المشرف (الوضع اليدوي)."""
        if self._cfg.manual_next:
            self._next.clear()
            self.waiting_next = True
            try:
                await self._safe(
                    lambda: self.bot.send_message(
                        self._admin_id, f"⏸ بانتظارك للانتقال إلى السؤال {next_number}.", reply_markup=keyboards.next_kb()
                    )
                )
                while True:
                    if self._stop.is_set():
                        return "stopped"
                    if self._next.is_set() or self._skip.is_set():
                        self._next.clear()
                        self._skip.clear()
                        return "ok"
                    await asyncio.sleep(0.2)
            finally:
                self.waiting_next = False
        end = time.monotonic() + BREAK_SECONDS
        while True:
            if self._stop.is_set():
                return "stopped"
            if self._skip.is_set():
                self._skip.clear()
                return "ok"
            if self._paused:
                end += 0.2
            elif time.monotonic() >= end:
                return "ok"
            await asyncio.sleep(0.2)

    # ------------------------------------------------------------ إنهاء السؤال
    async def _close_question(self, aq: ActiveQuestion, outcome: str) -> None:
        aq.deadline = 0.0  # لا مزيد من الإجابات
        q = aq.question
        if q.qtype == "mcq" and aq.message_ids:
            await self._broadcast(
                list(aq.message_ids),
                lambda uid: self.bot.edit_message_reply_markup(
                    chat_id=uid, message_id=aq.message_ids[uid], reply_markup=None
                ),
            )
        if outcome == "skipped":
            await self._broadcast_users(list(aq.recipients), f"⏭ تخطّى المشرف السؤال {aq.number} — لن يُحتسب.")
            return
        if outcome == "stopped":
            await self._broadcast_users(list(aq.recipients), "⏹ تم إيقاف المسابقة من قِبل المشرف. لن يُحتسب هذا السؤال.")
            return
        outcomes = self.compute_outcomes(aq)
        await self._persist(aq, outcomes)
        await self._announce(aq, outcomes)

    def compute_outcomes(self, aq: ActiveQuestion) -> dict[int, TeamOutcome]:
        q, cfg = aq.question, aq.cfg
        members: dict[int, list[int]] = defaultdict(list)
        for uid, tid in aq.recipients.items():
            members[tid].append(uid)

        outcomes: dict[int, TeamOutcome] = {}
        for tid, uids in members.items():
            given = [aq.answers[uid] for uid in uids if uid in aq.answers]  # [(value, elapsed)]
            o = TeamOutcome(team_id=tid, total=len(uids), answered=len(given))
            if given:
                if q.qtype == "mcq":
                    votes = [(int(v), t) for v, t in given]
                    o.votes = dict(Counter(v for v, _ in votes))
                    o.answer = mcq_team_answer(votes)
                    o.is_correct = o.answer == q.correct_option
                else:
                    o.answer = numeric_team_answer([v for v, _ in given])
                    o.is_correct = numeric_is_correct(o.answer, q.correct_value, q.tolerance)
            o.qualified = is_qualified(o.answered, o.total, cfg.min_participation)
            if o.is_correct and o.qualified:
                if cfg.speed_bonus:
                    avg_t = sum(t for _, t in given) / len(given)
                    o.bonus = speed_bonus(q.points, avg_t, q.time_limit, SPEED_BONUS_MAX)
                o.points = q.points + o.bonus
            outcomes[tid] = o
        return outcomes

    async def _persist(self, aq: ActiveQuestion, outcomes: dict[int, TeamOutcome]) -> None:
        q = aq.question
        async with SessionMaker() as s:
            for uid, (val, _) in aq.answers.items():
                s.add(
                    Answer(
                        round_id=aq.round_id,
                        user_id=uid,
                        team_id=aq.recipients[uid],
                        question_id=q.id,
                        selected_option=int(val) if q.qtype == "mcq" else None,
                        numeric_value=None if q.qtype == "mcq" else val,
                    )
                )
            for tid, o in outcomes.items():
                s.add(
                    TeamResult(
                        round_id=aq.round_id,
                        question_id=q.id,
                        team_id=tid,
                        team_answer=None if o.answer is None else float(o.answer),
                        answered_count=o.answered,
                        is_correct=o.is_correct,
                        points=o.points,
                        bonus=o.bonus,
                        qualified=o.qualified,
                    )
                )
                if o.points:
                    await s.execute(update(Team).where(Team.id == tid).values(score=Team.score + o.points))
            await s.commit()

    async def _announce(self, aq: ActiveQuestion, outcomes: dict[int, TeamOutcome]) -> None:
        async with SessionMaker() as s:
            board = await services.leaderboard(s)
        teams = {t.id: t for t, _ in board}

        by_team: dict[int, list[int]] = defaultdict(list)
        for uid, tid in aq.recipients.items():
            by_team[tid].append(uid)

        jobs = []
        for tid, uids in by_team.items():
            team = teams.get(tid)
            if team is None:
                continue
            text = texts.team_result_text(aq.question, aq.number, aq.total, outcomes[tid], team, aq.cfg.min_participation)
            jobs.extend((uid, text) for uid in uids)

        async def send(job):
            await self.bot.send_message(job[0], job[1])
            return True

        await self._broadcast(jobs, send)

        summary = f"✅ انتهى السؤال {aq.number}/{aq.total}\n\n" + texts.leaderboard_text(board)
        await self._safe(lambda: self.bot.send_message(self._admin_id, summary))

    async def _finish(self, round_id: int, stopped: bool) -> None:
        async with SessionMaker() as s:
            rnd = await s.get(Round, round_id)
            if rnd:
                rnd.status = "stopped" if stopped else "finished"
                rnd.finished_at = datetime.now(timezone.utc)
            await s.commit()
            board = await services.leaderboard(s)
        users = list((await self._load_recipients()).keys())
        title = "🛑 أُوقفت المسابقة — الترتيب الحالي" if stopped else "🏁 انتهت المسابقة — النتيجة النهائية"
        text = texts.leaderboard_text(board, title=title)
        await self._broadcast_users(users, text)
        if self._admin_id and self._admin_id not in users:
            await self._safe(lambda: self.bot.send_message(self._admin_id, text))

    # ------------------------------------------------------------ أدوات
    async def _load_recipients(self) -> dict[int, int]:
        async with SessionMaker() as s:
            rows = (await s.execute(select(User.telegram_id, User.team_id).where(User.team_id.is_not(None)))).all()
        return {uid: tid for uid, tid in rows}

    async def _broadcast_users(self, user_ids: list[int], text: str) -> None:
        async def send(uid):
            await self.bot.send_message(uid, text)
            return True

        await self._broadcast(user_ids, send)

    async def _safe(self, factory):
        for _ in range(3):
            try:
                await self._throttle_send()
                return await factory()
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 0.5)
            except (TelegramForbiddenError, TelegramBadRequest):
                return None  # الطالب حظر البوت أو الرسالة لم تعد قابلة للتعديل
            except Exception:
                log.exception("فشل إرسال/تعديل رسالة")
                return None
        return None

    async def _throttle_send(self) -> None:
        """Keep aggregate Telegram send/edit starts below the global limit."""
        interval = 1.0 / 25.0
        async with self._send_rate_lock:
            now = time.monotonic()
            wait = max(0.0, self._next_send_at - now)
            self._next_send_at = max(now, self._next_send_at) + interval
        if wait:
            await asyncio.sleep(wait)

    async def _broadcast(self, items, fn) -> dict:
        """تنفيذ fn(item) لكل عنصر بسرعة آمنة (~25 رسالة/ثانية، حدّ تليجرام 30).
        يرجع {المفتاح: النتيجة} للعناصر الناجحة فقط."""
        sem = asyncio.Semaphore(12)
        results: dict = {}

        async def worker(item):
            async with sem:
                res = await self._safe(lambda: fn(item))
                await asyncio.sleep(0.04)
                if res is not None:
                    results[item if not isinstance(item, tuple) else item[0]] = res

        await asyncio.gather(*(worker(i) for i in items))
        return results
