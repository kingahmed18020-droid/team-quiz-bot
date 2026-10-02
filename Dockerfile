FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY quizbot ./quizbot
COPY samples ./samples

# مستخدم غير جذري + مجلد بيانات (لاستخدام SQLite داخل حاوية)
RUN useradd -m bot && mkdir /data && chown bot /data
USER bot

CMD ["python", "-m", "quizbot"]
