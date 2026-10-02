"""قراءة ملفات الأسئلة (JSON / HTML) مع تحقق كامل من الصحة."""
import json
import math

from bs4 import BeautifulSoup

from .config import (DEFAULT_TIME_LIMIT, DIFFICULTY_POINTS, MAX_TIME_LIMIT,
                     MIN_TIME_LIMIT, POINTS_PER_QUESTION)

MAX_OPTIONS = 10
_DIFFICULTY = {
    "easy": "easy", "سهل": "easy",
    "medium": "medium", "متوسط": "medium",
    "hard": "hard", "صعب": "hard",
}


def _to_bool(v, default=True) -> bool:
    if v is None or v == "":
        return default
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() not in ("false", "0", "no", "off", "لا")


def _validate(raw: dict, idx: int) -> dict:
    def err(msg: str) -> ValueError:
        return ValueError(f"السؤال رقم {idx}: {msg}")

    if not isinstance(raw, dict):
        raise err("الصيغة غير صحيحة")

    text = str(raw.get("question", "")).strip()
    if not text:
        raise err("نص السؤال فارغ")

    qtype = str(raw.get("type", "mcq")).strip().lower()
    if qtype not in ("mcq", "numeric"):
        raise err("النوع يجب أن يكون mcq أو numeric")

    try:
        time_limit = int(raw.get("time_limit", DEFAULT_TIME_LIMIT))
    except (TypeError, ValueError):
        raise err("time_limit يجب أن يكون رقماً")
    if not MIN_TIME_LIMIT <= time_limit <= MAX_TIME_LIMIT:
        raise err(f"الوقت يجب أن يكون بين {MIN_TIME_LIMIT} و{MAX_TIME_LIMIT} ثانية")

    # النقاط: points صريحة > difficulty > الافتراضي
    if raw.get("points") not in (None, ""):
        try:
            points = int(raw["points"])
        except (TypeError, ValueError):
            raise err("points يجب أن يكون رقماً صحيحاً")
        if not 1 <= points <= 1000:
            raise err("points يجب أن يكون بين 1 و1000")
    elif raw.get("difficulty") not in (None, ""):
        level = _DIFFICULTY.get(str(raw["difficulty"]).strip().lower())
        if level is None:
            raise err("difficulty يجب أن يكون easy/medium/hard (أو سهل/متوسط/صعب)")
        points = DIFFICULTY_POINTS[level]
    else:
        points = POINTS_PER_QUESTION

    image = str(raw.get("image") or "").strip() or None
    if image and not image.lower().startswith(("http://", "https://")):
        raise err("image يجب أن يكون رابطاً يبدأ بـ http:// أو https://")

    explanation = str(raw.get("explanation") or "").strip()[:1500] or None

    base = {
        "subject": str(raw.get("subject") or "عام").strip()[:100],
        "qtype": qtype,
        "question": text,
        "time_limit": time_limit,
        "points": points,
        "image": image,
        "explanation": explanation,
        "shuffle_options": _to_bool(raw.get("shuffle"), True),
        "options": None,
        "correct_option": None,
        "correct_value": None,
        "tolerance": 0.0,
    }

    if qtype == "numeric":
        try:
            base["correct_value"] = float(raw["correct_value"])
            base["tolerance"] = float(raw.get("tolerance", 0))
        except (KeyError, TypeError, ValueError):
            raise err("السؤال الرقمي يحتاج correct_value (رقم) و tolerance اختياري")
        if not math.isfinite(base["correct_value"]):
            raise err("correct_value يجب أن يكون رقماً نهائياً وليس NaN أو Infinity")
        if not math.isfinite(base["tolerance"]):
            raise err("tolerance يجب أن يكون رقماً نهائياً وليس NaN أو Infinity")
        if base["tolerance"] < 0:
            raise err("tolerance لا يمكن أن يكون سالباً")
        return base

    options = [str(o).strip() for o in (raw.get("options") or []) if str(o).strip()]
    if not 2 <= len(options) <= MAX_OPTIONS:
        raise err(f"عدد الخيارات يجب أن يكون بين 2 و{MAX_OPTIONS}")
    try:
        correct = int(raw["correct_option"])
    except (KeyError, TypeError, ValueError):
        raise err("correct_option مفقود أو غير صحيح")
    if not 0 <= correct < len(options):
        raise err("correct_option خارج نطاق الخيارات (يبدأ العد من 0)")
    base["options"] = options
    base["correct_option"] = correct
    return base


def parse_json_questions(content: str) -> list[dict]:
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise ValueError(f"ملف JSON غير صالح: {e}")
    if isinstance(data, dict):
        data = data.get("questions", [])
    if not isinstance(data, list) or not data:
        raise ValueError("الملف لا يحتوي على أي أسئلة")
    return [_validate(q, i) for i, q in enumerate(data, 1)]


def parse_html_questions(content: str) -> list[dict]:
    soup = BeautifulSoup(content, "html.parser")
    items = soup.select(".question-item")
    if not items:
        raise ValueError('لم أجد أي عنصر بالكلاس "question-item" في الملف')
    result = []
    for i, item in enumerate(items, 1):
        q_el = item.select_one(".question")
        exp_el = item.select_one(".explanation")
        img_el = item.select_one("img")
        raw = {
            "subject": item.get("data-subject"),
            "type": item.get("data-type", "mcq"),
            "question": q_el.get_text(strip=True) if q_el else "",
            "time_limit": item.get("data-time", DEFAULT_TIME_LIMIT),
            "options": [li.get_text(strip=True) for li in item.select(".options li")],
            "explanation": exp_el.get_text(strip=True) if exp_el else item.get("data-explanation"),
            "image": img_el.get("src") if img_el else item.get("data-image"),
            "points": item.get("data-points"),
            "difficulty": item.get("data-difficulty"),
            "shuffle": item.get("data-shuffle"),
        }
        for attr, key in (("data-correct", "correct_option"),
                          ("data-correct-value", "correct_value"),
                          ("data-tolerance", "tolerance")):
            if item.get(attr) is not None:
                raw[key] = item.get(attr)
        result.append(_validate(raw, i))
    return result


def parse_questions_file(filename: str, content: str) -> list[dict]:
    name = filename.lower()
    if name.endswith(".json"):
        return parse_json_questions(content)
    if name.endswith((".html", ".htm")):
        return parse_html_questions(content)
    raise ValueError("الصيغة غير مدعومة (JSON أو HTML فقط)")
