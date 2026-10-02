FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    DATABASE_URL=sqlite+aiosqlite:////data/quiz.db
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY quizbot ./quizbot
COPY samples ./samples

# مستخدم غير جذري + مجلدات قابلة للكتابة (لدعم SQLite داخل الحاوية)
# chown لـ /app مهم إذا كانت منصة النشر تعيد تعريف DATABASE_URL إلى quiz.db نسبي.
RUN useradd -m bot && mkdir /data && chown -R bot:bot /app /data
USER bot

VOLUME ["/data"]

CMD ["python", "-m", "quizbot"]
